import math
import statistics as st

from crypto_robot.lab import swing, xsec
from crypto_robot.lab.hourly import ms

D, BAR = swing.D, swing.BAR
T0 = ms(2021, 1, 1)


def bars(n, t0=T0, price=lambda i: 100.0):
    t = [t0 + i * BAR for i in range(n)]
    c = [price(i) for i in range(n)]
    return swing.Bars(t, list(c), [x + 0.5 for x in c], [x - 0.5 for x in c], c)


def test_exit_path_stop_gap_and_time():
    b = bars(20 * 96)
    T = T0 + 5 * D
    i0 = 5 * 96
    b.l[i0 + 3] = 97.0                                                    # 碰到停損 98
    px, X, mae, stopped = swing.exit_path(b, i0, 1, 98.0, T)
    assert (px, X, stopped) == (98.0, b.t[i0 + 3], True) and math.isclose(mae, 0.03)
    b.l[i0 + 3] = 99.5
    b.o[i0 + 4], b.l[i0 + 4] = 96.0, 95.0                                 # 開盤就跳過停損 → 開盤價出場
    px, X, mae, stopped = swing.exit_path(b, i0, 1, 98.0, T)
    assert (px, X, stopped) == (96.0, b.t[i0 + 4], True) and math.isclose(mae, 0.04)
    c = bars(20 * 96)
    px, X, mae, stopped = swing.exit_path(c, i0, -1, 110.0, T)            # 沒停損 → 第 10 天 0 點開盤
    assert (px, X, stopped) == (100.0, T + 10 * D, False) and math.isclose(mae, 0.005)


def test_atr_by_hand():
    f = swing.Frame([T0 + i * D for i in range(16)], [10.0 + i for i in range(16)], [11.0 + i for i in range(16)],
                    [9.5 + i for i in range(16)], D)
    tr = [max(f.h[j] - f.l[j], abs(f.h[j] - f.c[j - 1]), abs(f.l[j] - f.c[j - 1])) for j in range(2, 16)]
    assert math.isclose(swing.atr(f, 15), sum(tr) / 14)
    assert swing.atr(f, 13) is None


def make_coin(days, price):
    k = [[T0 + i * D, price(i), price(i), 1000.0 + i, 600.0] for i in range(days)]
    f = [[T0 + i * 8 * 3_600_000, 0.0001] for i in range(days * 3)]
    return xsec.Coin('X', k, f)


def test_rows_for_gate_trend_and_outcomes():
    days = 170
    price = lambda d: 100 * 1.01 ** d                                     # noqa: E731  一路漲：閘門 +1、每天創新高
    coin = make_coin(days, price)
    b = bars(days * 96, price=lambda i: price(i // 96))
    for i in range(len(b.t)):
        b.o[i] = price(i // 96)
    rows = swing.rows_for('X', coin, b, coin, lambda m: True)
    assert rows
    d = dict(zip(swing.COLUMNS, rows[-20]))
    T = d['t']
    k = coin.pos[T - D]
    assert d['gate'] == 1 and d['trend'] == 1
    assert math.isclose(d['d_ma20'], coin.c[k] / st.fmean(coin.c[k - 19:k + 1]) - 1)
    assert math.isclose(d['w_ma20'], coin.c[k] / st.fmean(coin.c[k - 7 * j] for j in range(20)) - 1)
    assert math.isclose(d['gross_l'], price((T - T0) // D + 10) / price((T - T0) // D) - 1) and d['stop_l'] == 0
    assert d['days_l'] == 10
    assert math.isclose(d['fund_l'], 30 * 0.0001)                       # 10 天 × 每天 3 次（含出場那刻）
    assert d['stop_s'] == 1 and d['days_s'] < 10                         # 做空會被停損


def leg(g, days=10.0, stop=0, fund=0.0, mae=0.01):
    return (g, fund, mae, days, stop)


def test_simulate_slots_and_hold_until_exit():
    evs = [(T0, f'C{i}', 1, i, leg(0.01), leg(-0.01)) for i in range(12)]
    evs += [(T0 + 3 * D, 'C11', 1, 9, leg(0.01), leg(-0.01)), (T0 + 2 * D, 'Q', 1, 1, leg(0.01, days=1.0), leg(0.0))]
    tr = swing.simulate(evs)
    first = [x.sym for x in tr if x.t == T0]
    assert len(first) == 10 and 'C0' not in first and 'C1' not in first
    assert not [x for x in tr if x.t > T0]                                  # 10 筆都還抱著
    tr = swing.simulate([(T0, 'A', 1, 1, leg(0.02, days=2.0), leg(-0.02)), (T0 + D, 'A', 1, 1, leg(0.0), leg(0.0)),
                         (T0 + 2 * D, 'A', -1, 1, leg(0.0), leg(0.03, fund=0.001))])
    assert [x.t for x in tr] == [T0, T0 + 2 * D]
    assert math.isclose(tr[1].net(), -0.03 - 0.001 + 0.001)                # 做空：-gross − 成本 + 收到資金費


def test_evaluate_random_uses_each_side_and_pick():
    trades = [swing.Trade(T0 + i * D, 'A' if i % 3 else 'B', 1, leg(0.02), leg(0.01)) for i in range(60)]
    s = swing.evaluate(trades, (T0, T0 + 60 * D), T0 + 30 * D, runs=200)
    assert math.isclose(s['mean'], 0.02 - 0.001) and math.isclose(s['stress'], s['mean'] - 0.001)
    # 隨機：一半 +0.02、一半 -0.01（做空用自己的情境），平均約 +0.005 − 0.001
    assert 0.002 < s['p95'] < s['mean']
    assert s['days'] == 10 and s['liq_10x'] == 0
    good = {**s, 'n': 500, 't': 3.5}
    assert swing.pick({'trend': good, 'swing_gbm': {**good, 't': 4.0}, 'swing_gate': {'n': 0}, 'swing_gate_shuf': {'n': 9, 't_pre': 1.0}}) == 'swing_gbm'
    assert swing.pick({'trend': good, 'swing_gate_shuf': {'n': 9, 't_pre': 3.2}}) is None


def test_load_events(tmp_path):
    p = tmp_path / 'panel.csv'

    def line(t, sym, gate, trend):
        row = dict.fromkeys(swing.COLUMNS, '0')
        row.update(t=str(t), sym=sym, gate=str(gate), trend=str(trend), trend_s='1.5', gross_l='0.02', gross_s='-0.01')
        return ','.join(row[c] for c in swing.COLUMNS)
    p.write_text('\n'.join([','.join(swing.COLUMNS), line(T0, 'A', 1, 1), line(T0, 'B', -1, 0)]) + '\n', encoding='utf-8')
    preds = {(T0, 'A'): {'gbm': (-1, -0.3), 'gbm_shuf': (1, 0.2)}, (T0, 'B'): {'gbm': (-1, -0.5)}}
    e = swing.load_events(p, preds, (T0, T0 + D))
    assert [(x[1], x[2]) for x in e['trend']] == [('A', 1)]
    assert [(x[1], x[2]) for x in e['swing_gbm']] == [('A', -1), ('B', -1)]
    assert [(x[1], x[2]) for x in e['swing_gate']] == [('B', -1)]
    assert [(x[1], x[2]) for x in e['swing_gate_shuf']] == [('A', 1)]
    assert e['trend'][0][4][0] == 0.02 and e['trend'][0][5][0] == -0.01
