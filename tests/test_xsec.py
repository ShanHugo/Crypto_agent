import pytest

from crypto_robot.lab import xsec
from crypto_robot.lab.hourly import ms

D, W = xsec.D, xsec.W
T = ms(2023, 3, 6)              # 週一


def coin(sym='AAAUSDT', days=100, end=T, price=lambda i: 100.0 + i, qv=1000.0, fund=None, start=None):
    """從 end 往前 days 根日 K（最後一根開盤在 end + extra 由 start 決定）。"""
    t0 = start if start is not None else end - days * D
    k = [[t0 + i * D, price(i), price(i), qv, qv / 2] for i in range(days)]
    return xsec.Coin(sym, k, fund or [])


def test_rank_ties_and_missing():
    assert xsec.rank([1.0, 2.0, 3.0]) == [-0.5, 0.0, 0.5]
    assert xsec.rank([1.0, 1.0, 3.0]) == [-0.25, -0.25, 0.5]
    assert xsec.rank([1.0, None, 3.0]) == [-0.5, 0.0, 0.5]
    assert xsec.rank([None, 2.0]) == [0.0, 0.0]


def test_weeks_inside_period():
    ts = xsec.weeks(*xsec.DEV)
    assert ts[0] == xsec.DEV[0] and ts[-1] + W == xsec.DEV[1] and len(ts) == 156
    th = xsec.weeks(*xsec.DEV, 'thu')
    assert th[0] == xsec.DEV[0] + 3 * D and th[-1] + W <= xsec.DEV[1]
    ex = xsec.weeks(*xsec.EXAM)
    assert ex[0] == ms(2025, 1, 6) and ex[-1] + W <= xsec.END and ex[-1] == ms(2026, 10, 19)


def test_eligible_needs_60_days_and_sunday_bar():
    assert coin(days=60).eligible(T)
    assert not coin(days=59).eligible(T)
    assert not coin(days=80, end=T - D).eligible(T)            # 週日沒有 K 線：當時已經不在交易


def test_gap_over_7_days_is_a_new_coin():
    k = [[T - 200 * D + i * D, 10.0, 10.0, 1.0, 0.5] for i in range(100)]
    k += [[T - 50 * D + i * D, 10.0, 10.0, 1.0, 0.5] for i in range(50)]      # 中斷 50 天後重新上市，只有 50 天
    c = xsec.Coin('RELISTUSDT', k, [])
    assert c.pos[T - D] == 149 and c.seg[149] == T - 50 * D and not c.eligible(T)
    assert c.r[100] is None


def test_universe_top_by_median_volume_ties_by_symbol():
    coins = {f'C{i:02d}USDT': coin(f'C{i:02d}USDT', qv=1000.0 + i) for i in range(xsec.TOP + 5)}
    coins['ZEROUSDT'] = coin('ZEROUSDT', days=30)               # 上市不足
    uni = [c.sym for c in xsec.universe(coins, T)]
    assert len(uni) == xsec.TOP and uni[0] == f'C{xsec.TOP + 4:02d}USDT' and 'C00USDT' not in uni


def test_point_in_time_future_data_cannot_change_universe_or_features():
    """防偷看：把 t 之後的資料刪掉，選幣池與特徵必須一模一樣。"""
    def build(cut=None):
        out = {}
        for i in range(25):
            k = [[T - 100 * D + j * D, 10.0 + i + j % 7, 10.0 + i + (j * (i + 3)) % 11, 1000.0 + i * (j % 5 + 1), 400.0 + j] for j in range(130)]
            f = [[T - 100 * D + j * 8 * 3_600_000, 0.0001 * (1 + j % 3)] for j in range(390)]
            if cut is not None:
                k, f = [b for b in k if b[0] < cut], [x for x in f if x[0] < cut]
            out[f'C{i:02d}USDT'] = xsec.Coin(f'C{i:02d}USDT', k, f)
        return out
    full, cut = build(), build(T)
    assert [c.sym for c in xsec.universe(full, T)] == [c.sym for c in xsec.universe(cut, T)]
    # 大盤報酬也只取 t 以前的日子
    mf, mc = xsec.market_returns(full), xsec.market_returns(cut)
    for c in xsec.universe(full, T):
        assert c.features(T, mf) == cut[c.sym].features(T, mc)


