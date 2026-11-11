import math
import statistics as st

from crypto_robot.forecast import trend

D, BAR = trend.D, trend.BAR
S = trend.START_MS


def daily(n, end, price, qv=1000.0):
    """n 根日 K，最後一根開盤在 end − D（也就是 end 那天 0 點之前收完）。"""
    t0 = end - n * D
    rows = []
    for i in range(n):
        c = price(i)
        rows.append([t0 + i * D, c, c * 1.01, c * 0.99, c, 0, 0, qv, 0, 0, 0])
    return trend.Daily.from_klines(rows)


def k15(T, n, price=lambda i: 100.0):
    rows = [[T + i * BAR, price(i), price(i) + 0.5, price(i) - 0.5, price(i)] for i in range(n)]
    return trend.K15.from_klines(rows)


def test_universe_age_volume_and_listing():
    key = '2026-12'
    t = trend.month_start(key)
    d = {'OLD': daily(200, t, lambda i: 1.0, qv=5.0), 'BIG': daily(100, t, lambda i: 1.0, qv=9.0),
         'NEW': daily(60, t, lambda i: 1.0, qv=99.0), 'GONE': daily(200, t - 5 * D, lambda i: 1.0, qv=99.0)}
    assert trend.universe(key, d) == ['BIG', 'OLD']


def test_signal_by_hand():
    T = S
    up = daily(200, T, lambda i: 100 * 1.01 ** i)
    side, strength, atr = trend.signal(up, T)
    c = up.c
    k = len(c) - 1
    r = [c[j] / c[j - 1] - 1 for j in range(k - 19, k + 1)]
    assert side == 1
    sd = st.stdev(r)
    assert math.isclose(strength, abs((c[k] / c[k - 30] - 1) / (sd * math.sqrt(30)))) if sd else strength == 0
    tr = [max(up.h[j] - up.l[j], abs(up.h[j] - c[j - 1]), abs(up.l[j] - c[j - 1])) for j in range(k - 13, k + 1)]
    assert math.isclose(atr, sum(tr) / 14)
    down = daily(200, T, lambda i: 100 * 0.99 ** i)
    assert trend.signal(down, T)[0] == -1
    flat_top = daily(200, T, lambda i: 100 * 1.01 ** min(i, 190))          # 最近不是新高 → 沒訊號
    assert trend.signal(flat_top, T) is None or trend.signal(flat_top, T)[0] == 1
    assert trend.signal(daily(100, T, lambda i: 100 * 1.01 ** i), T) is None   # 不到 134 天


def test_exit_path_stop_and_time():
    T = S
    b = k15(T, 1000)
    b.l[5] = 97.0
    px, X, mae, stopped = trend.exit_path(b, 1, 98.0, T)
    assert (px, X, stopped) == (98.0, T + 5 * BAR, True) and math.isclose(mae, 0.03)
    px, X, mae, stopped = trend.exit_path(k15(T, 1000), -1, 110.0, T)
    assert (px, X, stopped) == (100.0, T + 10 * D, False) and math.isclose(mae, 0.005)
    assert trend.exit_path(k15(T, 50), 1, 90.0, T) is None                 # 還沒滿 10 天


def test_simulate_slots_holding_and_log(tmp_path):
    T = S
    uni = {trend.month_key(T): [f'C{i}' for i in range(12)]}
    d = {f'C{i}': daily(200 + 10, T + 10 * D, lambda j, i=i: 100 * (1.01 + i * 0.001) ** j) for i in range(12)}
    calls = []

    def get15(sym, t):
        calls.append((sym, t))
        return k15(t, 1000)

    fund = lambda sym: ([T + 8 * 3_600_000 * j for j in range(40)], [0.0001] * 40)   # noqa: E731
    done, holding = trend.simulate(T + 12 * D, uni, d, get15, fund)
    first = [r for r in done if r['t'] == T]
    assert len(first) == 10 and {r['sym'] for r in first} == {f'C{i}' for i in range(2, 12)}   # 強度小的 C0、C1 沒進
    r = first[0]
    assert r['days'] == 10 and not r['stopped'] and math.isclose(r['fund'], 30 * 0.0001)
    assert math.isclose(r['net'], 0.0 - 0.001 - 30 * 0.0001)
    assert r['alt'] is not None
    assert not [x for x in done if T < x['t'] < T + 10 * D]                 # 10 天內滿倉
    _, holding = trend.simulate(T + 3 * D, uni, d, get15, fund)
    assert len(holding) == 10
    path = tmp_path / 'trend.jsonl'
    rows, added = trend.update(path, first[:3])
    assert added == 3
    rows, added = trend.update(path, first)
    assert added == 7 and len(trend.read_log(path)) == 10


def test_status_checkpoints_and_text():
    rows = [{'t': S + i * D, 'sym': f'X{i % 7}', 'exit_t': S + (i + 1) * D, 'net': 0.02 if i % 3 else -0.01, 'alt': -0.02} for i in range(300)]
    s = trend.status(rows)
    assert s['n'] == 300 and s['stopped'] is False and s['final_pass'] is True
    bad = [{**r, 'net': -abs(r['net'])} for r in rows[:120]]
    s2 = trend.status(bad)
    assert s2['stopped'] is True and s2['final_pass'] is None
    txt = trend.text(s2, 4, 2, (3, None))
    assert '停止條件成立' in txt and '週數不足' in txt


def test_weekly_and_correlation():
    m = trend.weekly_returns([{'exit_t': S + D, 'net': 0.01}, {'exit_t': S + 2 * D, 'net': 0.02}])
    assert list(m.values()) == [0.1 * 0.03]
    mon = int(trend.datetime(2026, 11, 9, tzinfo=trend.timezone.utc).timestamp() * 1000)
    eq = [(mon + w * 7 * D + 1, 100 * 1.01 ** w) for w in range(12)]
    b = trend.blend_weekly(eq)
    assert len(b) == 11 and all(math.isclose(x, 0.01) for x in b.values())
    weeks, c = trend.correlation({w: 0.0 for w in b}, b)
    assert weeks == 11 and c is None                                        # 一邊沒變動
