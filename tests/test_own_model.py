import csv
from datetime import datetime, timezone
from decimal import Decimal as Dec

from crypto_robot.core.models import Bar
from crypto_robot.lab import own_model as om
from crypto_robot.lab.futures import FEE, Funding
from crypto_robot.lab.hourly import H, Series, ms
from crypto_robot.lab.ta_fa import Macro

D, W = om.D, om.W
T = ms(2022, 3, 1) + 16 * H


def series(price=lambda i: 100 + i * 0.01, start=T - 40 * D, n=45 * 24, end=None):
    bars = []
    for i in range(n):
        ts = start + i * H
        if end is not None and ts >= end:
            break
        p = Dec(str(price(i)))
        bars.append(Bar(ts, p, p + 1, p - 1, p, Dec(1 + i % 3)))
    return Series.of(bars)


def env(end=None):
    def macro(slope):
        return Macro([[T - T % D + i * D, 100.0 + slope * i] for i in range(-100, 10) if end is None or T - T % D + i * D <= end])
    m = {n: macro(1) for n in ('stablecoins', 'WALCL', 'WTREGEN', 'DTWEXBGS', 'DFII10')} | {'RRPONTSYD': macro(0)}
    prem = Macro([[T + i * H, 0.001 * i] for i in range(-10, 40) if end is None or T + i * H < end])
    fund = Funding([(T + i * 8 * H, 0.0001 * i) for i in range(-30, 10) if end is None or T + i * 8 * H <= end])
    return fund, prem, m


def test_features_hand_values():
    eth, btc = series(), series(lambda i: 200 + i)
    fund, prem, m = env()
    x = dict(zip(om.FEATURES, om.features(T, eth, btc, fund, prem, m)))
    i = eth.idx[T - H]
    assert x['ret_1d'] == eth.c[i] / eth.c[i - 24] - 1 and x['ret_28d'] == eth.c[i] / eth.c[i - 28 * 24] - 1
    assert abs(x['range_1d'] - (eth.h[i] - eth.l[i - 23]) / eth.c[i]) < 1e-12
    j = btc.idx[T - H]
    assert x['btc_7d'] == btc.c[j] / btc.c[j - 7 * 24] - 1
    assert x['fund_last'] == 0.0                                          # T 那期已結算
    assert abs(x['fund_7d'] - 0.0001 * sum(range(-20, 1)) / 21) < 1e-12   # (T-7 天, T] 的 21 期
    assert x['premium'] == -0.001                                         # 開盤 T-1h 那根，T 才收盤
    lv = lambda ts: 100.0 + (ts // D * D - (T - T % D)) // D              # noqa: E731
    now, old = T - W, T - 5 * W
    assert abs(x['stable_4w'] - (lv(now) / lv(old) - 1)) < 1e-12 and abs(x['real_4w'] - (lv(now) - lv(old))) < 1e-12


def test_features_do_not_peek():
    """把 T 之後的資料全部刪掉，T 的特徵必須一模一樣。"""
    fund, prem, m = env()
    full = om.features(T, series(), series(lambda i: 200 + i), fund, prem, m)
    fund2, prem2, m2 = env(end=T)
    cut = om.features(T, series(end=T), series(lambda i: 200 + i, end=T), fund2, prem2, m2)
    assert full is not None and full == cut


def test_features_none_when_data_missing():
    eth, btc = series(), series(lambda i: 200 + i)
    fund, prem, m = env()
    assert om.features(T - 20 * D, eth, btc, fund, prem, m) is None       # 不夠 28 天
    assert om.features(T + 41 * H, eth, btc, fund, prem, m) is None       # 溢價指數那根還沒有
    assert om.label(T, eth) == eth.c[eth.idx[T + 23 * H]] / eth.c[eth.idx[T - H]] - 1
    assert om.label(T + 200 * D, eth) is None


def test_write_csv(tmp_path):
    fund, prem, m = env()
    m['premium_ethusdt_1h'] = prem
    info = om.write_csv(tmp_path / 'f.csv', series(), series(lambda i: 200 + i), fund, m)
    rows = list(csv.reader((tmp_path / 'f.csv').open(encoding='utf-8')))
    assert rows[0] == ['ts', 'ret'] + om.FEATURES and len(rows) == info['rows'] + 1
    good = [r for r in rows[1:] if r[2]]
    assert len(good) == info['rows'] - info['missing'] and good[0][0] == str(T)


def test_days_direction_simulate():
    assert [datetime.fromtimestamp(x / 1000, timezone.utc).hour for x in om.days(ms(2024, 7, 1), ms(2024, 7, 3))] == [16, 16]
    assert om.direction(FEE * 2) == 1 and om.direction(-FEE * 2) == -1 and om.direction(FEE) == 0
    eth = series(lambda i: 100 + i, start=T - H, n=30)
    trades, pool = om.simulate({T: 0.01}, eth, Funding([]), T, T + D)
    assert trades == [(T, 1, 124 / 100 - 1 - FEE)] and len(pool[1]) == 1


def test_pass_rules_and_pick():
    good = {'n': 120, 'n_short': 60, 'net': 0.002, 't': 3.5, 'rand': 0.001, 'half': [0.001, 0.003]}
    assert om.dev_passed(good)
    assert not om.dev_passed(good | {'t': 2.9})
    assert not om.dev_passed(good | {'n': 99})
    assert not om.dev_passed(good | {'net': 0.0009, 'rand': 0.0})          # 成本再加 0.10% 就賠
    assert not om.dev_passed(good | {'half': [-0.001, 0.003]})
    assert om.exam_passed(good | {'t': 0.5}) and not om.exam_passed(good | {'net': 0.0005})
    assert om.leaked({'t': -3.1}) and not om.leaked({'t': 2.9}) and not om.leaked({'n': 1})
