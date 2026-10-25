from decimal import Decimal as D

import pytest

from crypto_robot.agents.strategy.rebalance import RebalanceParams, eth_weight, needs_rebalance, target_delta
from crypto_robot.core.models import Bar
from crypto_robot.lab.backtest import CostParams
from crypto_robot.lab.rebalance_backtest import rebalance_count, run_rebalance
from crypto_robot.lab.rebalance_variants import all_variants, by_name, passes

DAY = 86_400_000
NO_COST = CostParams(D(0), D(0))
P50 = RebalanceParams(D('0.5'), D('0.1'))


def daily(prices):
    """每天開盤 = 收盤 = 給定價格，方便手算。"""
    return [Bar(i * DAY, D(str(p)), D(str(p)), D(str(p)), D(str(p)), D(1)) for i, p in enumerate(prices)]


def test_weight_trigger_and_delta():
    assert eth_weight(D(1), D(100), D(100)) == D('0.5')
    assert not needs_rebalance(D(1), D(100), D(120), P50)     # 120/220 ≈ 54.5%
    assert needs_rebalance(D(1), D(100), D(150), P50)         # 150/250 = 60%
    assert target_delta(D(1), D(100), D(150), P50) == D('-0.1666666666666666666666666667')
    assert target_delta(D(1), D(100), D(50), P50) == D('0.5')


def test_params_validation():
    with pytest.raises(ValueError):
        RebalanceParams(D('0.3'), D('0.3'))
    with pytest.raises(ValueError):
        RebalanceParams(D('1'), D('0.1'))


def test_rebalance_buys_low_sells_high_next_open():
    bars = daily([100, 150, 150, 100, 100])
    res = run_rebalance(bars, 0, len(bars) * DAY, P50, NO_COST, initial=D(1000))
    reasons = [t.reason for t in res.trades]
    assert reasons == ['init', 'rebal-sell', 'rebal-buy']
    assert res.trades[1].entry_ts == 2 * DAY                       # 第 1 天收盤偏離，第 2 天開盤賣
    assert rebalance_count(res) == 2
    static = run_rebalance(bars, 0, len(bars) * DAY, P50, NO_COST, initial=D(1000), rebalance=False)
    assert static.equity[-1][1] == D(1000)                         # 價格回到原點，不再平衡沒賺
    assert res.equity[-1][1] > D(1000)                             # 再平衡在來回之間賺到價差


def test_costs_reduce_result_and_cash_never_negative():
    bars = daily([100, 150, 60, 150, 60, 150])
    cp = CostParams(D('0.0016'), D('0.001'))
    res = run_rebalance(bars, 0, len(bars) * DAY, P50, cp, initial=D(1000))
    free = run_rebalance(bars, 0, len(bars) * DAY, P50, NO_COST, initial=D(1000))
    assert res.costs > 0 and res.equity[-1][1] < free.equity[-1][1]
    assert all(v > 0 for _, v in res.equity)


def test_rebalance_grid():
    vs = all_variants()
    assert len(vs) == 12 and len({v.name for v in vs}) == 12
    assert by_name('rebal-eth50-band10').params == P50


def test_rebalance_passes():
    halves = [{'ret': 0.2, 'static_ret': 0.1}, {'ret': 0.3, 'static_ret': 0.25}]
    assert passes(10, 0.6, 0.4, -0.3, -0.4, halves) == (True, [])
    ok, fails = passes(3, 0.3, 0.4, -0.5, -0.4, [{'ret': -0.1, 'static_ret': -0.2}, {'ret': 0.1, 'static_ret': 0.2}])
    assert not ok
    assert len(fails) == 5   # 次數、研究期沒多賺、回撤深、前半報酬 ≤ 0、後半沒多賺（前半有多賺）
