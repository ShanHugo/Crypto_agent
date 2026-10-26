"""真單執行（第二階段，DESIGN.md 6.5）：用真實餘額跑主帳戶同一套比例再平衡，每天最多一次。

安全規則（全部在送出任何訂單之前檢查）：
- 只有 .env 同時有 LIVE_TRADING=1 與 LIVE_CONFIRM=I_UNDERSTAND 才會被呼叫（呼叫端檢查）；暫停檔同樣有效。
- 01:30～08:00、MAX 系統狀態 online、帳戶沒有掛單凍結、總權益不超過階段上限、ETH／USDT 價格和上次紀錄差距 ≤ 20%、
  任何一筆金額 ≤ 總權益 60%。任一項不過：這天不下單（blocked），08:30 推 🔴。
- 同一天只做一次：當天已經有訂單紀錄就不再送新單，只對帳。
- 下單用 ioc_limit：買價上限 = 中間價 × (1 + 0.5%)、賣價下限 = 中間價 × (1 − 0.5%)，超過就不成交（不會掛在簿子上）。
- client_oid 先寫進資料庫再送；送出後結果不明時先用 client_oid 查，查不到就標記 unknown 並停止（不盲目重送）。
- 交易所的實際餘額是唯一真相；紀錄存 state/live/live.db，和模擬帳戶分開。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from pathlib import Path

from crypto_robot.agents.strategy.rebalance_multi import MultiParams, needs_rebalance, target_deltas, weights
from crypto_robot.core.models import Market
from crypto_robot.core.num import amount_to_precision
from crypto_robot.core.timeutil import TAIPEI, taipei_date
from crypto_robot.exchange.max.public import MaxApiError

ZERO = Decimal(0)
ONE = Decimal(1)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    date TEXT PRIMARY KEY, started_at INTEGER NOT NULL, finished_at INTEGER, status TEXT NOT NULL, note TEXT NOT NULL,
    equity_before TEXT, equity_after TEXT, weights_before TEXT, weights_after TEXT, prices TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    client_oid TEXT PRIMARY KEY, date TEXT NOT NULL, asset TEXT NOT NULL, market TEXT NOT NULL, side TEXT NOT NULL,
    volume TEXT NOT NULL, price TEXT NOT NULL, status TEXT NOT NULL, exchange_id INTEGER, executed TEXT, avg_price TEXT,
    error TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
"""
FINAL = {'done', 'cancel', 'rejected', 'unknown'}


@dataclass(frozen=True)
class LiveConfig:
    params: MultiParams
    markets: dict[str, str]                      # 資產 → 市場，例如 {'eth': 'ethtwd', 'usdt': 'usdttwd'}
    capital_cap: Decimal                         # 階段上限：總權益超過就不下單（第二階段 6,000 元，留 50% 緩衝）
    taker_fee: Decimal = Decimal('0.0016')
    price_cap: Decimal = Decimal('0.005')        # ioc_limit 價格離中間價最多 0.5%
    max_trade_frac: Decimal = Decimal('0.6')     # 單筆金額上限（占總權益）
    max_price_jump: Decimal = Decimal('0.2')     # 和上次紀錄的價格差距上限
    min_equity: Decimal = Decimal(500)


@dataclass(frozen=True)
class PlannedOrder:
    asset: str
    market: str
    side: str
    volume: Decimal
    price: Decimal          # ioc_limit 價格（買的上限／賣的下限）
    value: Decimal          # 以中間價估的金額


