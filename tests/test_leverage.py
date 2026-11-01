from decimal import Decimal as D

import pytest

from crypto_robot.core.models import Bar
from crypto_robot.lab.leverage_backtest import COST, align, segment, simulate
from crypto_robot.lab.leverage_variants import all_variants, passes

H = 3_600_000


def bars(prices, low=None):
    return [Bar(i * H, D(str(p)), D(str(p)), D(str(low[i] if low else p)), D(str(p)), D(1)) for i, p in enumerate(prices)]


def rows(eth, usdt=None, low=None):
    return align(bars(eth, low), bars(usdt or [30] * len(eth)), 0, len(eth) * H)


def test_one_x_is_hold_minus_entry_cost():
    r = simulate(rows([100, 200]), 1.0, 0.2, 0.0)
    assert r.equity[-1][1] == pytest.approx(10_000 * (1 - COST) * 2) and r.min_ad == float('inf')


def test_two_x_doubles_gain_and_charges_interest():
    r = simulate(rows([100, 110]), 2.0, 9, 0.0)
    assert r.equity[-1][1] == pytest.approx(20_000 * (1 - COST) * 1.1 - 10_000)
    with_int = simulate(rows([100, 110]), 2.0, 9, 0.001)
    assert with_int.equity[-1][1] < r.equity[-1][1] and with_int.interest > 0


def test_liquidation_on_hourly_low_ends_account():
    # 2 倍：資產 ≈ 20,000、負債 10,000；最低價跌到 60 → 資產 ≈ 12,000，A/D ≈ 1.2 → 強平
    r = simulate(rows([100, 100, 100, 100], low=[100, 59, 100, 100]), 2.0, 9, 0.0)
    assert r.liquidated_at == H
    assert r.equity[-1][1] == r.equity[1][1] and r.equity[-1][1] < 2_000


def test_daily_rebalance_back_to_target():
    n = 40                                                   # 跨過台北 00:00（UTC 16:00）
    eth = [100] * 10 + [150] * (n - 10)
    r = simulate(rows(eth), 2.0, 0.2, 0.0)
    assert r.rebalances == 1                                 # 漲 50% 後槓桿掉到 1.5 倍以下，隔天調回


def test_segment_and_criteria():
    r = simulate(rows([100, 100, 200, 200]), 1.0, 0.2, 0.0)
    assert segment(r, 2 * H, 4 * H) == pytest.approx(1.0)
    assert [v.name for v in all_variants()] == ['lev1.5-band0.2', 'lev1.5-band0.5', 'lev2-band0.2', 'lev2-band0.5']
    assert passes(False, 1.6, [0.5, 0.3], [0.4, 0.2], -0.5, [0.45, 0.25]) == (True, [])
    assert len(passes(True, 1.1, [0.1, 0.1], [0.4, 0.2], -0.8, [0.0, 0.0])[1]) == 7
