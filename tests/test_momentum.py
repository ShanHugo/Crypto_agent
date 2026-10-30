from datetime import datetime, timezone
from decimal import Decimal as D

import pytest

from crypto_robot.core.models import Bar
from crypto_robot.lab.momentum_research import mondays, simulate, top_k, weeks
from crypto_robot.lab.momentum_variants import DAY_MS, all_variants, passes_dev, passes_holdout

MON = int(datetime(2021, 7, 5, tzinfo=timezone.utc).timestamp() * 1000)     # 週一


def coin(prices_by_day: dict[int, float], vol=10_000):
    return {MON + d * DAY_MS: Bar(MON + d * DAY_MS, D(str(p)), D(str(p)), D(str(p)), D(str(p)), D(vol))
            for d, p in prices_by_day.items()}


def test_mondays():
    ds = mondays(MON - 2 * DAY_MS, MON + 30 * DAY_MS)
    assert ds[0] == MON and ds[1] == MON + 7 * DAY_MS and ds[-1] + 8 * DAY_MS < MON + 30 * DAY_MS


def test_weeks_liquidity_momentum_and_return():
    days = range(-40, 10)
    up = coin({d: (200 if d >= 0 else 100) if d <= 1 else 220 for d in days})        # 回看 7 天 +100%；持有 +10%
    thin = coin({d: 100 for d in days}, vol=1)                                        # 成交額太小
    data = {'up': up, 'thin': thin, 'eth': coin({d: 100 for d in days})}
    w = weeks(data, 7, MON, MON + 10 * DAY_MS)
    assert len(w) == 1
    _, mom, ret = w[0]
    assert set(mom) == {'up'} and mom['up'] == pytest.approx(1.0) and ret['up'] == pytest.approx(0.1)


def test_simulate_turnover_cost_and_cash_weeks():
    w1 = (0, {'a': 0.5, 'b': 0.1, 'c': 0.0, 'd': -0.1}, {'a': 0.1, 'b': 0.0, 'c': 0.0, 'd': 0.0})
    w2 = (7, {'a': 0.0, 'b': 0.6, 'c': 0.0, 'd': -0.1}, {'a': 0.0, 'b': 0.2, 'c': 0.0, 'd': 0.0})
    w3 = (14, {'a': 0.0}, {'a': 0.0})                                                 # 少於 2K 個 → 現金
    out = simulate([w1, w2, w3], 1, top_k(1), 0.01)
    assert out[0] == (0, pytest.approx(0.1 - 0.01), True)                            # 買 a
    assert out[1] == (7, pytest.approx(0.2 - 0.02), True)                             # 賣 a、買 b
    assert out[2] == (14, pytest.approx(-0.01), False)                                # 賣 b，持有現金


def test_grid_and_criteria():
    assert [v.name for v in all_variants()] == ['mom-l7-top3', 'mom-l7-top5', 'mom-l30-top3', 'mom-l30-top5']
    assert passes_dev(100, 3.5, [0.01, 0.01], 0.97, 0.5, 0.3, -0.5, -0.6) == (True, [])
    assert len(passes_dev(50, 1.0, [-0.01, 0.0], 0.5, 0.1, 0.3, -0.7, -0.6)[1]) == 7
    assert passes_holdout(0.5, 0.3, 0.2, 0.95, -0.4, -0.5) == (True, [])
    assert len(passes_holdout(0.1, 0.3, 0.2, 0.5, -0.6, -0.5)[1]) == 4
