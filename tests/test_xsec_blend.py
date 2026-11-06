import math

import pytest

from crypto_robot.lab import xsec, xsec_blend as bl
from crypto_robot.lab.hourly import ms

D = xsec.D
T = ms(2023, 3, 6)


def names(n=5):
    return [f'C{i:02d}' for i in range(n)]


def test_blend_score_is_mean_of_three_ranks():
    # 3 個幣：排名 -0.5、0、+0.5。動能在 panel 的第二欄。
    uni = {'A': (0.0, 3.0), 'B': (0.0, 2.0), 'C': (0.0, 1.0)}
    p = {'gbm': {'A': 1.0, 'B': 2.0, 'C': 3.0}, 'ridge': {'A': 2.0, 'B': 1.0, 'C': 3.0}}
    s = bl.blend_score(uni, p, ['gbm', 'ridge'])
    assert s['A'] == pytest.approx((-0.5 + 0.0 + 0.5) / 3)
    assert s['B'] == pytest.approx((0.0 - 0.5 + 0.0) / 3)
    assert s['C'] == pytest.approx((0.5 + 0.5 - 0.5) / 3)


def test_blend_score_drops_coins_missing_a_model_and_outsiders():
    uni = {'A': (0.0, 1.0), 'B': (0.0, 2.0)}
    p = {'gbm': {'A': 1.0, 'B': 2.0, 'OUT': 9.0}, 'ridge': {'B': 1.0, 'OUT': 9.0}}
    assert set(bl.blend_score(uni, p, ['gbm', 'ridge'])) == {'B'}


def test_control_has_no_momentum():
    uni = {'A': (0.0, 100.0), 'B': (0.0, -100.0)}
    p = {'x': {'A': 1.0, 'B': 2.0}}
    assert bl.blend_score(uni, p, ['x'], use_mom=False) == {'A': -0.5, 'B': 0.5}


def test_picks_for_kinds():
    n = 25
    uni = {s: (0.0, float(i)) for i, s in enumerate(names(n))}
    up = {s: float(i) for i, s in enumerate(names(n))}
    down = {s: -float(i) for i, s in enumerate(names(n))}
    preds = {('gbm', 'mon'): {T: up}, ('ridge', 'mon'): {T: up}, ('gbm_shuf', 'mon'): {T: down}, ('ridge_shuf', 'mon'): {T: down}}
    panel = {T: uni}
    _, longs, shorts = bl.picks_for('gbm', panel, preds, 'mon', [T])[0]
    assert longs[0] == 'C24' and shorts[-1] == 'C00'
    _, longs, shorts = bl.picks_for('blend', panel, preds, 'mon', [T])[0]
    assert longs[0] == 'C24' and shorts[-1] == 'C00'
    _, longs, shorts = bl.picks_for('blend_shuf', panel, preds, 'mon', [T])[0]
    assert longs[0] == 'C00' and shorts[-1] == 'C24'
    assert bl.picks_for('blend', {T: dict(list(uni.items())[:10])}, preds, 'mon', [T]) == []     # 不足 20 個幣


def test_blend_picks_matches_research_and_refuses_too_few():
    n = 25
    uni = {s: (0.0, float(n - i)) for i, s in enumerate(names(n))}
    gbm = {s: float(i) for i, s in enumerate(names(n))}
    ridge = {s: float(i * i) for i, s in enumerate(names(n))}
    preds = {('gbm', 'mon'): {T: gbm}, ('ridge', 'mon'): {T: ridge}}
    research = bl.picks_for('blend', {T: uni}, preds, 'mon', [T])[0]
    assert bl.blend_picks(names(n), gbm, ridge, {s: v[1] for s, v in uni.items()}) == research[1:]
    with pytest.raises(ValueError):
        bl.blend_picks(names(n), {s: 1.0 for s in names(19)}, ridge, {s: v[1] for s, v in uni.items()})


def coin(sym, rets, end=T):
    """從 end 往回排的每日報酬（最後一個是 end 前一天那根）。"""
    n = len(rets)
    bars, c = [[end - (n + 1) * D, 100.0, 100.0, 100.0, 50.0]], 100.0
    for i, r in enumerate(rets):
        c *= 1 + r
        bars.append([end - (n - i) * D, c, c, 100.0, 50.0])
    return xsec.Coin(sym, bars, [])


def test_basket_vol_constant_returns_by_hand():
    # 多方每天 +5%、空方不動：組合每天 0.5 × 5% = 2.5%；權重正規化後 σ² = 2.5%²，年化 2.5% × √365
    coins = {'L': coin('L', [0.05] * 56), 'S': coin('S', [0.0] * 56)}
    vol = bl.basket_vol(coins, T, ['L'], ['S'])
    assert vol == pytest.approx(0.025 * math.sqrt(365))
    assert bl.scale_of(vol) == pytest.approx(0.20 / (0.025 * math.sqrt(365)))