class LiveStore:
    def __init__(self, path: str | Path):
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def run(self, day) -> dict | None:
        cur = self.db.execute('SELECT * FROM runs WHERE date = ?', (day.isoformat(),))
        row = cur.fetchone()
        return None if row is None else dict(zip([c[0] for c in cur.description], row))

    def orders(self, day=None) -> list[dict]:
        sql, args = ('SELECT * FROM orders WHERE date = ? ORDER BY created_at', (day.isoformat(),)) if day else \
            ('SELECT * FROM orders ORDER BY created_at', ())
        cur = self.db.execute(sql, args)
        return [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]

    def runs(self, n: int = 30) -> list[dict]:
        cur = self.db.execute('SELECT * FROM runs ORDER BY date DESC LIMIT ?', (n,))
        return [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()][::-1]

    def save_run(self, day, now_ms: int, status: str, note: str, **fields) -> None:
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO runs (date, started_at, status, note) VALUES (?,?,?,?)',
                            (day.isoformat(), now_ms, status, note))
            sets = {'status': status, 'note': note, 'finished_at': now_ms, **{k: (v if v is None or isinstance(v, str) else json.dumps(v))
                                                                               for k, v in fields.items()}}
            self.db.execute(f'UPDATE runs SET {", ".join(f"{k} = ?" for k in sets)} WHERE date = ?',
                            (*sets.values(), day.isoformat()))

    def add_order(self, oid: str, day, o: PlannedOrder, now_ms: int) -> None:
        with self.db:
            self.db.execute('INSERT INTO orders (client_oid, date, asset, market, side, volume, price, status, created_at, updated_at) '
                            'VALUES (?,?,?,?,?,?,?,?,?,?)',
                            (oid, day.isoformat(), o.asset, o.market, o.side, str(o.volume), str(o.price), 'pending', now_ms, now_ms))

    def update_order(self, oid: str, now_ms: int, **fields) -> None:
        sets = {**fields, 'updated_at': now_ms}
        with self.db:
            self.db.execute(f'UPDATE orders SET {", ".join(f"{k} = ?" for k in sets)} WHERE client_oid = ?',
                            (*[str(v) if isinstance(v, Decimal) else v for v in sets.values()], oid))


def new_client_oid(now_ms: int) -> str:
    """cr2-{台北時間 YYYYMMDDHHMMSS}-{8 碼}，27 字元（上限 36）。"""
    return f'cr2-{datetime.fromtimestamp(now_ms / 1000, TAIPEI):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}'


def limit_price(mid: Decimal, side: str, cap: Decimal, places: int) -> Decimal:
    """買：中間價 × (1 + cap) 無條件捨去（不超過上限）；賣：中間價 × (1 − cap) 無條件進位（不低於下限）。"""
    q = Decimal(1).scaleb(-places)
    return (mid * (ONE + cap)).quantize(q, rounding=ROUND_DOWN) if side == 'buy' else \
        (mid * (ONE - cap)).quantize(q, rounding=ROUND_UP)


def check_guards(holdings: dict, cash: Decimal, locked: Decimal, prices: dict, prev_prices: dict | None,
                 cfg: LiveConfig, online: bool) -> str | None:
    """不能下單的原因；可以就回傳 None。"""
    if not online:
        return 'MAX 系統狀態不是 online'
    if locked > 0:
        return '帳戶有掛單凍結的資金（可能有未完成的訂單）'
    eq = cash + sum(holdings[a] * prices[a] for a in holdings)
    if eq > cfg.capital_cap:
        return f'總權益 {eq:,.0f} 元超過階段上限 {cfg.capital_cap:,.0f} 元（要加碼請先調整上限）'
    if eq < cfg.min_equity:
        return f'總權益 {eq:,.0f} 元太少'
    for a, p in (prev_prices or {}).items():
        if a in prices and p and abs(prices[a] / p - 1) > cfg.max_price_jump:
            return f'{a.upper()} 價格 {prices[a]} 和上次紀錄 {p} 差距超過 {cfg.max_price_jump:.0%}，懷疑資料異常'
    return None


