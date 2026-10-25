"""比例再平衡模擬帳戶（不下真單）。

每天在 01:30～07:30 時段內執行一次：
- 第一次執行：用初始資金市價買到目標比例；同時建立兩個對照帳戶（同比例不再平衡、全部買 ETH），用同樣的盤口與成本。
- 之後每天：ETH 比例偏離目標達門檻就以市價調回目標；否則不動作。每天記一筆快照。
- 同一個台北日期只執行一次（重跑不會重複交易）。
估值用盤口買一賣一的中間價。手續費一律以台幣計（實際 MAX 買入時手續費可能扣 ETH，金額相同）。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from crypto_robot.agents.strategy.intraday import taipei_date, taipei_hm
from crypto_robot.agents.strategy.rebalance import RebalanceParams, eth_weight, needs_rebalance, target_delta
from crypto_robot.core.models import Market, OrderBook
from crypto_robot.core.num import amount_to_precision
from crypto_robot.paper.fills import Fill, market_buy, market_sell

ZERO = Decimal(0)
ONE = Decimal(1)
WINDOW = ((1, 30), (7, 30))      # 使用者只允許凌晨這段時間自動交易

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    started_at INTEGER NOT NULL, initial TEXT NOT NULL, target TEXT NOT NULL, band TEXT NOT NULL,
    cash TEXT NOT NULL, eth TEXT NOT NULL,
    static_cash TEXT NOT NULL, static_eth TEXT NOT NULL,
    hodl_cash TEXT NOT NULL, hodl_eth TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    ts INTEGER NOT NULL, date TEXT NOT NULL, side TEXT NOT NULL, reason TEXT NOT NULL,
    qty TEXT NOT NULL, avg_price TEXT NOT NULL, gross TEXT NOT NULL, fee TEXT NOT NULL,
    weight_before TEXT NOT NULL, weight_after TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    date TEXT PRIMARY KEY, ts INTEGER NOT NULL, price TEXT NOT NULL,
    cash TEXT NOT NULL, eth TEXT NOT NULL, equity TEXT NOT NULL, weight TEXT NOT NULL,
    static_equity TEXT NOT NULL, hodl_equity TEXT NOT NULL, action TEXT NOT NULL, note TEXT NOT NULL
);
"""


@dataclass
class Account:
    started_at: int
    initial: Decimal
    params: RebalanceParams
    cash: Decimal
    eth: Decimal
    static_cash: Decimal
    static_eth: Decimal
    hodl_cash: Decimal
    hodl_eth: Decimal


class PaperStore:
    def __init__(self, path: str | Path):
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def account(self) -> Account | None:
        row = self.db.execute('SELECT started_at, initial, target, band, cash, eth, static_cash, static_eth, '
                              'hodl_cash, hodl_eth FROM account WHERE id = 1').fetchone()
        if row is None:
            return None
        started, initial, target, band, *rest = row
        return Account(started, Decimal(initial), RebalanceParams(Decimal(target), Decimal(band)),
                       *(Decimal(x) for x in rest))

    def has_snapshot(self, day: date) -> bool:
        return self.db.execute('SELECT 1 FROM snapshots WHERE date = ?', (day.isoformat(),)).fetchone() is not None

    def last_snapshot_date(self) -> date | None:
        row = self.db.execute('SELECT MAX(date) FROM snapshots').fetchone()
        return date.fromisoformat(row[0]) if row[0] else None

    def snapshots(self) -> list[tuple]:
        return self.db.execute('SELECT date, price, equity, weight, static_equity, hodl_equity, action '
                               'FROM snapshots ORDER BY date').fetchall()

    def trades(self) -> list[tuple]:
        return self.db.execute('SELECT date, side, reason, qty, avg_price, gross, fee FROM trades ORDER BY ts').fetchall()


def in_window(ts: int) -> bool:
    return WINDOW[0] <= taipei_hm(ts) < WINDOW[1]


def mid_price(book: OrderBook) -> Decimal:
    if not book.bids or not book.asks:
        raise ValueError('盤口沒有買單或賣單，無法估值')
    return (book.bids[0][0] + book.asks[0][0]) / 2


