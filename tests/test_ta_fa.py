from datetime import datetime, timezone
from decimal import Decimal as Dec

from crypto_robot.core.models import Bar
from crypto_robot.lab import ta_fa as tf
from crypto_robot.lab.futures import Funding
from crypto_robot.lab.hourly import H, Series, ms

D, W = tf.D, tf.W
T0 = tf.mondays(ms(2022, 1, 1), ms(2022, 1, 31))[0]


def daily(prices, start):
    """每個台北日界前一根 1h K 線的收盤 = prices[i]（其他小時不放，price() 只看那一根）。"""
    return Series.of([Bar(start + i * D - H, *(Dec(str(p)),) * 4, Dec(1)) for i, p in enumerate(prices)])


def test_mondays_are_taipei_midnight():
    dt = datetime.fromtimestamp(T0 / 1000, timezone.utc)
    assert (dt.weekday(), dt.hour) == (6, 16)
    assert tf.mondays(T0, T0 + 3 * W) == [T0, T0 + W, T0 + 2 * W]


def test_ta_signals_follow_trend():
    up = daily([100 + i for i in range(200)], T0 - 199 * D)
    assert all(tf.ta_signal(n, up, T0) == 1 for n in tf.TA_VARIANTS)
    down = daily([300 - i for i in range(200)], T0 - 199 * D)
    assert all(tf.ta_signal(n, down, T0) == -1 for n in tf.TA_VARIANTS)
    assert tf.ta_signal('ma200', daily([100] * 199, T0 - 198 * D), T0) is None      # 少一天就不判斷


def test_weekly_charges_fee_only_on_position_change():
    eth = daily([100] * 40, T0)
    rows = tf.weekly([(T0, 1), (T0 + W, 1), (T0 + 2 * W, -1), (T0 + 3 * W, 0)], eth, Funding([]), 0.001)
    assert [round(x, 6) for _, _, x, _ in rows] == [-0.0005, 0, -0.001, -0.0005]
    fund = Funding([(T0 + 8 * H, 0.001)])
    assert tf.weekly([(T0, 1)], eth, fund, 0.0)[0][2] == -0.001 and tf.weekly([(T0, -1)], eth, fund, 0.0)[0][2] == 0.001


def test_fa_parts_signs_and_lag():
    days = range(-500, 10)
    rising = lambda k: tf.Macro([[T0 + i * D, 100.0 + k * i] for i in days])   # noqa: E731
    m = {'stablecoins': rising(1), 'WALCL': rising(10), 'WTREGEN': rising(1), 'RRPONTSYD': rising(0),
         'DTWEXBGS': rising(1), 'DFII10': rising(1), 'premium_ethusdt': tf.Macro([[T0 + i * D, 0.0001] for i in days])}
    fund = Funding([(T0 + i * 8 * H, 0.0001) for i in range(-1600, 20)])
    assert tf.fa_parts(m, fund, T0) == [0, 1, 1, -1, -1]
    assert tf.fa_signal(m, fund, T0) == 0
    m['DTWEXBGS'] = rising(-1)
    m['DFII10'] = rising(-1)
    assert tf.fa_signal(m, fund, T0) == 1
    late = tf.Macro([[T0 - 3 * D, 1.0]])
    assert late.asof(T0 - tf.FRED_LAG) is None        # 一週內才出現的值不能用


def test_qlike_and_pass_rules():
    assert tf.qlike(2.0, 2.0) == 0 and tf.qlike(1.0, 2.0) > tf.qlike(3.0, 2.0)     # 低估波動罰比較重
    good = {'net': 0.01, 't': 2.5, 'rand': 0.005, 'half': [0.01, 0.02], 'dd': -0.3, 'hold_dd': -0.6}
    assert tf.dev_passed(good)
    assert not tf.dev_passed(good | {'dd': -0.7}) and not tf.dev_passed(good | {'t': 1.9})
    assert tf.vol_passed({'n': 60, 'ewma': 1.0, 'base': 1.2, 'half': [[1.0, 1.1], [0.9, 1.3]]})
    assert not tf.vol_passed({'n': 60, 'ewma': 1.0, 'base': 1.2, 'half': [[1.2, 1.1], [0.9, 1.3]]})