def plan_orders(holdings: dict[str, Decimal], cash: Decimal, prices: dict[str, Decimal], markets: dict[str, Market],
                cfg: LiveConfig) -> tuple[list[PlannedOrder], list[str], str | None]:
    """(訂單（賣在前）, 備註, 擋下原因)。不需要再平衡就回傳空清單。"""
    if not needs_rebalance(holdings, cash, prices, cfg.params):
        return [], [], None
    eq = cash + sum(holdings[a] * prices[a] for a in holdings)
    deltas = target_deltas(holdings, cash, prices, cfg.params)
    orders, notes = [], []
    for a in sorted(deltas, key=lambda x: deltas[x]):
        m, d = markets[a], deltas[a]
        side = 'sell' if d < 0 else 'buy'
        vol = amount_to_precision(min(-d, holdings[a]) if d < 0 else d, m.amount_places)
        price = limit_price(prices[a], side, cfg.price_cap, m.price_places)
        if vol <= 0 or vol < m.min_amount or vol * price < m.min_cost:
            notes.append(f'{a.upper()} 差額低於交易所最低下單量，不調整')
            continue
        value = vol * prices[a]
        if value > cfg.max_trade_frac * eq:
            return [], notes, f'{a.upper()} 這筆 {value:,.0f} 元超過總權益的 {cfg.max_trade_frac:.0%}，懷疑資料或程式異常'
        orders.append(PlannedOrder(a, cfg.markets[a], side, vol, price, value))
    return orders, notes, None


def resize_buy(o: PlannedOrder, cash: Decimal, m: Market, fee: Decimal) -> PlannedOrder | None:
    """賣完之後依實際可用台幣縮小買量；低於最低下單量就不買。"""
    vol = min(o.volume, amount_to_precision(cash / (o.price * (ONE + fee)), m.amount_places))
    if vol < m.min_amount or vol * o.price < m.min_cost:
        return None
    return PlannedOrder(o.asset, o.market, o.side, vol, o.price, vol * o.value / o.volume)


async def _settle(client, store: LiveStore, oid: str, now_ms, poll: float, tries: int) -> dict:
    """查到最終狀態（IOC 通常立即結束）；還在 wait 就取消再查。回傳最後一次查到的訂單。"""
    o: dict = {}
    for i in range(tries * 2):
        if i == tries:
            try:
                await client.cancel(oid)
            except MaxApiError:
                pass
        o = await client.order(oid)
        store.update_order(oid, now_ms(), status=o.get('state'), exchange_id=o.get('id'),
                          executed=str(o.get('executed_volume') or '0'), avg_price=str(o.get('avg_price') or '0'))
        if o.get('state') in ('done', 'cancel'):
            return o
        await asyncio.sleep(poll)
    return o


async def _send(client, store: LiveStore, day, o: PlannedOrder, now_ms, poll: float, tries: int) -> dict:
    oid = new_client_oid(now_ms())
    store.add_order(oid, day, o, now_ms())
    try:
        await client.create_order(o.market, o.side, o.volume, ord_type='ioc_limit', client_oid=oid, price=o.price)
    except MaxApiError as e:
        if e.status < 500 and e.status != 429:
            store.update_order(oid, now_ms(), status='rejected', error=f'HTTP {e.status}: {e.body[:150]}')
            return {'client_oid': oid, 'status': 'rejected', 'error': e.body[:150]}
        # 5xx／429：結果不明，先查
    except (asyncio.TimeoutError, OSError):
        pass
    try:
        final = await _settle(client, store, oid, now_ms, poll, tries)
    except MaxApiError as e:
        store.update_order(oid, now_ms(), status='unknown', error=f'送出後查不到：HTTP {e.status}')
        return {'client_oid': oid, 'status': 'unknown', 'error': f'送出後查不到：HTTP {e.status}'}
    return {'client_oid': oid, 'status': final.get('state'), 'executed': final.get('executed_volume'), 'avg_price': final.get('avg_price')}


