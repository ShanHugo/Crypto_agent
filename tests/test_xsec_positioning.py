import math

import pytest

from crypto_robot.lab import xsec, xsec_positioning as xp
from crypto_robot.lab.hourly import ms

D = xsec.D
T = ms(2023, 3, 6)
MIN = 60_000


def raw(global_=1.5, top=2.0):
    return {'count_long_short_ratio': str(global_), 'sum_toptrader_long_short_ratio': str(top),
            'count_toptrader_long_short_ratio': '1.1', 'sum_open_interest_value': '1000'}


def test_snapshot_never_uses_rows_closer_than_15_minutes_even_when_unordered():
    rows = [(T - 5 * MIN, raw(9)), (T - 15 * MIN, raw(2)), (T - 10 * MIN, raw(9)), (T, raw(9)), (T - 20 * MIN, raw(3)), (T + 5 * MIN, raw(9))]
    when, v = xp.snapshot(rows, T)
    assert when == T - 15 * MIN and v['global'] == 2.0 and v['top'] == 2.0 and v['oi'] == 1000.0


def test_snapshot_ignores_rows_older_than_six_hours():
    assert xp.snapshot([(T - 6 * 60 * MIN - 1, raw())], T) is None
    assert xp.snapshot([(T - 6 * 60 * MIN, raw())], T)[0] == T - 6 * 60 * MIN
    assert xp.snapshot([], T) is None


def test_snapshot_blank_or_bad_values_are_none():
    r = {**raw(), 'sum_toptrader_long_short_ratio': '', 'count_long_short_ratio': '0', 'sum_open_interest_value': 'nan'}
    _, v = xp.snapshot([(T - 15 * MIN, r)], T)
    assert v['top'] is None and v['global'] is None and v['oi'] is None and v['top_acc'] == 1.1


def test_read_metrics_skips_missing_files(tmp_path):
    p = tmp_path / 'm.csv'
    p.write_text('t,sym,status,create_time,global,top,top_acc,oi\n'
                 f'{T},A,ok,{T - 15 * MIN},1.5,,1.1,1000\n{T},B,no_file,,,,,\n{T},C,no_row,,,,,\n', encoding='utf-8')
    assert xp.read_metrics(p) == {T: {'A': {'global': 1.5, 'top': None, 'top_acc': 1.1, 'oi': 1000.0}}}


SNAP = {'A': {'global': 1.0, 'top': 2.0}, 'B': {'global': 2.0, 'top': 2.0}, 'C': {'global': 4.0, 'top': 1.0}, 'D': {'global': None, 'top': 1.0}, 'E': {'global': 1.0, 'top': None}}


def test_crowd_score_is_contrarian_by_hand():
    s = xp.crowd_score(['A', 'B', 'C', 'D', 'F'], SNAP)
    assert s == {'A': pytest.approx(0.0), 'B': pytest.approx(-math.log(2)), 'C': pytest.approx(-math.log(4))}


def test_whale_score_by_hand_and_needs_both():
    s = xp.whale_score(['A', 'B', 'C', 'D', 'E'], SNAP)
    assert s == {'A': pytest.approx(math.log(2)), 'B': pytest.approx(0.0), 'C': pytest.approx(math.log(1 / 4))}


def test_blend_is_mean_of_four_ranks_by_hand():
    # 3 個幣排名 -0.5、0、+0.5。動能在 panel 的第二欄。
    uni = {'A': (0.0, 3.0), 'B': (0.0, 2.0), 'C': (0.0, 1.0)}
    gbm = {'A': 1.0, 'B': 2.0, 'C': 3.0}
    ridge = {'A': 2.0, 'B': 1.0, 'C': 3.0}
    s = xp.score_for('blend_crowd', uni, SNAP, gbm, ridge)
    # crowd：A 最高 +0.5、B 0、C -0.5
    assert s['A'] == pytest.approx((-0.5 + 0.0 + 0.5 + 0.5) / 4)
    assert s['B'] == pytest.approx((0.0 - 0.5 + 0.0 + 0.0) / 4)
    assert s['C'] == pytest.approx((0.5 + 0.5 - 0.5 - 0.5) / 4)
    assert set(xp.score_for('blend_whale', {**uni, 'D': (0.0, 0.0)}, SNAP, {**gbm, 'D': 0.0}, {**ridge, 'D': 0.0})) == {'A', 'B', 'C'}


