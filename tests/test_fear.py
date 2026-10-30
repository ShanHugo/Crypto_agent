from decimal import Decimal as D

import pytest

from crypto_robot.core.models import Bar
from crypto_robot.lab.fear_research import evaluate_asset, find_signals, parse_fng, rsi
from crypto_robot.lab.fear_variants import DAY_MS, FearVariant, all_variants, passes_dev, passes_holdout


def bars(prices):
    return [Bar(i * DAY_MS, D(str(p)), D(str(p)), D(str(p)), D(str(p)), D(1)) for i, p in enumerate(prices)]


def test_parse_fng_maps_to_utc_day():
    assert parse_fng({'data': [{'value': '12', 'timestamp': str(3 * 86_400)}]}) == {3 * DAY_MS: 12}


def test_rsi_extremes_and_known_value():
    assert rsi([float(i) for i in range(20)])[14] == 100.0
    r = rsi([20.0 - i for i in range(20)])
    assert r[13] is None and r[14] == pytest.approx(0.0)
    up_down = [10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10]      # 漲跌各 7 次、幅度相同
    assert rsi([float(x) for x in up_down])[14] == pytest.approx(50.0)


def test_signals_skip_holding_period_and_need_rsi():
    b = bars([100 - i for i in range(40)])                                        # 一路下跌：RSI 很低
    fng = {i * DAY_MS: 10 for i in range(40)}
    v = FearVariant('t', True, 5)
    s = find_signals(b, fng, v, 0, 40 * DAY_MS)
    assert s[0] == 14 and s[1] == 20                                              # RSI 從第 14 天才有；持有 5 天後才接下一個
    assert find_signals(b, {k: 30 for k in fng}, v, 0, 40 * DAY_MS) == []         # 不夠恐懼
    flat = bars([100] * 40)
    assert find_signals(flat, fng, FearVariant('t', False, 5), 0, 40 * DAY_MS)[0] == 0   # 只看恐懼：第 0 天就算


def test_evaluate_asset_flat_prices_only_cost():
    r = evaluate_asset(bars([100] * 40), {i * DAY_MS: 10 for i in range(40)}, FearVariant('t', False, 5),
                       0, 40 * DAY_MS, 20 * DAY_MS, runs=50)
    assert r['n'] > 0 and r['mean_excess'] == pytest.approx(0.0) and not r['passed']


def test_grid_and_criteria():
    assert [v.name for v in all_variants()] == ['fear25-rsi30-h30', 'fear25-rsi30-h90', 'fear25-h30', 'fear25-h90']
    assert passes_dev(10, 0.97, 3.5, [0.02, 0.01], 0.01) == (True, [])
    assert len(passes_dev(3, 0.5, 1.0, [-0.1, 0.0], -0.01)[1]) == 6
    assert passes_holdout(5, 0.01, 0.95) == (True, [])
    assert len(passes_holdout(2, -0.01, 0.5)[1]) == 3