async def run_live(client, store: LiveStore, prices: dict[str, Decimal], markets: dict[str, Market], cfg: LiveConfig,
                   now_ms: int, *, online: bool, prev_prices: dict | None, poll: float = 1.0, tries: int = 8,
                   clock=None) -> dict:
    """執行今天的真單。prices：各資產當下盤口中間價。回傳摘要（給報告與紀錄）。"""
    clock = clock or (lambda: int(datetime.now(TAIPEI).timestamp() * 1000))
    day = taipei_date(now_ms)
    done = store.run(day)
    if done and done['status'] in ('done', 'hold', 'blocked', 'reconciled'):
        return {'status': 'already', 'date': day}
    if store.orders(day):                                   # 之前送過單但沒跑完：只對帳，不送新單
        for o in store.orders(day):
            if o['status'] not in FINAL:
                try:
                    await _settle(client, store, o['client_oid'], clock, poll, tries)
                except MaxApiError:
                    store.update_order(o['client_oid'], clock(), status='unknown', error='對帳時查不到')
        store.save_run(day, clock(), 'reconciled', '今天已送過單，只對帳不送新單')
        return {'status': 'reconciled', 'date': day, 'orders': store.orders(day)}

    bal = await client.balances()
    get = lambda c, k: bal.get(c, {}).get(k, ZERO)          # noqa: E731
    assets = [a for a, _ in cfg.params.targets]
    holdings = {a: get(a, 'balance') for a in assets}
    cash = get('twd', 'balance')
    locked = get('twd', 'locked') + sum(get(a, 'locked') for a in assets)
    eq = cash + sum(holdings[a] * prices[a] for a in assets)
    w = weights(holdings, cash, prices)
    common = dict(equity_before=str(eq), weights_before={k: str(v) for k, v in w.items()}, prices={k: str(v) for k, v in prices.items()})
    reason = check_guards(holdings, cash, locked, prices, prev_prices, cfg, online)
    if reason:
        store.save_run(day, clock(), 'blocked', reason, **common)
        return {'status': 'blocked', 'date': day, 'reason': reason, 'equity': eq, 'weights': w}
    orders, notes, reason = plan_orders(holdings, cash, prices, markets, cfg)
    if reason:
        store.save_run(day, clock(), 'blocked', reason, **common)
        return {'status': 'blocked', 'date': day, 'reason': reason, 'equity': eq, 'weights': w}
    if not orders:
        store.save_run(day, clock(), 'hold', '；'.join(notes) or '沒有偏離門檻', equity_after=str(eq), **common)
        return {'status': 'hold', 'date': day, 'equity': eq, 'weights': w, 'notes': notes}

    store.save_run(day, clock(), 'running', '送單中', **common)
    results = []
    for o in [x for x in orders if x.side == 'sell']:
        results.append({**(await _send(client, store, day, o, clock, poll, tries)), 'asset': o.asset, 'side': 'sell'})
    buys = [x for x in orders if x.side == 'buy']
    if buys:
        bal = await client.balances()
        cash = bal.get('twd', {}).get('balance', ZERO)
        for o in buys:
            sized = resize_buy(o, cash, markets[o.asset], cfg.taker_fee)
            if sized is None:
                notes.append(f'{o.asset.upper()} 賣出後台幣不夠最低下單量，沒有買')
                continue
            r = await _send(client, store, day, sized, clock, poll, tries)
            results.append({**r, 'asset': o.asset, 'side': 'buy'})
            spent = Decimal(str(r.get('executed') or '0')) * Decimal(str(r.get('avg_price') or '0')) * (ONE + cfg.taker_fee)
            cash -= spent
    bal = await client.balances()
    after = {a: bal.get(a, {}).get('balance', ZERO) for a in assets}
    cash_after = bal.get('twd', {}).get('balance', ZERO)
    eq_after = cash_after + sum(after[a] * prices[a] for a in assets)
    w_after = weights(after, cash_after, prices)
    bad = [r for r in results if r['status'] in ('rejected', 'unknown')]
    status = 'done'
    note = '；'.join(notes + [f'{r["asset"].upper()} {r["side"]} {r["status"]}：{r.get("error", "")}' for r in bad])
    store.save_run(day, clock(), status, note or '完成', equity_after=str(eq_after),
                   weights_after={k: str(v) for k, v in w_after.items()}, **common)
    return {'status': status, 'date': day, 'equity': eq, 'equity_after': eq_after, 'weights': w, 'weights_after': w_after,
            'results': results, 'notes': notes, 'errors': bad}
