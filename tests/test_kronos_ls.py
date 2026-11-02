from decimal import Decimal as D

from crypto_robot.agents.data.resample import DAY_MS
from crypto_robot.core.models import Bar
from crypto_robot.forecast import kronos_ls as kl
from crypto_robot.lab.futures import FEE, Funding
from crypto_robot.lab.hourly import H, Series

TS = kl.START_MS


def eth(prices, start=TS - kl.LOOKBACK * H):
    return Series.of([Bar(start + i * H, D(str(p)), D(str(p)), D(str(p)), D(str(p)), D(1)) for i, p in enumerate(prices)])


def test_forecast_ts_is_taipei_2am():
    assert kl.forecast_ts(TS + 5 * H) == TS                  # 台北 07:00 → 當天 02:00
    assert kl.forecast_ts(TS - 3 * H) == TS - DAY_MS          # 台北 23:00 → 前一天 02:00（還沒換日）
    assert (TS + 8 * H) % DAY_MS == 2 * H


def test_model_input_needs_512_closed_bars():
    e = eth([100.0] * kl.LOOKBACK)
    req = kl.model_input(e, TS)
    assert len(req['close']) == kl.LOOKBACK and req['x_ms'][-1] == TS - H and req['y_ms'][0] == TS
    assert len(req['y_ms']) == 24 and req['seed'] == TS // H
    assert kl.model_input(eth([100.0] * (kl.LOOKBACK - 1)), TS) is None


def test_direction_threshold_is_futures_fee():
    assert kl.direction([FEE + 1e-6] * 30) == 1 and kl.direction([-FEE - 1e-6] * 30) == -1
    assert kl.direction([FEE] * 30) == 0


def test_open_forecast_once_per_day():
    f = kl.forecast_row(TS, 100.0, [0.01] * 30)
    assert kl.open_forecast([], [], TS + H)[0] is None
    got, _ = kl.open_forecast([f], [], TS + 7 * H)
    assert got is f
    j = kl.judgment_row(f, 'short', 101.0, TS + 7 * H, '')
    assert j['call'] == -1 and j['kronos'] == 1
    assert '已經記過' in kl.open_forecast([f], [j], TS + 8 * H)[1]
    assert kl.open_forecast([f], [], TS + DAY_MS)[0] is None   # 隔天 02:00 之後不能補記


def test_settle_three_tracks_with_funding():
    e = eth([100.0] * kl.LOOKBACK + [110.0] * 24)             # 02:00 進場 100，隔天 02:00 收 110
    fund = Funding([(TS + 8 * H, 0.001)])
    f = kl.forecast_row(TS, 100.0, [0.01] * 30)               # Kronos 看多
    j = kl.judgment_row(f, 'long', 105.0, TS + 7 * H, '')     # 營運者 09:00 用 105 做多
    trades, pool = kl.settle([f], [j], e, fund)
    assert abs(trades['kronos'][0]['net'] - (0.10 - 0.001 - FEE)) < 1e-12
    assert abs(trades['user'][0]['net'] - (110 / 105 - 1 - 0.001 - FEE)) < 1e-12
    assert trades['agree'] == trades['user']
    assert abs(pool[-1][0] - (-0.10 + 0.001 - FEE)) < 1e-12
    j2 = kl.judgment_row(f, 'short', 105.0, TS + 7 * H, '')
    trades, _ = kl.settle([f], [j2], e, fund)
    assert trades['agree'] == [] and trades['user'][0]['d'] == -1


def test_settle_skips_unfinished_and_before_start():
    e = eth([100.0] * (kl.LOOKBACK + 10))
    f = kl.forecast_row(TS, 100.0, [0.01] * 30)
    assert kl.settle([f], [], e, Funding([]))[0]['kronos'] == []
    early = kl.forecast_row(TS - DAY_MS, 100.0, [0.01] * 30)
    e2 = eth([100.0] * (kl.LOOKBACK + 48), start=TS - DAY_MS - kl.LOOKBACK * H)
    assert kl.settle([early], [], e2, Funding([]))[1] == {1: [], -1: []}


def test_status_checkpoints():
    pool = {1: [0.0], -1: [0.0]}
    rows = [{'d': 1, 'net': 0.002}] * 59
    assert 'stopped' not in kl.status(rows, pool)
    assert kl.status(rows + rows[:1], pool)['stopped'] is False
    assert kl.status(rows + rows[:1], {1: [0.003], -1: [0.0]})['stopped'] is True
    good = [{'d': 1, 'net': 0.004 + (0.001 if i % 2 else -0.001)} for i in range(150)]
    assert kl.status(good, pool)['final_pass'] is True
    assert '距中途檢查還差 50 筆' in kl.track_text('user', kl.status(rows[:10], pool))


def test_morning_line():
    f = kl.forecast_row(TS, 100.0, [0.01] * 22 + [-0.01] * 8)
    assert '看多' in kl.morning_line([f], [], TS + 6 * H) and 'judge.py' in kl.morning_line([f], [], TS + 6 * H)
    j = kl.judgment_row(f, 'flat', 100.0, TS + 7 * H, '')
    assert '已記錄' in kl.morning_line([f], [j], TS + 8 * H)
    assert '沒有預測' in kl.morning_line([], [], TS + 6 * H)
