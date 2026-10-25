"""比例再平衡回測（日 K）。

- 期初第一根開盤，買到目標比例（再平衡與基準相同）。
- 每天收盤判斷是否偏離門檻；觸發時隔天開盤以開盤價重算買賣量並成交。
- 每次成交扣 taker 手續費，價格往不利方向移動 slippage。
- rebalance=False 就是基準：同比例買進後不再調整。
"""
from __future__ import annotations

from decimal import Decimal

from crypto_robot.agents.strategy.rebalance import RebalanceParams, needs_rebalance, target_delta
from crypto_robot.core.models import Bar
from crypto_robot.lab.backtest import CostParams, Result, Trade

ZERO = Decimal(0)
ONE = Decimal(1)


def run_rebalance(bars: list[Bar], start_ms: int, end_ms: int, p: RebalanceParams, cp: CostParams,
                  *, initial: Decimal = Decimal(1_000_000), rebalance: bool = True) -> Result:
    """trades 只記錄成交：reason 為 init（期初建倉）、rebal-buy、rebal-sell。"""
    res = Result(initial)
    cash, qty = initial, ZERO
    started = False
    pending = False

    def trade(b: Bar, reason_prefix: str):
        nonlocal cash, qty
        delta = target_delta(qty, cash, b.open, p)
        if delta > 0:
            fill = b.open * (ONE + cp.slippage)
            delta = min(delta, cash / (fill * (ONE + cp.taker_fee)))
            gross = delta * fill
            fee = gross * cp.taker_fee
            cash -= gross + fee
            qty += delta
            reason = 'buy'
        elif delta < 0:
            fill = b.open * (ONE - cp.slippage)
            delta = max(delta, -qty)
            gross = -delta * fill
            fee = gross * cp.taker_fee
            cash += gross - fee
            qty += delta
            reason = 'sell'
        else:
            return
        res.costs += abs(delta) * abs(fill - b.open) + fee
        res.trades.append(Trade(b.ts, b.ts, fill, fill, delta, ZERO,
                                'init' if reason_prefix == 'init' else f'rebal-{reason}'))

    for b in bars:
        if b.ts < start_ms:
            continue
        if b.ts >= end_ms:
            break
        if not started:
            trade(b, 'init')
            started = True
        elif pending:
            trade(b, 'rebal')
        pending = rebalance and needs_rebalance(qty, cash, b.close, p)
        res.bars_in_position += 1
        res.equity.append((b.ts, cash + qty * b.close))
    return res


def rebalance_count(res: Result) -> int:
    return sum(1 for t in res.trades if t.reason.startswith('rebal'))