def test_basket_vol_ewma_weights_recent_day_most_by_hand():
    # 只有最後一天有報酬 2%：組合 1%；權重 1 ÷ Σ 0.94^a（a=0..55）
    norm = sum(0.94 ** a for a in range(56))
    coins = {'L': coin('L', [0.0] * 55 + [0.02]), 'S': coin('S', [0.0] * 56)}
    assert bl.basket_vol(coins, T, ['L'], ['S']) == pytest.approx(math.sqrt(365 * 0.01 ** 2 / norm))
    # 同樣大小放在最舊那天：權重 0.94^55 ÷ Σ
    coins = {'L': coin('L', [0.02] + [0.0] * 55), 'S': coin('S', [0.0] * 56)}
    assert bl.basket_vol(coins, T, ['L'], ['S']) == pytest.approx(math.sqrt(365 * 0.94 ** 55 * 0.01 ** 2 / norm))


def test_basket_vol_shorts_offset_longs_and_ignores_future():
    coins = {'L': coin('L', [0.03] * 56), 'S': coin('S', [0.03] * 56)}
    assert bl.basket_vol(coins, T, ['L'], ['S']) == pytest.approx(0.0, abs=1e-12)    # 多空抵銷
    a = coin('L', [0.01, -0.02] * 28)
    old = [list(x) for x in zip(a.t, a.o, a.c, a.qv, a.tb)]
    b = xsec.Coin('L', old + [[T, 999.0, 999.0, 1.0, 1.0], [T + D, 5.0, 5.0, 1.0, 1.0]], [])
    s = coin('S', [0.0] * 56)
    assert bl.basket_vol({'L': a, 'S': s}, T, ['L'], ['S']) == bl.basket_vol({'L': b, 'S': s}, T, ['L'], ['S'])


def test_basket_vol_missing_days_count_as_zero():
    coins = {'L': coin('L', [0.02] * 10), 'S': coin('S', [0.0] * 56)}
    norm = sum(0.94 ** a for a in range(56))
    expect = sum(0.94 ** a for a in range(10)) * 0.01 ** 2
    assert bl.basket_vol(coins, T, ['L'], ['S']) == pytest.approx(math.sqrt(365 * expect / norm))


def test_scale_only_shrinks():
    assert bl.scale_of(0.10) == 1.0 and bl.scale_of(0.0) == 1.0
    assert bl.scale_of(0.40) == pytest.approx(0.5)


def week(t, closes):
    """t 那週的 7 根日 K：開盤＝前一根收盤（第一根開盤 100）。"""
    return [[t + i * D, 100.0 if i == 0 else closes[i - 1], x, 100.0, 50.0] for i, x in enumerate(closes)]


def small_book():
    before = [[T - D, 100.0, 100.0, 100.0, 50.0]]
    up = xsec.Coin('UP', before + week(T, [102.0, 104.0, 106.0, 108.0, 109.0, 110.0, 110.0]) + [[T + (7 + i) * D, 110.0, 110.0, 100.0, 50.0] for i in range(7)], [])
    dn = xsec.Coin('DN', before + week(T, [99.0, 98.0, 97.0, 96.0, 95.0, 95.0, 95.0]) + [[T + (7 + i) * D, 95.0, 95.0, 100.0, 50.0] for i in range(7)], [])
    return xsec.Book({'UP': up, 'DN': dn})


def test_step_scale_by_hand():
    # 多 UP +10%、空 DN -5%（空方賺）；比例 0.5：每個幣 0.25。損益 0.25×10% + 0.25×5% = 3.75%；換手 0.5，成本 0.05%
    s = bl.step(small_book(), T, ['UP'], ['DN'], {}, 0.001, 0.5)
    assert s['turnover'] == pytest.approx(0.5)
    assert s['fee'] == pytest.approx(0.0005)
    assert s['ret'] == pytest.approx(0.0375 - 0.0005)
    assert s['tgt'] == {'UP': 0.25, 'DN': -0.25}


def test_scale_one_is_identical_to_round_21():
    book = small_book()
    picks = [(T, ['UP'], ['DN']), (T + 7 * D, ['DN'], ['UP'])]
    a, b = bl.simulate(book, picks), xsec.simulate(book, picks)
    assert a['marks'] == b['marks'] and a['equity'] == b['equity']
    panel = {T: {'UP': (0, 0), 'DN': (0, 0)}, T + 7 * D: {'UP': (0, 0), 'DN': (0, 0)}}
    assert bl.random_means(book, panel, [T, T + 7 * D], runs=20) == xsec.random_means(book, panel, [T, T + 7 * D], runs=20)


def test_half_scale_halves_pnl_before_costs():
    book = small_book()
    picks = [(T, ['UP'], ['DN'])]
    full = bl.simulate(book, picks, cost=0.0)
    half = bl.simulate(book, picks, cost=0.0, scales={T: 0.5})
    assert half['equity'] - 1 == pytest.approx((full['equity'] - 1) / 2)


def test_vt_checks_and_beats_gbm():
    m = {'dd': -0.2, 't': 1.5}
    assert all(bl.vt_checks(m, -0.25, 1.0).values())
    assert not bl.vt_checks(m, -0.15, 1.0)['回撤比同平均比例的固定版本淺']
    assert not bl.vt_checks(m, -0.25, 1.5)['t 高於不調部位']
    base = {'dd': -0.3, 't': 1.2}
    assert bl.beats_gbm(m, base, None) and bl.beats_gbm(m, base, -0.25)
    assert not bl.beats_gbm(m, base, -0.15)
    assert not bl.beats_gbm({'dd': -0.2, 't': 1.0}, base, None)
    assert not bl.beats_gbm({'dd': -0.4, 't': 2.0}, base, None)