def test_features_hand_calculated():
    c = coin(days=100, price=lambda i: 100.0 + i)              # 最後收盤 199（T − D）
    f = dict(zip(xsec.FEATURES, c.features(T, {})))
    assert f['ret_7d'] == pytest.approx(199 / 192 - 1)
    assert f['ret_56d'] == pytest.approx(199 / 143 - 1)
    assert f['ma_20'] == pytest.approx(199 / 189.5)            # 180～199 的平均
    assert f['age'] == 100
    assert f['dv_ratio'] == pytest.approx(0.25)                # 7 天 ÷ 28 天成交額
    assert f['taker_7d'] == pytest.approx(0.5)
    assert f['beta_56d'] is None                               # 沒有大盤資料


def test_funding_feature_excludes_decision_time():
    fund = [[T - 8 * 3_600_000, 0.001], [T, 0.5]]              # t 那一刻的費率還不算已知
    c = coin(fund=fund)
    f = dict(zip(xsec.FEATURES, c.features(T, {})))
    assert f['fund_7d'] == pytest.approx(0.001)


def bars_from(t, closes, opens=None):
    return [[t + i * D, (opens or closes)[i], x, 100.0, 50.0] for i, x in enumerate(closes)]


def test_hold_ratio_and_funding_on_position_size():
    k = bars_from(T - D, [100.0]) + bars_from(T, [110.0, 120.0, 120.0, 120.0, 120.0, 120.0, 150.0], [100.0] * 7)
    f = [[T + 8 * 3_600_000, 0.01], [T + D, 0.02]]             # 第一天 0.01（部位 1 倍）、第二天 0.02（部位 1.1 倍）
    h = xsec.Coin('AUSDT', k, f).hold(T)
    assert h.ratio == pytest.approx(1.5) and not h.delisted
    assert h.fund == pytest.approx(0.01 + 1.1 * 0.02)
    assert h.path[0] == pytest.approx((1.1, 0.01))


def test_hold_delisted_mid_week_exits_at_last_close():
    k = bars_from(T - D, [100.0]) + bars_from(T, [90.0, 50.0], [100.0, 90.0])
    h = xsec.Coin('LUNAUSDT', k, []).hold(T)
    assert h.delisted and h.ratio == pytest.approx(0.5) and h.path[-1][0] == pytest.approx(0.5)


def test_hold_small_gap_is_not_delisted():
    k = bars_from(T - D, [100.0]) + bars_from(T, [100.0, 110.0]) + bars_from(T + 4 * D, [120.0, 120.0, 120.0])
    h = xsec.Coin('AUSDT', k, []).hold(T)
    assert not h.delisted and h.ratio == pytest.approx(1.2)


def test_label_is_return_minus_funding():
    k = bars_from(T - D, [100.0]) + bars_from(T, [110.0] * 7, [100.0] * 7)
    assert xsec.label(xsec.Coin('AUSDT', k, [[T, 0.01]]), T) == pytest.approx(0.1 - 0.01)


def test_select_top_bottom_20_percent():
    score = {f'C{i:02d}': float(i) for i in range(50)}
    longs, shorts = xsec.select(score)
    assert len(longs) == 10 and len(shorts) == 10 and longs[0] == 'C49' and shorts[-1] == 'C00' and 'C39' not in longs


def two_coin_book():
    a = bars_from(T - D, [100.0]) + bars_from(T, [110.0] * 7, [100.0] * 7)     # +10%
    b = bars_from(T - D, [100.0]) + bars_from(T, [90.0] * 7, [100.0] * 7)      # -10%
    return xsec.Book({'A': xsec.Coin('A', a, [[T, 0.01]]), 'B': xsec.Coin('B', b, [[T, 0.01]])})