def _buy_within_cash(book: OrderBook, market: Market, qty: Decimal, cash: Decimal, fee: Decimal) -> Fill:
    """市價買 qty，若含手續費超過現金就逐步縮小（每次 1%）。"""
    qty = amount_to_precision(qty, market.amount_places)
    while qty > 0:
        f = market_buy(book, qty, fee)
        if f.gross + f.fee <= cash:
            return f
        qty = amount_to_precision(qty * Decimal('0.99'), market.amount_places)
    return market_buy(book, ZERO, fee)


def _tradable(qty: Decimal, price: Decimal, market: Market) -> bool:
    return qty >= market.min_amount and qty * price >= market.min_cost


def run_day(store: PaperStore, book: OrderBook, market: Market, params: RebalanceParams, fee: Decimal,
            now_ms: int, initial: Decimal, *, write: bool = True) -> dict:
    """執行一天。write=False 只回傳結果、不寫入（--dry-run）。"""
    today = taipei_date(now_ms)
    if store.has_snapshot(today):
        return {'status': 'already', 'date': today}
    price = mid_price(book)
    acct = store.account()
    trades = []
    note = []
    last = store.last_snapshot_date()
    if last is not None and (today - last).days > 1:
        note.append(f'距離上次執行 {(today - last).days} 天（中間有漏跑）')

    if acct is None:
        acct = Account(now_ms, initial, params, initial, ZERO, initial, ZERO, initial, ZERO)
        f = _buy_within_cash(book, market, target_delta(ZERO, initial, price, params), initial, fee)
        if not _tradable(f.qty, f.avg_price, market):
            raise ValueError('初始資金太少，買不到交易所最低下單量')
        acct.cash -= f.gross + f.fee
        acct.eth = f.qty
        acct.static_cash, acct.static_eth = acct.cash, acct.eth
        h = _buy_within_cash(book, market, initial / (price * (ONE + fee)), initial, fee)
        acct.hodl_cash, acct.hodl_eth = initial - h.gross - h.fee, h.qty
        trades.append(('buy', 'init', f, ZERO, eth_weight(acct.eth, acct.cash, price)))
        action = 'init'
    else:
        w = eth_weight(acct.eth, acct.cash, price)
        if not needs_rebalance(acct.eth, acct.cash, price, params):
            action = 'hold'
        else:
            delta = target_delta(acct.eth, acct.cash, price, params)
            if delta > 0:
                f = _buy_within_cash(book, market, delta, acct.cash, fee)
                side = 'buy'
            else:
                f = market_sell(book, amount_to_precision(min(-delta, acct.eth), market.amount_places), fee)
                side = 'sell'
            if not _tradable(f.qty, f.avg_price or price, market):
                action = 'too-small'
                note.append('差額低於交易所最低下單量，不調整')
            else:
                if side == 'buy':
                    acct.cash -= f.gross + f.fee
                    acct.eth += f.qty
                else:
                    acct.cash += f.gross - f.fee
                    acct.eth -= f.qty
                if f.qty < abs(delta) * Decimal('0.99'):
                    note.append('盤口深度不足，只部分成交')
                trades.append((side, 'rebalance', f, w, eth_weight(acct.eth, acct.cash, price)))
                action = side

    equity = acct.cash + acct.eth * price
    result = {
        'status': 'done', 'date': today, 'action': action, 'price': price, 'cash': acct.cash, 'eth': acct.eth,
        'equity': equity, 'weight': eth_weight(acct.eth, acct.cash, price),
        'static_equity': acct.static_cash + acct.static_eth * price,
        'hodl_equity': acct.hodl_cash + acct.hodl_eth * price,
        'initial': acct.initial, 'trades': trades, 'note': note, 'params': acct.params,
    }
    if write:
        with store.db:
            store.db.execute('INSERT OR REPLACE INTO account VALUES (1,?,?,?,?,?,?,?,?,?,?)', (
                acct.started_at, str(acct.initial), str(acct.params.target), str(acct.params.band),
                str(acct.cash), str(acct.eth), str(acct.static_cash), str(acct.static_eth),
                str(acct.hodl_cash), str(acct.hodl_eth)))
            for side, reason, f, wb, wa in trades:
                store.db.execute('INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?)', (
                    now_ms, today.isoformat(), side, reason, str(f.qty), str(f.avg_price), str(f.gross), str(f.fee),
                    str(wb), str(wa)))
            store.db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)', (
                today.isoformat(), now_ms, str(price), str(acct.cash), str(acct.eth), str(equity),
                str(result['weight']), str(result['static_equity']), str(result['hodl_equity']), action,
                '；'.join(note)))
    return result
