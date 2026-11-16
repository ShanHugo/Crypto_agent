from crypto_robot.forecast import picks, trend

D, BAR = trend.D, trend.BAR


def test_leverage_highest_safe():
    assert picks.leverage(0.10) == 5          # 0.12 < 0.19
    assert picks.leverage(0.18) == 4          # 0.20 ≥ 0.19、< 0.24
    assert picks.leverage(0.25) == 3          # 0.27 < 0.3233
    assert picks.leverage(0.32) is None


def _daily(closes, start=0):
    n = len(closes)
    t = [start + i * D for i in range(n)]
    return trend.Daily(t, closes[:], [c * 1.01 for c in closes], [c * 0.99 for c in closes], closes[:], [1.0] * n)


def test_choose_puts_breakout_first_and_caps_five():
    up = [100 * 1.01 ** i for i in range(200)]                 # 一路創新高 → 做多、突破
    flat_up = [100 * 1.01 ** i for i in range(190)] + [100 * 1.01 ** 189 * 0.99] * 10   # 方向向上但沒突破
    daily = {f'U{i}': _daily(up) for i in range(3)}
    daily |= {f'F{i}': _daily(flat_up) for i in range(4)}
    T = 199 * D
    out = picks.choose(daily, sorted(daily), T)
    assert len(out) == 5
    assert [p['breakout'] for p in out] == [True] * 3 + [False] * 2
    p = out[0]
    assert p['side'] == 1 and abs((p['tp'] - p['entry']) - 2 * (p['entry'] - p['stop'])) < 1e-9
    assert p['lev'] in (3, 4, 5)


def _bars(rows, t0=0):
    return trend.K15([t0 + i * BAR for i in range(len(rows))], *[[r[i] for r in rows] for i in range(4)])


def test_score_tp_stop_same_bar_and_expire():
    p = {'t': 0, 'sym': 'X', 'side': 1, 'entry': 100.0, 'stop': 90.0, 'tp': 120.0, 'stop_pct': 0.1, 'lev': 5}
    r = picks.score(p, _bars([(100, 101, 99, 100), (100, 121, 99, 120)]))
    assert r['how'] == 'tp' and r['exit_px'] == 120.0 and abs(r['margin'] - 5 * (0.2 - 0.001)) < 1e-9
    r = picks.score(p, _bars([(100, 125, 85, 100)]))
    assert r['how'] == 'stop' and r['exit_px'] == 90.0           # 同一根都碰算停損
    r = picks.score({**p, 'side': -1, 'stop': 110.0, 'tp': 80.0}, _bars([(100, 101, 99, 100), (115, 116, 114, 115)]))
    assert r['how'] == 'stop' and r['exit_px'] == 115            # 開盤已跳過停損
    assert picks.score(p, _bars([(100, 101, 99, 100)] * 10)) is None
    n = trend.HOLD * D // BAR
    r = picks.score(p, _bars([(100, 101, 99, 100)] * n + [(103, 104, 102, 103)]))
    assert r['how'] == 'expire' and r['exit_px'] == 103


def test_summary_streak():
    rs = [{'exit_t': i, 'sym': 'X', 'net': x, 'how': 'stop' if x < 0 else 'tp', 'r': x * 10, 'margin': x * 5}
          for i, x in enumerate([0.1, -0.1, -0.1, -0.1, 0.2])]
    s = picks.summary(rs)
    assert s['n'] == 5 and s['worst_streak'] == 3 and s['tp'] == 2 and abs(s['win'] - 0.4) < 1e-9