def test_step_long_short_hand_calculated():
    s = xsec.step(two_coin_book(), T, ['A'], ['B'], {}, 0.001)
    # 多 A：0.5 ×（+10% − 1%）＝+4.5%；空 B：−0.5 ×（−10% − 1%）＝+5.5%；換手 1 倍 × 0.1%
    assert s['long'] == pytest.approx(0.045) and s['short'] == pytest.approx(0.055)
    assert s['fund'] == pytest.approx(0.0)                     # 多方付、空方收，互相抵消
    assert s['turnover'] == pytest.approx(1.0) and s['ret'] == pytest.approx(0.1 - 0.001)
    assert s['weights']['A'] == pytest.approx(0.5 * 1.1 / 1.099) and s['weights']['B'] == pytest.approx(-0.5 * 0.9 / 1.099)


def test_step_trades_only_the_difference():
    s = xsec.step(two_coin_book(), T, ['A'], ['B'], {'A': 0.5, 'B': -0.5}, 0.001)
    assert s['turnover'] == pytest.approx(0.0) and s['fee'] == 0.0


def test_step_delisted_position_doubles_exit_cost_and_is_gone():
    a = bars_from(T - D, [100.0]) + bars_from(T, [110.0] * 7, [100.0] * 7)
    gone = bars_from(T - D, [100.0]) + bars_from(T, [50.0], [100.0])
    book = xsec.Book({'A': xsec.Coin('A', a, []), 'G': xsec.Coin('G', gone, [])})
    s = xsec.step(book, T, ['A'], ['G'], {}, 0.001)
    # 空 G：−0.5 × −50% ＝ +25%；下架出場額 0.5 × 0.5 ＝ 0.25，加倍成本 0.25 × 0.2%
    assert s['extra'] == pytest.approx(0.25 * 0.002) and 'G' not in s['weights']
    assert s['ret'] == pytest.approx(0.05 + 0.25 - 0.001 - 0.0005)


def test_simulate_marks_and_ruin():
    k = bars_from(T - D, [100.0]) + bars_from(T, [100.0, 500.0, 500.0, 500.0, 500.0, 500.0, 500.0], [100.0] * 7)
    flat = bars_from(T - D, [100.0]) + bars_from(T, [100.0] * 7)
    book = xsec.Book({'UP': xsec.Coin('UP', k, []), 'F': xsec.Coin('F', flat, [])})
    sim = xsec.simulate(book, [(T, ['F'], ['UP'])], 0.0)
    assert sim['equity'] == 0.0                                # 空單 5 倍：賠 200%，帳戶歸零
    assert xsec.drawdown(sim['marks']) == pytest.approx(-1.0)


def test_drawdown_uses_daily_marks():
    a = bars_from(T - D, [100.0]) + bars_from(T, [50.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0], [100.0] * 7)
    b = bars_from(T - D, [100.0]) + bars_from(T, [100.0] * 7)
    book = xsec.Book({'A': xsec.Coin('A', a, []), 'B': xsec.Coin('B', b, [])})
    sim = xsec.simulate(book, [(T, ['A'], ['B'])], 0.0)
    assert sim['equity'] == pytest.approx(1.0) and xsec.drawdown(sim['marks']) == pytest.approx(-0.25)


def test_metrics_halves_and_drop_top():
    rows = [{'t': T, 'ret': 0.1, 'turnover': 1.0, 'long': 0.1, 'short': 0.0, 'fund': 0.0, 'by': {'A': 0.15, 'B': -0.05}},
            {'t': T + W, 'ret': -0.05, 'turnover': 0.5, 'long': 0.0, 'short': -0.05, 'fund': 0.0, 'by': {'B': -0.05}}]
    m = xsec.metrics({'rows': rows, 'marks': [1.0, 1.1, 1.045], 'equity': 1.045}, T + W)
    assert m['h1'] == pytest.approx(0.1) and m['h2'] == pytest.approx(-0.05) and m['mean'] == pytest.approx(0.025)
    assert m['top'] == 'A' and m['drop_top'] == pytest.approx(0.025 - 0.075)


