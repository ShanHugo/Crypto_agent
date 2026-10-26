from decimal import Decimal as D

import pytest

from crypto_robot.agents.strategy.rebalance import RebalanceParams
from crypto_robot.agents.strategy.rebalance_multi import MultiParams, needs_rebalance, target_deltas, weights
from crypto_robot.core.models import Bar
from crypto_robot.lab.backtest import CostParams
from crypto_robot.lab.multi_backtest import align, rebalance_days, run_multi
from crypto_robot.lab.multi_variants import SHADOW, all_variants, by_name, passes
from crypto_robot.lab.rebalance_backtest import run_rebalance

DAY = 86_400_000
COSTS = CostParams(D('0.0016'), D('0.001'))
NO_COST = CostParams(D(0), D(0))
P3 = MultiParams((('eth', D('0.5')), ('usdt', D('0.375'))), D('0.10'))


def daily(prices, skip=()):
    return [Bar(i * DAY, D(str(p)), D(str(p)), D(str(p)), D(str(p)), D(1)) for i, p in enumerate(prices) if i not in skip]


def test_params_validation_and_cash_target():
    assert P3.cash_target == D('0.125')
    with pytest.raises(ValueError):
        MultiParams((('eth', D('0.6')), ('usdt', D('0.4'))), D('0.05'))        # 沒有台幣
    with pytest.raises(ValueError):
        MultiParams((('eth', D('0.5')), ('usdt', D('0.375'))), D('0.2'))      # 門檻 > 台幣 12.5%


def test_weights_trigger_and_deltas():
    qty, prices = {'eth': D(1), 'usdt': D(30)}, {'eth': D(100), 'usdt': D('2.5')}
    w = weights(qty, D(25), prices)                                            # 100 + 75 + 25 = 200
    assert w == {'eth': D('0.5'), 'usdt': D('0.375'), 'twd': D('0.125')}
    assert not needs_rebalance(qty, D(25), prices, P3)
    up = {'eth': D(160), 'usdt': D('2.5')}                                     # 160 / 260 ≈ 61.5%
    assert needs_rebalance(qty, D(25), up, P3)
    d = target_deltas(qty, D(25), up, P3)
    assert d['eth'] == D('-0.1875') and d['usdt'] == D(9)                      # 260 × 0.5 = 130；260 × 0.375 = 97.5


def test_align_skips_days_missing_in_any_market():
    rows = align({'eth': daily([1, 2, 3]), 'usdt': daily([1, 2, 3], skip={1})})
    assert [ts for ts, _ in rows] == [0, 2 * DAY]


def test_two_asset_engine_matches_existing_backtest():
    eth = daily([100, 150, 150, 90, 95, 140, 60, 100])
    usdt = daily([30] * 8)
    multi = run_multi({'eth': eth, 'usdt': usdt}, 0, 8 * DAY, MultiParams((('eth', D('0.5')),), D('0.10')), COSTS)
    single = run_rebalance(eth, 0, 8 * DAY, RebalanceParams(D('0.5'), D('0.10')), COSTS)
    assert [v for _, v in multi.equity] == [v for _, v in single.equity]


def test_three_asset_rebalance_sells_winner_buys_others():
    eth = daily([100, 200, 200, 200])
    usdt = daily([30, 30, 30, 30])
    res = run_multi({'eth': eth, 'usdt': usdt}, 0, 4 * DAY, P3, NO_COST, initial=D(1000))
    assert [t.reason for t in res.trades[:2]] == ['init', 'init']
    rebal = [t for t in res.trades if t.reason.startswith('rebal')]
    assert {t.reason for t in rebal} == {'rebal-sell', 'rebal-buy'} and rebal[0].reason == 'rebal-sell'   # 先賣後買
    assert rebalance_days(res) == 1
    assert res.equity[-1][1] == pytest.approx(D(1500))                         # 500 ETH 翻倍＋375＋125
    static = run_multi({'eth': eth, 'usdt': usdt}, 0, 4 * DAY, P3, NO_COST, initial=D(1000), rebalance=False)
    assert rebalance_days(static) == 0


def test_cash_never_negative_with_costs():
    eth = daily([100, 300, 50, 300, 50, 300])
    usdt = daily([30, 28, 33, 28, 33, 30])
    res = run_multi({'eth': eth, 'usdt': usdt}, 0, 6 * DAY, P3, COSTS, initial=D(1000))
    assert res.costs > 0 and all(v > 0 for _, v in res.equity)


def test_grid_and_shadow():
    vs = all_variants()
    assert len(vs) == 4 and len({v.name for v in vs}) == 4
    s = by_name(SHADOW)
    assert s.params == P3 and s.baseline == MultiParams((('eth', D('0.5')),), D('0.10'))
    assert by_name('rebal3-eth50-cashusdt50-band5').params.targets[1] == ('usdt', D('0.25'))


def test_passes():
    good = [{'ret': 0.3, 'base_ret': 0.2}, {'ret': 0.1, 'base_ret': 0.05}]
    assert passes(10, 0.5, 0.4, -0.3, -0.4, good) == (True, [])
    ok, fails = passes(3, 0.3, 0.4, -0.5, -0.4, [{'ret': -0.1, 'base_ret': 0.0}, {'ret': 0.1, 'base_ret': 0.2}])
    assert not ok and len(fails) == 6
