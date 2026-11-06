"""多幣排序的前瞻模擬帳戶，目前跑 blend（DESIGN.md 6.18；tick 規則在 6.16）。不用金鑰、不下單、不碰真錢。

tick 是純函式：帳戶＋報價＋目標 → 模擬成交與事件。連網在 scripts/futures_paper.py。金額一律 Decimal。

每分鐘的順序（就是優先順序）：
1. 已結束或暫停 → 只記錄，不成交。
2. 持有的幣已不在交易 → 用最後標記價出場，成本加倍。
3. 權益 ≤ 起始 × 70% → 全部平倉，本次模擬結束（reference 對照帳戶不套這條）。
4. 空單 +50%、多單 -50% → 平掉該部位，這週不開回來（reference 不套這條）。
5. 有新的目標部位 → 先平要換掉的、先減後加，合計 ≤ 1 倍；買用賣一價、賣用買一價，手續費 0.05%。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

ZERO = Decimal(0)
FEE = Decimal('0.0005')
INITIAL = Decimal(300)
KILL = Decimal('0.7')
STOP = Decimal('0.5')
MIN_ADJUST = Decimal(5)
HOUR = 3_600_000


@dataclass(frozen=True)
class Quote:
    bid: Decimal
    ask: Decimal
    mark: Decimal

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class Rule:
    step: Decimal
    min_notional: Decimal


@dataclass(frozen=True)
class Target:
    week: int
    longs: tuple[str, ...]
    shorts: tuple[str, ...]


@dataclass
class Position:
    qty: Decimal                     # 正＝多、負＝空
    entry: Decimal
    mark: Decimal
    funded_ms: int


@dataclass
class Account:
    name: str
    cash: Decimal = INITIAL
    week: int = 0                    # 已換到哪一週的目標
    ended: bool = False
    positions: dict[str, Position] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({'name': self.name, 'cash': str(self.cash), 'week': self.week, 'ended': self.ended,
                           'positions': {s: {'qty': str(p.qty), 'entry': str(p.entry), 'mark': str(p.mark), 'funded_ms': p.funded_ms}
                                         for s, p in self.positions.items()}})

    @staticmethod
    def from_json(text: str) -> 'Account':
        d = json.loads(text)
        return Account(d['name'], Decimal(d['cash']), d['week'], d['ended'],
                       {s: Position(Decimal(p['qty']), Decimal(p['entry']), Decimal(p['mark']), p['funded_ms']) for s, p in d['positions'].items()})


def equity(a: Account) -> Decimal:
    return a.cash + sum((p.qty * (p.mark - p.entry) for p in a.positions.values()), ZERO)


def gross(a: Account) -> Decimal:
    return sum((abs(p.qty) * p.mark for p in a.positions.values()), ZERO)


def fill(a: Account, sym: str, delta: Decimal, q: Quote, now: int, reason: str, cost_mult: int = 1) -> dict:
    """成交 delta（正買負賣）：買用賣一、賣用買一。加倉算平均成本，減倉實現損益；不能一次翻倉。"""
    price = q.ask if delta > 0 else q.bid
    notional = abs(delta) * price
    fee = notional * FEE * cost_mult
    p = a.positions.get(sym)
    a.cash -= fee
    if p is None:
        a.positions[sym] = Position(delta, price, q.mark, now)
    elif (p.qty > 0) == (delta > 0):
        total = p.qty + delta
        p.entry = (p.qty * p.entry + delta * price) / total
        p.qty = total
    else:
        if abs(delta) > abs(p.qty):
            raise ValueError('不能一次翻倉')
        a.cash += -delta * (price - p.entry)
        p.qty += delta
        if p.qty == 0:
            del a.positions[sym]
    return {'ts': now, 'account': a.name, 'week': a.week, 'sym': sym, 'qty': str(delta), 'price': str(price), 'mid': str(q.mid),
            'fee': str(fee), 'reason': reason}


def close(a: Account, sym: str, q: Quote, now: int, reason: str, cost_mult: int = 1) -> dict:
    return fill(a, sym, -a.positions[sym].qty, q, now, reason, cost_mult)


def apply_funding(a: Account, sym: str, rate: Decimal, mark: Decimal | None, ms: int) -> Decimal:
    """資金費率：正費率多付空收。只算部位建立之後的結算。回傳付出的金額（負數代表收到）。"""
    p = a.positions.get(sym)
    if p is None or ms <= p.funded_ms:
        return ZERO
    paid = p.qty * (mark if mark else p.mark) * rate
    a.cash -= paid
    p.funded_ms = ms
    return paid


def hit_stop(p: Position) -> bool:
    return p.mark >= p.entry * (1 + STOP) if p.qty < 0 else p.mark <= p.entry * (1 - STOP)


def _floor(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(ROUND_DOWN) * step


def rebalance(a: Account, t: Target, quotes: dict[str, Quote], rules: dict[str, Rule], now: int) -> tuple[list[dict], list[dict]]:
    """換到目標：不在名單的全平 → 需要減的先減 → 再加。回傳 (成交, 略過原因)。"""
    trades, skips = [], []
    a.week = t.week
    each = equity(a) / (2 * len(t.longs)) if t.longs else ZERO
    want = {s: 1 for s in t.longs} | {s: -1 for s in t.shorts}
    for s in sorted(set(a.positions) - set(want)):
        if s in quotes:
            trades.append(close(a, s, quotes[s], now, 'rebalance'))
        else:
            skips.append({'sym': s, 'why': 'no_quote_close'})
    adds = []
    for s, side in sorted(want.items()):
        q, r = quotes.get(s), rules.get(s)
        if q is None or r is None:
            skips.append({'sym': s, 'why': 'no_quote'})
            continue
        qty = side * _floor(each / q.mid, r.step)
        p = a.positions.get(s)
        cur = p.qty if p else ZERO
        if cur and (cur > 0) != (side > 0):            # 方向相反：先平
            trades.append(close(a, s, q, now, 'rebalance'))
            cur = ZERO
        if abs(qty) * q.mid < r.min_notional:
            skips.append({'sym': s, 'why': 'below_min'})
            continue
        delta = qty - cur
        if abs(delta) * q.mid < MIN_ADJUST:
            skips.append({'sym': s, 'why': 'small_adjust'})
        elif abs(qty) < abs(cur):
            trades.append(fill(a, s, delta, q, now, 'rebalance'))
        else:
            adds.append((s, side, delta, q, r))
    for s, side, delta, q, r in adds:
        price = q.ask if delta > 0 else q.bid
        room = equity(a) - gross(a)
        unit = q.mark + abs(price - q.mark) + price * FEE       # 成交後部位變大、權益也少掉價差與手續費
        size = min(abs(delta), _floor(room / unit, r.step)) if room > 0 else ZERO
        if size * price < max(r.min_notional, MIN_ADJUST):
            skips.append({'sym': s, 'why': 'gross_cap' if abs(delta) * price >= r.min_notional else 'below_min'})
            continue
        trades.append(fill(a, s, side * size, q, now, 'rebalance'))
    return trades, skips


def tick(a: Account, quotes: dict[str, Quote], rules: dict[str, Rule], target: Target | None, now: int, paused: bool = False,
         protect: bool = True) -> dict:
    """一輪。protect=False 是對照帳戶：沒有緊急停損與帳戶線，只報告。"""
    out: dict = {'trades': [], 'skips': [], 'events': []}
    for s, p in a.positions.items():
        if s in quotes:
            p.mark = quotes[s].mark
    if a.ended or paused:
        return out
    for s in sorted(set(a.positions) - set(rules)):        # 已不在交易的幣
        p = a.positions[s]
        out['trades'].append(close(a, s, Quote(p.mark, p.mark, p.mark), now, 'delisted', cost_mult=2))
        out['events'].append({'kind': 'delisted', 'detail': s})
    if protect and equity(a) <= INITIAL * KILL:
        for s in sorted(a.positions):
            p = a.positions[s]
            out['trades'].append(close(a, s, quotes.get(s) or Quote(p.mark, p.mark, p.mark), now, 'kill'))
        a.ended = True
        out['events'].append({'kind': 'ended', 'detail': f'權益 {equity(a):.2f} ≤ 起始 × 70%，本次模擬結束'})
        return out
    if protect:
        for s in sorted(s for s, p in a.positions.items() if hit_stop(p) and s in quotes):
            out['trades'].append(close(a, s, quotes[s], now, 'stop'))
            out['events'].append({'kind': 'stop', 'detail': s})
    if target is not None and target.week > a.week:
        trades, skips = rebalance(a, target, quotes, rules, now)
        out['trades'] += trades
        out['skips'] = skips
        out['events'].append({'kind': 'rebalance', 'detail': f'{len(trades)} 筆成交、{len(skips)} 筆略過'})
    return out


class Ledger:
    """state/futures/futures.db：帳戶狀態、成交、權益、事件。"""

    def __init__(self, path: Path, readonly: bool = False):
        if readonly:
            self.db = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(path)
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts(name TEXT PRIMARY KEY, json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY, ts INTEGER, account TEXT, week INTEGER, sym TEXT, qty TEXT, price TEXT, mid TEXT, fee TEXT, reason TEXT);
                CREATE TABLE IF NOT EXISTS equity(ts INTEGER, account TEXT, equity TEXT, gross TEXT, PRIMARY KEY(ts, account));
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts INTEGER, account TEXT, kind TEXT, detail TEXT);
            """)

    def load(self, name: str) -> Account:
        row = self.db.execute('SELECT json FROM accounts WHERE name=?', (name,)).fetchone()
        return Account.from_json(row[0]) if row else Account(name)

    def save(self, a: Account, result: dict, now: int, mark_equity: bool) -> None:
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO accounts VALUES(?,?)', (a.name, a.to_json()))
            for t in result['trades']:
                self.db.execute('INSERT INTO trades(ts,account,week,sym,qty,price,mid,fee,reason) VALUES(?,?,?,?,?,?,?,?,?)',
                                (t['ts'], t['account'], t['week'], t['sym'], t['qty'], t['price'], t['mid'], t['fee'], t['reason']))
            for s in result['skips']:
                self.db.execute('INSERT INTO events(ts,account,kind,detail) VALUES(?,?,?,?)', (now, a.name, 'skip', f"{s['sym']}:{s['why']}"))
            for e in result['events']:
                self.db.execute('INSERT INTO events(ts,account,kind,detail) VALUES(?,?,?,?)', (now, a.name, e['kind'], e['detail']))
            if mark_equity or result['trades']:
                self.db.execute('INSERT OR REPLACE INTO equity VALUES(?,?,?,?)', (now, a.name, str(equity(a)), str(gross(a))))

    def equity_rows(self, name: str) -> list[tuple[int, Decimal]]:
        return [(t, Decimal(e)) for t, e in self.db.execute('SELECT ts, equity FROM equity WHERE account=? ORDER BY ts', (name,))]

    def trades(self, name: str) -> list[dict]:
        rows = self.db.execute('SELECT ts, week, sym, qty, price, mid, fee, reason FROM trades WHERE account=? ORDER BY id', (name,))
        return [dict(zip(('ts', 'week', 'sym', 'qty', 'price', 'mid', 'fee', 'reason'), r)) for r in rows]

    def events(self, name: str) -> list[tuple[int, str, str]]:
        return list(self.db.execute('SELECT ts, kind, detail FROM events WHERE account=? ORDER BY id', (name,)))

    def close(self) -> None:
        self.db.close()