def good(**kw):
    return {'weeks': 150, 'mean': 0.01, 't': 3.5, 'h1': 0.01, 'h2': 0.01, 'dd': -0.2, 'drop_top': 0.005} | kw


@pytest.mark.parametrize('bad', [{'weeks': 129}, {'mean': 0.0}, {'t': 2.99}, {'h2': -0.001}, {'dd': -0.31}, {'drop_top': 0.0}])
def test_dev_checks_each_fails(bad):
    assert all(xsec.dev_checks(good(), 0.005, 0.001, 0.001).values())
    assert not all(xsec.dev_checks(good(**bad), 0.005, 0.001, 0.001).values())


def test_dev_checks_random_stress_thursday():
    assert not all(xsec.dev_checks(good(), 0.02, 0.001, 0.001).values())
    assert not all(xsec.dev_checks(good(), 0.005, -0.001, 0.001).values())
    assert not all(xsec.dev_checks(good(), 0.005, 0.001, -0.001).values())


def test_exam_checks_and_leak():
    m = good()
    assert all(xsec.exam_checks(m, 0.005).values()) and not all(xsec.exam_checks(m, 0.02).values())
    assert xsec.leaked({'t': -3.0}) and not xsec.leaked({'t': 2.9})


def test_picks_for_mom_uses_ret_28_rank():
    panel = {T: {f'C{i:02d}': (0.0, float(i)) for i in range(25)}}
    picks = xsec.picks_for('mom', panel, {}, 'mon', [T])
    assert picks[0][1][0] == 'C24' and picks[0][2][-1] == 'C00' and len(picks[0][1]) == 5


def test_picks_for_model_ignores_coins_outside_universe():
    panel = {T: {f'C{i:02d}': (0.0, 0.0) for i in range(25)}}
    preds = {('gbm', 'mon'): {T: {**{f'C{i:02d}': float(i) for i in range(25)}, 'OUT': 99.0}}}
    assert 'OUT' not in xsec.picks_for('gbm', panel, preds, 'mon', [T])[0][1]


def test_random_means_reproducible_and_percentile():
    book = two_coin_book()
    panel = {T: {'A': (0, 0), 'B': (0, 0)}}
    # 2 個幣、20% 取 1 多 1 空：不是 +9.9% 就是 -10.1%
    r = xsec.random_means(book, panel, [T], runs=50, seed=1, cost=0.001)
    assert r == xsec.random_means(book, panel, [T], runs=50, seed=1, cost=0.001)
    assert set(round(x, 3) for x in r) == {0.099, -0.101}
    assert xsec.percentile([1.0, 2.0, 3.0, 4.0], 0.95) == 4.0 and xsec.percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 3.0


def test_write_and_read_panel(tmp_path):
    coins = {}
    start = xsec.FIRST_MONDAY
    for i in range(22):
        k = [[start + j * D, 10.0 + i, 10.0 + i + j * (i + 1) * 0.01, 1000.0 + i, 500.0] for j in range(90)]
        coins[f'C{i:02d}USDT'] = xsec.Coin(f'C{i:02d}USDT', k, [])
    p = tmp_path / 'panel.csv'
    info = xsec.write_panel(p, coins, 'mon')
    panel = xsec.read_panel(p)
    assert info['weeks'] == len(panel) and all(len(v) == 22 for v in panel.values())
    t = min(panel)
    assert t == start + 63 * D                                 # 第一個上市滿 60 天的週一
    assert panel[t]['C21USDT'][1] == 0.5                       # 漲最多的 28 天報酬排名最高
