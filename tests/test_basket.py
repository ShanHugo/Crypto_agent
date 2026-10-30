from decimal import Decimal as D

import pytest

from crypto_robot.core.models import Bar
from crypto_robot.lab.backtest import CostParams
from crypto_robot.lab.basket_variants import BASELINE, all_variants, by_name, passes_dev, passes_holdout
from crypto_robot.lab.multi_backtest import rebalance_days, run_multi

DAY = 86_400_000
NO_COST = CostParams(D(0), D(0))


def daily(prices):
    return [Bar(i * DAY, D(str(p)), D(str(p)), D(str(p)), D(str(p)), D(1)) for i, p in enumerate(prices)]


def test_grid_and_baseline():
    vs = all_variants()
    assert [v.name for v in vs] == ['btc25-eth25-band5', 'btc25-eth25-band10', 'btc15-eth35-band5', 'btc15-eth35-band10']
    p = by_name('btc15-eth35-band5').params
    assert dict(p.targets) == {'btc': D('0.15'), 'eth': D('0.35'), 'usdt': D('0.375')} and p.cash_target == D('0.125')
    assert dict(BASELINE.targets) == {'eth': D('0.5'), 'usdt': D('0.375')} and BASELINE.band == D('0.05')


def test_four_asset_rebalance_when_btc_doubles():
    data = {'btc': daily([100, 200, 200, 200]), 'eth': daily([10] * 4), 'usdt': daily([30] * 4)}
    res = run_multi(data, 0, 4 * DAY, by_name('btc25-eth25-band5').params, NO_COST, initial=D(1000))
    assert rebalance_days(res) == 1
    assert res.equity[-1][1] == pytest.approx(D(1250))                         # 250 BTC 翻倍，其餘不動


def test_criteria():
    good = [{'ret': 0.3, 'base_ret': 0.2}, {'ret': 0.1, 'base_ret': 0.05}]
    assert passes_dev(10, 0.5, 0.4, -0.3, -0.4, good, 0.45, 0.4) == (True, [])
    assert len(passes_dev(3, 0.3, 0.4, -0.5, -0.4, [{'ret': 0.1, 'base_ret': 0.2}] * 2, 0.1, 0.2)[1]) == 6
    assert passes_holdout(0.2, 0.1, -0.3, -0.35) == (True, [])
    assert len(passes_holdout(0.1, 0.2, -0.4, -0.35)[1]) == 2