def names(n=25):
    return [f'C{i:02d}' for i in range(n)]


def book_inputs(n=25):
    uni = {s: (0.0, float(i)) for i, s in enumerate(names(n))}
    snap = {s: {'global': float(i + 1), 'top': 100.0, 'oi': 10.0} for i, s in enumerate(names(n))}
    return uni, snap


def test_picks_directions():
    uni, snap = book_inputs()
    _, longs, shorts = xp.picks_for('crowd', {T: uni}, {T: snap}, {}, 'mon', [T])[0]
    assert longs[0] == 'C00' and shorts[-1] == 'C24' and len(longs) == len(shorts) == 5
    _, longs, shorts = xp.picks_for('whale', {T: uni}, {T: snap}, {}, 'mon', [T])[0]
    assert longs[0] == 'C00' and shorts[-1] == 'C24'    # 大戶固定，全體比越低比值越高


def test_picks_skips_week_with_too_few_values():
    uni, snap = book_inputs()
    few = {s: snap[s] for s in names(19)}
    assert xp.picks_for('crowd', {T: uni}, {T: few}, {}, 'mon', [T]) == []
    assert xp.picks_for('crowd', {T: uni}, {}, {}, 'mon', [T]) == []
    preds = {('gbm', 'mon'): {T: {s: 1.0 for s in names(19)}}, ('ridge', 'mon'): {T: {s: 1.0 for s in names()}}}
    assert xp.picks_for('blend_crowd', {T: uni}, {T: snap}, preds, 'mon', [T]) == []


def test_shuffled_picks_same_names_reproducible_and_differ_from_real():
    uni, snap = book_inputs()
    a = xp.shuffled_picks({T: uni}, {T: snap}, [T])
    assert a == xp.shuffled_picks({T: uni}, {T: snap}, [T])
    assert a != xp.picks_for('whale', {T: uni}, {T: snap}, {}, 'mon', [T])
    _, longs, shorts = a[0]
    assert len(longs) == len(shorts) == 5 and not set(longs) & set(shorts)


def test_coverage_and_run_dev_refuses_below_95_percent():
    uni, snap = book_inputs()
    c = xp.coverage({T: uni}, {T: {s: snap[s] for s in names(20)}}, [T])
    assert c['rate'] == pytest.approx(0.8) and c['have'] == 20 and c['total'] == 25 and c['worst_t'] == T
    with pytest.raises(ValueError):
        xp.run_dev({}, {'mon': {}, 'thu': {}}, {}, {})


def test_dev_window_is_104_mondays():
    ts = xsec.weeks(*xp.DEV, 'mon')
    assert len(ts) == 104 and ts[0] == ms(2023, 1, 2) and ts[-1] == ms(2024, 12, 23)     # 結束日不含，同之前每一輪


def m(**kw):
    return {'weeks': 100, 'mean': 0.01, 't': 3.5, 'h1': 0.01, 'h2': 0.01, 'dd': -0.2, 'drop_top': 0.01, **kw}


def test_dev_checks_uses_90_weeks_keeps_the_other_eight():
    c = xp.dev_checks(m(), 0.005, 0.005, 0.005)
    assert all(c.values()) and len(c) == 9 and '有持倉 ≥ 90 週' in c
    assert not xp.dev_checks(m(weeks=89), 0.005, 0.005, 0.005)['有持倉 ≥ 90 週']
    assert not xp.dev_checks(m(t=2.99), 0.005, 0.005, 0.005)['t ≥ 3']


def test_beats_blend():
    base = {'t': 1.5, 'dd': -0.2}
    assert xp.beats_blend({'t': 1.6, 'dd': -0.2}, base)
    assert not xp.beats_blend({'t': 1.5, 'dd': -0.1}, base)
    assert not xp.beats_blend({'t': 2.0, 'dd': -0.21}, base)


def test_oi_change_by_hand():
    uni = {'A': (0, 0), 'B': (0, 0), 'C': (0, 0)}
    snaps = {T: {'A': {'oi': 110.0}, 'B': {'oi': None}, 'C': {'oi': 5.0}}, T - 7 * D: {'A': {'oi': 100.0}, 'B': {'oi': 1.0}}}
    assert xp.oi_change({T: uni}, snaps, [T]) == {T: {'A': pytest.approx(0.1)}}
