import pytest

from crypto_robot.lab import xsec, xsec_nohv as nv
from crypto_robot.lab.hourly import ms

D = xsec.D
T = ms(2023, 3, 6)


def names(n=50):
    return [f'C{i:02d}' for i in range(n)]


def test_barred_top_20_percent_ties_by_symbol():
    vols = {s: float(i) for i, s in enumerate(names())}
    assert nv.barred(vols) == {f'C{i}' for i in range(40, 50)}
    tied = {s: 0.0 for s in names(5)}
    assert nv.barred(tied) == {'C00'}


def test_select_longs_unrestricted_shorts_skip_high_vol():
    score = {s: float(i) for i, s in enumerate(names())}        # C49 分數最高、C00 最低
    vols = {s: (1.0 if i < 10 else 0.0) for i, s in enumerate(names())}    # 分數最低的 10 個波動也最高
    vols |= {s: 0.0 for s in names()[10:]}
    longs, shorts = nv.select(score, vols)
    assert longs[0] == 'C49' and len(longs) == 10
    assert sorted(shorts) == [f'C{i}' for i in range(10, 20)]   # 跳過 C00～C09，往上取 10 個


def test_select_high_vol_can_still_be_long():
    score = {s: float(i) for i, s in enumerate(names())}
    vols = {s: (1.0 if i >= 40 else 0.0) for i, s in enumerate(names())}   # 分數最高的 10 個波動也最高
    longs, shorts = nv.select(score, vols)
    assert 'C49' in longs and sorted(shorts) == [f'C{i:02d}' for i in range(10)]


@pytest.mark.parametrize('n', [20, 22, 50])
def test_select_no_overlap_and_right_size(n):
    score = {s: float(i) for i, s in enumerate(names(n))}
    vols = {s: float(n - i) for i, s in enumerate(names(n))}
    longs, shorts = nv.select(score, vols)
    k = round(n * 0.2)
    assert len(longs) == k and len(shorts) == k and not set(longs) & set(shorts)
    assert not set(shorts) & nv.barred(vols)


def test_picks_for_uses_vol_of_the_same_week_and_ignores_outside():
    panel = {T: {s: (0.0, 0.0) for s in names(25)}}
    vols = {T: {s: float(25 - i) for i, s in enumerate(names(25))}}       # C00～C04 波動最高，不准放空
    preds = {('gbm', 'mon'): {T: {**{s: float(i) for i, s in enumerate(names(25))}, 'OUT': -99.0}}}
    t, longs, shorts = nv.picks_for('gbm', panel, preds, vols, 'mon', [T])[0]
    assert t == T and sorted(shorts) == [f'C{i:02d}' for i in range(5, 10)]


def bars_from(t, closes, opens=None):
    return [[t + i * D, (opens or closes)[i], x, 100.0, 50.0] for i, x in enumerate(closes)]


def small_book():
    flat = bars_from(T - D, [100.0]) + bars_from(T, [100.0] * 7)
    pump = bars_from(T - D, [100.0]) + bars_from(T, [100.0, 160.0, 120.0, 120.0, 120.0, 120.0, 120.0], [100.0] * 7)
    return xsec.Book({'A': xsec.Coin('A', flat, []), 'B': xsec.Coin('B', flat, []), 'C': xsec.Coin('C', flat, []),
                      'H': xsec.Coin('H', pump, []), 'P': xsec.Coin('P', pump, [])})


def test_short_pump_rate_uses_any_day_close():
    picks = [(T, ['A'], ['B', 'P'])]
    assert nv.short_pump_rate(small_book(), picks) == pytest.approx(0.5)


def test_random_means_never_shorts_barred_coin_and_is_reproducible():
    book = small_book()
    panel = {T: {s: (0.0, 0.0) for s in 'ABCHP'}}
    vols = {T: {'A': 0.0, 'B': 0.0, 'C': 0.0, 'H': 1.0, 'P': 0.0}}         # 只有 H 不准放空
    seen = []
    orig = xsec.step

    def spy(b, t, longs, shorts, prev, cost):
        seen.append(shorts)
        return orig(b, t, longs, shorts, prev, cost)
    xsec.step = spy
    try:
        r = nv.random_means(book, panel, vols, [T], runs=100, seed=1)
    finally:
        xsec.step = orig
    assert r == nv.random_means(book, panel, vols, [T], runs=100, seed=1)
    assert all('H' not in s for s in seen) and any('P' in s for s in seen)


def test_worst_short_only_looks_at_shorts():
    rows = [{'t': T, 'by': {'A': -0.5, 'B': -0.1}}, {'t': T + 7 * D, 'by': {'B': -0.2}}]
    assert nv.worst_short(rows, {T: ['B'], T + 7 * D: ['B']}) == (-0.2, 'B', T + 7 * D)


def test_read_vols(tmp_path):
    p = tmp_path / 'p.csv'
    p.write_text('ts,sym,n,label,vol_28d\n100,A,2,0.5,-0.5\n100,B,2,-0.5,0.5\n', encoding='utf-8')
    assert nv.read_vols(p) == {100: {'A': -0.5, 'B': 0.5}}
