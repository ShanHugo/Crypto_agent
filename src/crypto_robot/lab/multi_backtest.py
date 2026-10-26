"""多資產比例再平衡回測（日 K）。和 rebalance_backtest.py 同樣的成交假設：

- 期初第一天開盤買到目標比例（再平衡與基準相同）。
- 每天收盤判斷是否偏離門檻；觸發時隔天開盤以開盤價重算買賣量並成交：先賣後買，買不夠錢就縮小。
- 每次成交扣 taker 手續費，價格往不利方向移動 slippage。
- rebalance=False 就是基準：同比例買進後不再調整。
只用各資產都有日 K 的日子。
"""
from __future__ import annotations

from decimal import Decimal

from crypto_robot.agents.strategy.rebalance_multi import MultiParams, needs_rebalance, target_deltas
from crypto_robot.core.models import Bar
from crypto_robot.lab.backtest import CostParams, Result, Trade

ZERO = Decimal(0)
ONE = Decimal(1)


def align(daily: dict[str, list[Bar]]) -> list[tuple[int, dict[str, Bar]]]:
    """各資產同一天的日 K，由舊到新；任何一個資產缺那天就跳過。"""
    by = {a: {b.ts: b for b in bars} for a, bars in daily.items()}
    common = set.intersection(*(set(m) for m in by.values()))
    return [(ts, {a: by[a][ts] for a in by}) for ts in sorted(common)]


def run_multi(daily: dict[str, list[Bar]], start_ms: int, end_ms: int, p: MultiParams, cp: CostParams,
              *, initial: Decimal = Decimal(1_000_000), rebalance: bool = True) -> Result:
    """trades 的 reason：init（期初建倉）、rebal-buy、rebal-sell；entry_price 是成交價，qty 正買負賣。"""
    assets = [a for a, _ in p.targets]
    res = Result(initial)
    cash = initial
    qty = {a: ZERO for a in assets}
    started = pending = False

    def trade(ts: int, bars: dict[str, Bar], init: bool):
        nonlocal cash
        opens = {a: bars[a].open for a in assets}
        deltas = target_deltas(qty, cash, opens, p)
        for a in sorted(assets, key=lambda x: deltas[x]):          # 先賣（負的在前）再買
            d = deltas[a]
            if d < 0:
                fill = opens[a] * (ONE - cp.slippage)
                d = max(d, -qty[a])
                gross = -d * fill
                fee = gross * cp.taker_fee
                cash += gross - fee
            elif d > 0:
                fill = opens[a] * (ONE + cp.slippage)
                d = min(d, cash / (fill * (ONE + cp.taker_fee)))
                gross = d * fill
                fee = gross * cp.taker_fee
                cash -= gross + fee
            else:
                continue
            qty[a] += d
            res.costs += abs(d) * abs(fill - opens[a]) + fee
            res.trades.append(Trade(ts, ts, fill, fill, d, ZERO,
                                    'init' if init else ('rebal-buy' if d > 0 else 'rebal-sell')))

    for ts, bars in align(daily):
        if ts < start_ms:
            continue
        if ts >= end_ms:
            break
        if not started:
            trade(ts, bars, True)
            started = True
        elif pending:
            trade(ts, bars, False)
        closes = {a: bars[a].close for a in assets}
        pending = rebalance and needs_rebalance(qty, cash, closes, p)
        res.bars_in_position += 1
        res.equity.append((ts, cash + sum(qty[a] * closes[a] for a in assets)))
    return res


def rebalance_days(res: Result) -> int:
    """有再平衡成交的天數（一天可能同時買賣好幾項資產）。"""
    return len({t.entry_ts for t in res.trades if t.reason.startswith('rebal')})
