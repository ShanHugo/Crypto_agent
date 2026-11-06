import pytest

from crypto_robot.lab import xsec, xsec3
from crypto_robot.lab.hourly import ms

D = xsec.D
T = ms(2023, 3, 6)


def bars_from(t, closes, opens=None):
    return [[t + i * D, (opens or closes)[i], x, 100.0, 50.0] for i, x in enumerate(closes)]


def test_periods_anchor_and_bounds():
    ts = xsec3.periods(*xsec.DEV)
    assert ts[0] == xsec.DEV[0] and ts[-1] + xsec3.STEP == xsec.DEV[1] and len(ts) == 364
    assert all(b - a == 3 * D for a, b in zip(ts, ts[1:]))
    later = xsec3.periods(*xsec.DEV, 's1')
    assert later[0] == xsec.DEV[0] + D and later[-1] + xsec3.STEP <= xsec.DEV[1]
    ref = xsec3.periods(*xsec.EXAM)
    assert ref[0] >= xsec.EXAM[0] and ref[-1] + xsec3.STEP <= xsec.END


def test_periods_before_anchor_stay_on_the_same_grid():
    ts = xsec3.periods(xsec.FIRST_MONDAY, xsec.DEV[0] + D)
    assert (xsec3.ANCHOR - ts[0]) % xsec3.STEP == 0 and ts[-1] == xsec.DEV[0] - xsec3.STEP


def test_hold_three_days_and_funding_on_position_size():
    k = bars_from(T - D, [100.0]) + bars_from(T, [110.0, 120.0, 150.0, 999.0], [100.0] * 4)
    f = [[T, 0.01], [T + D, 0.02], [T + 3 * D, 0.5]]           # 第 4 天的費率不算
    h = xsec3.hold(xsec.Coin('A', k, f), T)
    assert h.ratio == pytest.approx(1.5) and not h.delisted and len(h.path) == 3
    assert h.fund == pytest.approx(0.01 + 1.1 * 0.02)


def test_hold_delisted_inside_three_days():
    k = bars_from(T - D, [100.0]) + bars_from(T, [50.0], [100.0])
    h = xsec3.hold(xsec.Coin('G', k, []), T)
    assert h.delisted and h.ratio == pytest.approx(0.5)


def test_hold_last_bar_on_final_day_is_not_delisted_yet():
    k = bars_from(T - D, [100.0]) + bars_from(T, [100.0, 100.0, 120.0], [100.0] * 3)
    h = xsec3.hold(xsec.Coin('A', k, []), T)
    assert not h.delisted and h.ratio == pytest.approx(1.2)


def test_label_is_three_day_return_minus_funding():
    k = bars_from(T - D, [100.0]) + bars_from(T, [110.0, 110.0, 110.0, 500.0], [100.0] * 4)
    assert xsec3.label(xsec.Coin('A', k, [[T, 0.01]]), T) == pytest.approx(0.1 - 0.01)


def book():
    a = bars_from(T - D, [100.0]) + bars_from(T, [50.0, 100.0, 110.0], [100.0] * 3)
    b = bars_from(T - D, [100.0]) + bars_from(T, [100.0, 100.0, 90.0], [100.0] * 3)
    return xsec3.Book({'A': xsec.Coin('A', a, []), 'B': xsec.Coin('B', b, [])})


def test_simulate_three_daily_marks_and_hand_return():
    sim = xsec3.simulate(book(), [(T, ['A'], ['B'])], 0.001)
    # 多 A：0.5 × 10%；空 B：−0.5 × −10%；換手 1 倍 × 0.1%
    assert sim['rows'][0]['ret'] == pytest.approx(0.1 - 0.001) and len(sim['marks']) == 4
    assert sim['marks'][1] == pytest.approx(1 - 0.001 - 0.25) and xsec.drawdown(sim['marks']) == pytest.approx(-0.251)


def good(**kw):
    return {'weeks': 364, 'mean': 0.01, 't': 3.5, 'h1': 0.01, 'h2': 0.01, 'dd': -0.2, 'drop_top': 0.005} | kw


@pytest.mark.parametrize('bad', [{'weeks': 299}, {'mean': 0.0}, {'t': 2.99}, {'h1': -0.001}, {'dd': -0.31}, {'drop_top': 0.0}])
def test_dev_checks_each_fails(bad):
    assert all(xsec3.dev_checks(good(), 0.005, 0.001, 0.001).values())
    assert not all(xsec3.dev_checks(good(**bad), 0.005, 0.001, 0.001).values())


def test_dev_checks_nine_and_other_inputs():
    assert len(xsec3.dev_checks(good(), 0.005, 0.001, 0.001)) == 9
    assert not all(xsec3.dev_checks(good(), 0.02, 0.001, 0.001).values())
    assert not all(xsec3.dev_checks(good(), 0.005, -0.001, 0.001).values())
    assert not all(xsec3.dev_checks(good(), 0.005, 0.001, -0.001).values())


def test_write_panel_uses_three_day_label_and_grid(tmp_path):
    start = xsec3.periods(xsec.FIRST_MONDAY, xsec.END)[0]
    coins = {}
    for i in range(22):
        k = [[start + j * D, 10.0 + i, 10.0 + i + j * (i + 1) * 0.01, 1000.0 + i, 500.0] for j in range(90)]
        coins[f'C{i:02d}USDT'] = xsec.Coin(f'C{i:02d}USDT', k, [])
    p = tmp_path / 'panel.csv'
    info = xsec3.write_panel(p, coins, 's0')
    panel = xsec.read_panel(p)
    t = min(panel)
    assert info['periods'] == len(panel) and (t - start) % xsec3.STEP == 0 and t >= start + 60 * D
    assert all(len(v) == 22 for v in panel.values())
    assert panel[t]['C21USDT'][1] == 0.5


def test_future_data_cannot_change_the_panel_row_features(tmp_path):
    """防偷看：把判斷日之後的資料刪掉，特徵必須一模一樣（只有答案會變）。"""
    def build(cut=None):
        out = {}
        for i in range(25):
            k = [[T - 100 * D + j * D, 10.0 + i + j % 7, 10.0 + i + (j * (i + 3)) % 11, 1000.0 + i * (j % 5 + 1), 400.0 + j] for j in range(130)]
            if cut is not None:
                k = [b for b in k if b[0] < cut]
            out[f'C{i:02d}USDT'] = xsec.Coin(f'C{i:02d}USDT', k, [])
        return out
    full, cut = build(), build(T)
    mf, mc = xsec.market_returns(full), xsec.market_returns(cut)
    for c in xsec.universe(full, T):
        assert c.features(T, mf) == cut[c.sym].features(T, mc)
