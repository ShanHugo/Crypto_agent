import json
from decimal import Decimal as D

import pytest

from crypto_robot.agents.data.resample import DAY_MS, HOUR_MS, day_start, to_daily
from crypto_robot.core.models import Bar
from crypto_robot.lab.backtest import CostParams, buy_and_hold
from crypto_robot.lab.common import window
from crypto_robot.lab.metrics import max_drawdown, segment_return, t_stat
from crypto_robot.lab.rebalance_research import run_holdout
from crypto_robot.lab.rebalance_variants import by_name

NO_COST = CostParams(D(0), D(0))
DAY0 = 1_700_000_000_000 // DAY_MS * DAY_MS - 8 * HOUR_MS    # 某個台北日 00:00


def flat(i, price):
    p = D(str(price))
    return Bar(DAY0 + i * DAY_MS, p, p, p, p, D(1))


# ---- 日 K 合成 ----

def test_day_boundary_is_taipei_midnight():
    assert day_start(DAY0 + 5 * HOUR_MS) == DAY0
    assert day_start(DAY0 - 1) == DAY0 - DAY_MS


def test_to_daily_aggregates_and_drops_incomplete_days():
    hours = [Bar(DAY0 - 2 * HOUR_MS + i * HOUR_MS, D(10 + i), D(20 + i), D(5 + i), D(11 + i), D(2))
             for i in range(2 + 24 + 3)]                   # 前一天只有 2 根、完整 1 天、下一天只有 3 根
    [d] = to_daily(hours)
    assert d.ts == DAY0
    assert (d.open, d.close) == (D(12), D(11 + 25))
    assert (d.high, d.low, d.volume) == (D(20 + 25), D(7), D(48))


# ---- 基準與統計 ----

def test_buy_and_hold_and_metrics():
    bars = [flat(i, p) for i, p in enumerate([10, 10, 20, 15])]
    res = buy_and_hold(bars, bars[0].ts, bars[-1].ts + DAY_MS, NO_COST, initial=D(1000))
    assert res.equity[-1][1] == D(1500)
    assert max_drawdown([float(v) for _, v in res.equity]) == pytest.approx(-0.25)
    assert segment_return(res, bars[2].ts, bars[-1].ts + DAY_MS) == pytest.approx(0.5)


def test_t_stat():
    assert t_stat([0.01]) == 0.0
    assert t_stat([0.01, 0.03]) == pytest.approx(2.0)
    assert t_stat([-0.5] * 5) == float('-inf')


def test_window_rejects_gaps_and_short_warmup():
    hour = [Bar(DAY0 + i * HOUR_MS, D(1), D(1), D(1), D(1), D(1)) for i in range(10)]
    assert len(window(hour, DAY0 + 2 * HOUR_MS, DAY0 + 10 * HOUR_MS, 2)) == 10
    with pytest.raises(RuntimeError):
        window(hour, DAY0 + 2 * HOUR_MS, DAY0 + 10 * HOUR_MS, 5)          # 暖機不夠
    with pytest.raises(RuntimeError):
        window(hour[:4] + hour[5:], DAY0 + 2 * HOUR_MS, DAY0 + 10 * HOUR_MS, 2)   # 有缺漏


# ---- 考卷只考一次 ----

def test_holdout_runs_once(tmp_path, monkeypatch):
    path = tmp_path / 'holdout.json'
    path.write_text(json.dumps({'variant': 'x', 'passed': True}), encoding='utf-8')
    monkeypatch.setattr('crypto_robot.lab.rebalance_research.evaluate', lambda *a: pytest.fail('不應重算'))
    assert run_holdout([], by_name('rebal-eth50-band10'), 0, path, 'abc')['variant'] == 'x'