def max_drawdown(values: list[Decimal]) -> Decimal:
    peak, worst = None, ZERO
    for v in values:
        peak = v if peak is None or v > peak else peak
        worst = min(worst, v / peak - 1)
    return worst


def summary(rows: list[tuple[int, Decimal]], ref_rows: list[tuple[int, Decimal]], trades: list[dict], events: list[tuple[int, str, str]],
            account: Account) -> dict:
    """給週報與操作台的事實數字（只列數字，不給建議）。"""
    vals = [e for _, e in rows]
    spreads = [abs(Decimal(t['price']) / Decimal(t['mid']) - 1) for t in trades if t['reason'] == 'rebalance' and Decimal(t['mid']) > 0]
    weeks = sorted({t['week'] for t in trades if t['week']})
    return {'weeks': len(weeks), 'equity': vals[-1] if vals else INITIAL, 'ret': (vals[-1] / INITIAL - 1) if vals else ZERO,
            'dd': max_drawdown(vals) if vals else ZERO, 'ref_equity': ref_rows[-1][1] if ref_rows else INITIAL,
            'fees': sum((Decimal(t['fee']) for t in trades), ZERO), 'half_spread': sum(spreads, ZERO) / len(spreads) if spreads else None,
            'stops': sum(1 for _, k, _d in events if k == 'stop'), 'skips': sum(1 for _, k, _d in events if k == 'skip'),
            'trades': len(trades), 'positions': len(account.positions), 'ended': account.ended}


def text(s: dict) -> str:
    if not s['trades']:
        return 'blend 前瞻模擬：還沒有成交（第一週是 2026-11-09）。'
    half = '—' if s['half_spread'] is None else f"{s['half_spread'] * 10000:.1f} bp"
    lines = [f"blend 前瞻模擬（只模擬，不下單）：第 {s['weeks']} 週，權益 {s['equity']:.2f} USDT（起始 300，{s['ret']:+.2%}），最大回撤 {s['dd']:.2%}。",
             f"對照（沒有緊急停損與帳戶線）：{s['ref_equity']:.2f} USDT。",
             f"成交 {s['trades']} 筆、手續費 {s['fees']:.2f} USDT、實際半價差平均 {half}（研究假設 5 bp）；緊急停損 {s['stops']} 次、略過 {s['skips']} 筆；持有 {s['positions']} 個部位。"]
    if s['ended']:
        lines.append('權益跌到起始的 70%，本次模擬已結束，等營運者決定。')
    return '\n'.join(lines)
