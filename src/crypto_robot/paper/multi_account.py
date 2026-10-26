"""多資產比例再平衡影子帳戶（不下真單）：和主模擬帳戶同一次排程執行，用同樣的盤口模擬與成本。

和 rebalance_account.py 相同的做法：
- 第一次執行：用初始資金市價買到各資產目標比例。
- 之後每天：任何一項（含台幣）偏離目標達門檻，就以市價全部調回（先賣後買，錢不夠就縮小買量）。
- 同一個台北日期只執行一次；估值用各市場買一賣一的中間價；手續費以台幣計。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from crypto_robot.agents.strategy.rebalance_multi import MultiParams, needs_rebalance, target_deltas, weights
from crypto_robot.core.models import Market, OrderBook
from crypto_robot.core.num import amount_to_precision
from crypto_robot.core.timeutil import taipei_date
from crypto_robot.paper.fills import market_buy, market_sell
from crypto_robot.paper.rebalance_account import _tradable, mid_price

ZERO = Decimal(0)

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    started_at INTEGER NOT NULL, initial TEXT NOT NULL, targets TEXT NOT NULL, band TEXT NOT NULL,
    cash TEXT NOT NULL, holdings TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    ts INTEGER NOT NULL, date TEXT NOT NULL, asset TEXT NOT NULL, side TEXT NOT NULL, reason TEXT NOT NULL,
    qty TEXT NOT NULL, avg_price TEXT NOT NULL, gross TEXT NOT NULL, fee TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    date TEXT PRIMARY KEY, ts INTEGER NOT NULL, prices TEXT NOT NULL, cash TEXT NOT NULL, holdings TEXT NOT NULL,
    equity TEXT NOT NULL, weights TEXT NOT NULL, action TEXT NOT NULL, note TEXT NOT NULL
);
"""


def _dump(d: dict[str, Decimal]) -> str:
    return json.dumps({k: str(v) for k, v in d.items()})


def _load(s: str) -> dict[str, Decimal]:
    return {k: Decimal(v) for k, v in json.loads(s).items()}


class MultiPaperStore:
    def __init__(self, path: str | Path):
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def account(self) -> dict | None:
        row = self.db.execute('SELECT started_at, initial, targets, band, cash, holdings FROM account WHERE id = 1').fetchone()
        if row is None:
            return None
        started, initial, targets, band, cash, holdings = row
        params = MultiParams(tuple((a, Decimal(t)) for a, t in json.loads(targets)), Decimal(band))
        return {'started_at': started, 'initial': Decimal(initial), 'params': params,
                'cash': Decimal(cash), 'holdings': _load(holdings)}

    def has_snapshot(self, day: date) -> bool:
        return self.db.execute('SELECT 1 FROM snapshots WHERE date = ?', (day.isoformat(),)).fetchone() is not None

    def history(self) -> list[dict]:
        cur = self.db.execute('SELECT * FROM snapshots ORDER BY date')
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def trade_rows(self) -> list[dict]:
        cur = self.db.execute('SELECT * FROM trades ORDER BY ts')
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def run_day(store: MultiPaperStore, books: dict[str, OrderBook], markets: dict[str, Market], params: MultiParams,
            fee: Decimal, now_ms: int, initial: Decimal, *, write: bool = True) -> dict:
    """books／markets：資產 → 盤口／市場資訊。write=False 只回傳結果、不寫入。"""
    today = taipei_date(now_ms)
    if store.has_snapshot(today):
        return {'status': 'already', 'date': today}
    prices = {a: mid_price(b) for a, b in books.items()}
    acct = store.account()
    first = acct is None
    if first:
        acct = {'started_at': now_ms, 'initial': initial, 'params': params, 'cash': initial,
                'holdings': {a: ZERO for a, _ in params.targets}}
    cash, qty = acct['cash'], dict(acct['holdings'])
    trades, note = [], []
    p = acct['params']

    if first or needs_rebalance(qty, cash, prices, p):
        deltas = target_deltas(qty, cash, prices, p)
        for a in sorted(deltas, key=lambda x: deltas[x]):          # 先賣後買
            m, d = markets[a], deltas[a]
            if d < 0:
                f = market_sell(books[a], amount_to_precision(min(-d, qty[a]), m.amount_places), fee)
            elif d > 0:
                q = amount_to_precision(d, m.amount_places)
                f = market_buy(books[a], q, fee)
                while q > 0 and f.gross + f.fee > cash:              # 錢不夠就每次縮小 1%
                    q = amount_to_precision(q * Decimal('0.99'), m.amount_places)
                    f = market_buy(books[a], q, fee)
            else:
                continue
            if not _tradable(f.qty, f.avg_price or prices[a], m):
                note.append(f'{a.upper()} 差額低於交易所最低下單量，不調整')
                continue
            if d < 0:
                cash += f.gross - f.fee
                qty[a] -= f.qty
            else:
                cash -= f.gross + f.fee
                qty[a] += f.qty
            if f.qty < abs(d) * Decimal('0.99') and not first:
                note.append(f'{a.upper()} 盤口深度不足，只部分成交')
            trades.append((a, 'buy' if d > 0 else 'sell', 'init' if first else 'rebalance', f))
        action = 'init' if first else ('rebalance' if trades else 'too-small')
    else:
        action = 'hold'

    eq = cash + sum(qty[a] * prices[a] for a in qty)
    w = weights(qty, cash, prices)
    result = {'status': 'done', 'date': today, 'action': action, 'prices': prices, 'cash': cash, 'holdings': qty,
              'equity': eq, 'weights': w, 'initial': acct['initial'], 'params': p, 'note': note,
              'trades': [{'asset': a, 'side': s, 'qty': f.qty, 'avg_price': f.avg_price, 'fee': f.fee} for a, s, _, f in trades]}
    if write:
        with store.db:
            store.db.execute('INSERT OR REPLACE INTO account VALUES (1,?,?,?,?,?,?)', (
                acct['started_at'], str(acct['initial']), json.dumps([(a, str(t)) for a, t in p.targets]), str(p.band),
                str(cash), _dump(qty)))
            for a, s, reason, f in trades:
                store.db.execute('INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?)', (
                    now_ms, today.isoformat(), a, s, reason, str(f.qty), str(f.avg_price), str(f.gross), str(f.fee)))
            store.db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?)', (
                today.isoformat(), now_ms, _dump(prices), str(cash), _dump(qty), str(eq), _dump(w), action, '；'.join(note)))
    return result


def day_report(store: MultiPaperStore, day: date) -> dict | None:
    """從資料庫讀出某一天的結果；那天沒有執行就回傳 None。"""
    row = store.db.execute('SELECT prices, cash, holdings, equity, weights, action, note FROM snapshots WHERE date = ?',
                           (day.isoformat(),)).fetchone()
    acct = store.account()
    if row is None or acct is None:
        return None
    prices, cash, holdings, eq, w, action, note = row
    trades = store.db.execute('SELECT asset, side, qty, avg_price, fee FROM trades WHERE date = ? ORDER BY ts',
                              (day.isoformat(),)).fetchall()
    return {'status': 'done', 'date': day, 'action': action, 'prices': _load(prices), 'cash': Decimal(cash),
            'holdings': _load(holdings), 'equity': Decimal(eq), 'weights': _load(w), 'initial': acct['initial'],
            'params': acct['params'], 'note': [n for n in note.split('；') if n],
            'trades': [{'asset': a, 'side': s, 'qty': Decimal(q), 'avg_price': Decimal(px), 'fee': Decimal(f)}
                       for a, s, q, px, f in trades]}
