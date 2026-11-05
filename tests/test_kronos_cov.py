import csv
from datetime import datetime, timezone
from decimal import Decimal as Dec

from crypto_robot.core.models import Bar
from crypto_robot.lab import kronos_cov as kc
from crypto_robot.lab.futures import FEE, Funding
from crypto_robot.lab.hourly import H, Series, ms
from crypto_robot.lab.ta_fa import Macro

D, W = kc.D, kc.W
T = ms(2022, 3, 1)


def macro(slope):
    return Macro([[T + i * D, 100.0 + slope * i] for i in range(-100, 10)])


def env():
    m = {n: macro(1) for n in ('stablecoins', 'WALCL', 'WTREGEN', 'DTWEXBGS', 'DFII10')} | {'RRPONTSYD': macro(0)}
    prem = Macro([[T + i * H, 0.001 * i] for i in range(-10, 10)])
    fund = Funding([(T + i * 8 * H, 0.0001 * i) for i in range(-30, 10)])
    return fund, prem, m


def test_raw_covs_only_use_known_data():
    fund, prem, m = env()
    r = kc.raw_covs(T, fund, prem, m)
    assert r[0] == 0.0                                       # 最後一期已結算費率：T 那期（T+1h 前）
    assert abs(r[1] - 0.0001 * sum(range(-20, 1)) / 21) < 1e-12   # (T+1h-7 天, T+1h] 的 21 期
    assert r[2] == 0.0                                       # 開盤 T 那根溢價 K
    now, old = T + H - W, T + H - 5 * W                      # 一週延遲、4 週變化
    lv = lambda ts: 100.0 + (ts // D * D - T) // D          # noqa: E731  每天一筆，取當天（含）以前
    assert abs(r[3] - (lv(now) / lv(old) - 1)) < 1e-12
    assert abs(r[6] - (lv(now) - lv(old))) < 1e-12
    assert kc.raw_covs(T + 30 * H, fund, prem, m) is None    # 溢價指數那根還沒有 → 不算


def test_standardize_uses_train_period_and_clips():
    rows = {1: [0.0], 2: [2.0], 3: [100.0], 4: None}
    z, stats = kc.standardize(rows, 1, 3)
    assert stats == [(1.0, 1.0)] and z[1] == [-1.0] and z[2] == [1.0] and z[3] == [kc.CLIP] and z[4] is None


def test_write_csv(tmp_path):
    fund, prem, m = env()
    m['premium_ethusdt_1h'] = prem
    eth = Series.of([Bar(T + i * H, *(Dec(100),) * 4, Dec(1)) for i in range(3)])
    info = kc.write_csv(tmp_path / 'eth.csv', eth, fund, m)
    rows = list(csv.reader((tmp_path / 'eth.csv').open(encoding='utf-8')))
    assert rows[0][:6] == ['ts', 'open', 'high', 'low', 'close', 'volume'] and len(rows) == 4 and info['missing'] == 0


def test_days_are_taipei_midnight_and_direction():
    d = kc.days(ms(2024, 7, 1), ms(2024, 7, 3))
    assert [datetime.fromtimestamp(x / 1000, timezone.utc).hour for x in d] == [16, 16]
    assert kc.direction([FEE * 2] * 30) == 1 and kc.direction([-FEE * 2] * 30) == -1 and kc.direction([0.0] * 30) == 0


def test_simulate_and_k2_must_beat_k1():
    t0 = kc.days(T, T + D)[0]
    eth = Series.of([Bar(t0 - H + i * H, *(Dec(str(100 + i)),) * 4, Dec(1)) for i in range(30)])
    trades, pool = kc.simulate({t0: [0.01] * 30}, eth, Funding([]), t0, t0 + D)
    assert trades == [(t0, 1, 124 / 100 - 1 - FEE)] and len(pool[1]) == 1
    good = {'n': 120, 'net': 0.002, 't': 2.5, 'rand': 0.001, 'half': [0.001, 0.003]}
    assert kc.dev_passed(good) and kc.dev_passed(good, {'net': 0.001}) and not kc.dev_passed(good, {'net': 0.003})
    assert not kc.dev_passed(good | {'n': 99})
