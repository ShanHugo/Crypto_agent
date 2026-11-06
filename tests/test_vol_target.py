from decimal import Decimal as Dec

from crypto_robot.core.models import Bar
from crypto_robot.lab import vol_target as vt
from crypto_robot.lab.futures import Funding
from crypto_robot.lab.hourly import H, Series, ms

D = vt.D
T = ms(2022, 3, 1) + 16 * H
A = 1e-12


def bars(rows, start=T - H):
    """rows：[(開, 高, 低, 收), ...]，第一根開盤是 T − 1h（它的收盤就是 T 的價格）。"""
    return Series.of([Bar(start + i * H, *(Dec(str(x)) for x in r), Dec(1)) for i, r in enumerate(rows)])


def flat(p, n):
    return [(p, p, p, p)] * n


def test_forecast_hand_values_and_warmup():
    closes = [100.0]
    for _ in range(35):
        closes.append(closes[-1] * 1.01)
    eth = Series.of([Bar(T - H + i * D, *(Dec(str(c)),) * 4, Dec(1)) for i, c in enumerate(closes)])
    f = vt.forecasts(eth)
    assert T + 29 * D not in f and T + 30 * D in f                 # 第 30 個日報酬才有
    assert abs(f[T + 35 * D] - 0.01 * 365 ** 0.5) < 1e-9           # 報酬固定 → 波動＝|r|×√365


def test_target_and_stop_distance():
    assert vt.target(None) == 0.0 and vt.target(0.25) == 1.0 and vt.target(1.0) == 0.5
    assert vt.stop_dist(vt.RULES[0], 1.0) is None and vt.stop_dist(vt.RULES[1], 1.0) == 0.05
    assert abs(vt.stop_dist(vt.RULES[2], 0.73) - 0.73 / 365 ** 0.5 * 3) < A


def test_run_fee_pnl_funding():
    # T 買 1 倍（收 0.05%）；第一小時漲到 110；該小時收盤結算費率 +0.1%（多單付錢）
    eth = bars(flat(100, 1) + [(100, 110, 100, 110)] + flat(110, 24))
    r = vt.run(eth, Funding([(T + H, 0.001)]), T, T + D + 1, lambda t: 1.0)
    e = 0.9995
    qty = e / 100
    after = e + qty * 10 - qty * 110 * 0.001
    assert r.days == [T + D] and abs(r.rets[0] - (after - 1)) < A
    assert r.dd == 0.0                                             # 逐小時收盤的權益都沒有低於高點（起點 1）


def test_band_no_trade_when_close_to_target():
    eth = bars(flat(100, 2 + 24 * 2))
    goals = {T: 0.5, T + D: 0.55, T + 2 * D: 0.8}
    r = vt.run(eth, Funding([]), T, T + 2 * D + 1, lambda t: goals[t])
    fee = 0.5 * 0.0005
    assert abs(r.rets[0] - (-fee)) < A                             # 第一天買 0.5 倍
    assert abs(r.rets[1]) < A                                      # 差 0.05 倍，不調


def test_stop_fill_at_stop_or_gap_open_then_flat():
    # 停損 5%（95）：第一小時低點 94 成交在 95；之後大漲也沒有部位
    eth = bars(flat(100, 1) + [(100, 100, 94, 96), (96, 200, 96, 200)] + flat(200, 23))
    r = vt.run(eth, Funding([]), T, T + D + 1, lambda t: 1.0, lambda t: 0.05)
    e = 0.9995
    qty = e / 100
    assert abs(r.rets[0] - (e + qty * (95 - 100) - qty * 95 * 0.0005 - 1)) < A
    # 跳空：開盤 90 已低於停損 95，成交在 90
    eth = bars(flat(100, 1) + [(90, 91, 89, 90)] + flat(90, 24))
    r = vt.run(eth, Funding([]), T, T + D + 1, lambda t: 1.0, lambda t: 0.05)
    assert abs(r.rets[0] - (e + qty * (90 - 100) - qty * 90 * 0.0005 - 1)) < A


def test_average_exposure_counts_flat_hours():
    eth = bars(flat(100, 1 + 24))
    assert abs(vt.run(eth, Funding([]), T, T + D + 1, lambda t: 0.5).avg - 0.5) < 1e-3
    assert vt.run(eth, Funding([]), T, T + D + 1, lambda t: 0.05).avg == 0.0   # 差不到 0.1 倍，沒有進場


def test_sharpe_and_halves():
    r = [0.01, -0.01, 0.02]
    import statistics as st
    assert abs(vt.sharpe(r) - st.fmean(r) / st.stdev(r) * 365 ** 0.5) < A
    assert vt.sharpe([0.1]) == 0.0 and vt.sharpe([0.1, 0.1]) == 0.0
    res = vt.Result([1, 2, 3, 4], [0.01, 0.02, -0.01, -0.03], 0, 0, 0)
    assert vt.half_sharpes(res, 3) == [vt.sharpe([0.01, 0.02]), vt.sharpe([-0.01, -0.03])]


def test_prob_better():
    b = [(-1) ** i * 0.01 * (1 + i % 5) for i in range(200)]
    assert vt.prob_better(b, b, runs=50) == 0.0                    # 一樣的序列不算贏
    assert vt.prob_better([x + 0.01 for x in b], b, runs=50) == 1.0
    assert vt.prob_better([0.1], [0.1]) == 0.0                     # 樣本不夠


def test_pass_rules():
    good = {'total': 0.1, 'sharpe': 1.0, 'hold_sharpe': 0.8, 'dd': -0.4, 'same_dd': -0.5, 'half': [1.0, 0.5], 'hold_half': [0.9, 0.4],
            'sharpe_fee2': 0.9, 'hold_sharpe_fee2': 0.8, 'prob': 0.95}
    assert vt.dev_passed(good)
    for bad in ({'total': -0.1}, {'sharpe': 0.7}, {'half': [1.0, 0.3]}, {'same_dd': -0.3}, {'dd': -0.61, 'same_dd': -0.7},
                {'sharpe_fee2': 0.7}, {'prob': 0.89}):
        assert not vt.dev_passed(good | bad), bad
    assert vt.exam_a_passed(good) and not vt.exam_a_passed(good | {'dd': -0.61, 'same_dd': -0.7}) and not vt.exam_a_passed(good | {'total': -0.1})
    assert vt.exam_b_passed(good | {'total': -0.5, 'dd': -0.8, 'same_dd': -0.9}) and not vt.exam_b_passed(good | {'sharpe': 0.7})
