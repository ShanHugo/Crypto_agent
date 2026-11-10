"""第二十六輪（DESIGN 6.22）：手算的小例子。"""
from datetime import date, timedelta

import pytest

from crypto_robot.lab import usstock as us


def bdays(a: date, n: int) -> list[date]:
    out, d = [], a
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def stock(sym: str, days: list[date], closes: list[float], opens: list[float] | None = None, vol: float = 100.0) -> us.Stock:
    opens = opens or closes
    # [日期, 開, 高, 低, 收, 還原收, 量]；還原收 = 收 → 還原開 = 開
    return us.Stock(sym, [[d.isoformat(), o, o, o, c, c, vol] for d, o, c in zip(days, opens, closes)])


def test_adjusted_open_uses_adjclose_ratio():
    s = us.Stock('X', [['2020-01-02', 10.0, 11, 9, 20.0, 10.0, 5]])     # 還原收是收盤的一半（之後分割 1 拆 2）
    assert s.open[0] == 5.0 and s.close[0] == 10.0 and s.dv[0] == 100.0


def test_weeks_skip_holiday_monday():
    cal = [d for d in bdays(date(2024, 1, 1), 40) if d != date(2024, 1, 15)]   # 1/15 休市
    ws = us.weeks(cal, date(2024, 1, 8), date(2024, 1, 29))
    assert [(w.monday, w.decision, w.entry, w.exit) for w in ws] == [
        (date(2024, 1, 8), date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 16)),
        (date(2024, 1, 15), date(2024, 1, 12), date(2024, 1, 16), date(2024, 1, 22)),
        (date(2024, 1, 22), date(2024, 1, 19), date(2024, 1, 22), date(2024, 1, 29))]


def test_features_relative_to_spy():
    days = bdays(date(2020, 1, 1), 300)
    spy = stock('SPY', days, [100.0] * 300)
    closes = [100.0] * 300
    closes[-6] = 50.0                       # 5 天前 50 → 今天 100：1 週 +100%
    closes[-1] = 100.0
    f = us.features(stock('A', days, closes), spy, days[-1])
    assert f['ret_1w'] == pytest.approx(1.0)
    assert f['dist_high'] == pytest.approx(0.0)
    assert us.features(stock('A', days[:200], closes[:200]), spy, days[199]) is None   # 不到 252 天


def test_eligible_needs_one_year():
    days = bdays(date(2020, 1, 1), 400)
    w = us.weeks(days, days[300], days[320])[0]
    assert us.eligible(stock('A', days, [1.0] * 400), w)
    assert not us.eligible(stock('B', days[200:], [1.0] * 200), w)


def test_step_fee_turnover_and_funding():
    days = bdays(date(2024, 1, 1), 10)
    w = us.Week(date(2024, 1, 1), days[0], days[0], days[5])
    stocks = {'A': stock('A', days, [10.0] * 10, [10.0] * 5 + [11.0] * 5),     # 做多 +10%
              'B': stock('B', days, [10.0] * 10, [10.0] * 5 + [9.0] * 5)}      # 做空，跌 10% → +10%
    s = us.step(stocks, w, ['A'], ['B'], {}, 0.001)
    assert s['turnover'] == pytest.approx(1.0)
    assert s['ret'] == pytest.approx(0.5 * 0.1 + 0.5 * 0.1 - 0.001 - us.FUND_WEEK)
    s2 = us.step(stocks, w, ['A'], ['B'], {'A': 0.5, 'B': -0.5}, 0.001)    # 名單沒變 → 不收手續費
    assert s2['turnover'] == pytest.approx(0.0)


def test_select_top_and_bottom_fifth():
    longs, shorts = us.select({f's{i}': float(i) for i in range(10)})
    assert longs == ['s9', 's8'] and shorts == ['s1', 's0']


def test_beta_and_drawdown():
    assert us.beta([0.02, -0.02, 0.04], [0.01, -0.01, 0.02]) == pytest.approx(2.0)
    assert us.drawdown([1.0, 1.2, 0.9, 1.3]) == pytest.approx(0.9 / 1.2 - 1)


def test_blend_score_averages_model_ranks():
    uni = {'a': {}, 'b': {}, 'c': {}}
    preds = {'gbm': {date(2024, 1, 1): {'a': 3, 'b': 2, 'c': 1}}, 'ridge': {date(2024, 1, 1): {'a': 1, 'b': 2, 'c': 3}}}
    assert us.score_for('blend', uni, preds, date(2024, 1, 1)) == {'a': 0.0, 'b': 0.0, 'c': 0.0}
    assert us.score_for('gbm', uni, preds, date(2024, 1, 1))['a'] == 0.5


def test_checks():
    m = {'mean': 0.01, 't': 3.1, 'h1': 0.01, 'h2': 0.01, 'dd': -0.2, 'beta': 0.1}
    assert all(us.dev_checks(m, 0.005, 0.001).values())
    assert not us.dev_checks({**m, 'beta': 0.4}, 0.005, 0.001)['beta 在 ±0.3 內']
    assert not us.dev_checks({**m, 't': 2.9}, 0.005, 0.001)['t ≥ 3']
