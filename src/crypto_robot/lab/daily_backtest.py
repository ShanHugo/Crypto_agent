"""日 K 規則回測：每天收盤後判斷，隔天開盤市價成交；每次成交扣 taker 手續費與滑價。

停損也是收盤判斷、隔天開盤賣（日線規則不盯盤中價格）。期末仍持有：以最後收盤價計算，不扣賣出成本。
"""
from __future__ import annotations

from decimal import Decimal

from crypto_robot.core.models import Bar
from crypto_robot.lab.backtest import CostParams, Result, Trade

ZERO = Decimal(0)
ONE = Decimal(1)


def run_rule(bars: list[Bar], start_ms: int, end_ms: int, rule, cp: CostParams,
             *, initial: Decimal = Decimal(1_000_000)) -> Result:
    """bars 要包含 start_ms 之前的暖機資料；只在 [start_ms, end_ms) 之間交易與記錄權益。"""
    rule.prepare(bars)
    res = Result(initial)
    cash, qty = initial, ZERO
    pos: dict | None = None
    action: str | None = None        # 前一天收盤決定、今天開盤執行

    for i, b in enumerate(bars):
        if b.ts < start_ms:
            continue
        if b.ts >= end_ms:
            break
        if action == 'buy' and pos is None:
            fill = b.open * (ONE + cp.slippage)
            qty = cash / (fill * (ONE + cp.taker_fee))
            gross = qty * fill
            fee = gross * cp.taker_fee
            res.costs += qty * (fill - b.open) + fee
            pos = {'ts': b.ts, 'entry_i': i, 'entry_price': fill, 'spent': gross + fee}
            cash -= gross + fee
        elif action == 'sell' and pos is not None:
            fill = b.open * (ONE - cp.slippage)
            gross = qty * fill
            fee = gross * cp.taker_fee
            res.costs += qty * (b.open - fill) + fee
            cash += gross - fee
            res.trades.append(Trade(pos['ts'], b.ts, pos['entry_price'], fill, qty,
                                    (gross - fee) / pos['spent'] - ONE, 'rule'))
            qty, pos = ZERO, None
        if pos is not None:
            res.bars_in_position += 1
        action = rule.decide(i, pos)
        res.equity.append((b.ts, cash + qty * b.close))

    if pos is not None:
        last_ts, last_equity = res.equity[-1]
        value = last_equity - cash
        res.trades.append(Trade(pos['ts'], last_ts, pos['entry_price'], value / qty, qty,
                                value / pos['spent'] - ONE, 'open'))
    return res
