import math
import statistics as st

from crypto_robot.lab import mtf
from crypto_robot.lab.hourly import ms

D, BAR = mtf.D, mtf.BAR
T0 = ms(2021, 1, 1)


def make_bars(n, t0=T0):
    t = [t0 + i * BAR for i in range(n)]
    c = [100 * (1 + 0.001 * math.sin(i)) + i * 0.01 for i in range(n)]
    return mtf.Bars(t, list(c), [x + 0.2 for x in c], [x - 0.2 for x in c], c, [1000.0] * n, [600.0] * n)


def test_agg_uses_only_full_blocks_and_closed_bars():
    b = make_bars(40)
    del b.t[20], b.o[20], b.h[20], b.l[20], b.c[20], b.qv[20], b.tb[20]      # 第 2 個 4 小時塊少一根
    f = mtf.Frame.agg(b, 16)
    assert f.t == [T0]                                                     # 只有第 1 塊完整
    assert f.c == [b.c[15]] and f.h == [max(b.h[:16])] and f.l == [min(b.l[:16])]
    g = mtf.Frame.agg(make_bars(64), 16)
    assert g.last(T0 + 4 * 3_600_000 + BAR, 1) is None                     # 第 2 塊（4:00 開）還沒收完 → 最後收完的是第 1 塊，往前不夠 1 根
    assert g.last(T0 + 8 * 3_600_000, 1) == 1                              # 8:00 時，4:00 那塊剛收完
    assert g.last(T0 + 8 * 3_600_000 - BAR, 1) is None                     # 7:45 時它還沒收完


def test_htf_daily_values_by_hand():
    days = 60
    closes = [100 + i + (i % 3) for i in range(days)]
    d = mtf.Frame([T0 + i * D for i in range(days)], closes, closes, closes, D)
    T = T0 + days * D + 5 * BAR                                            # 最後一根日 K 已收完
    ft = [T - 4 * D, T - 2 * D, T - D]
    fr = [0.01, 0.0002, 0.0004]
    empty = mtf.Frame([], [], [], [], 16 * BAR)
    out = dict(zip(mtf.HTF, mtf.htf(T, d, empty, mtf.Frame([], [], [], [], 4 * BAR), ft, fr)))
    k = days - 1
    r = [closes[j] / closes[j - 1] - 1 for j in range(k - 19, k + 1)]
    sd = st.stdev(r)
    assert math.isclose(out['d_z7'], (closes[k] / closes[k - 7] - 1) / (sd * math.sqrt(7)), rel_tol=1e-9)
    assert math.isclose(out['d_ma20'], closes[k] / st.fmean(closes[k - 19:k + 1]) - 1)
    assert math.isclose(out['d_vol20'], sd, rel_tol=1e-9)
    assert math.isclose(out['d_fund3'], 0.0003)                            # 只算 72 小時內的兩次
    assert out['h4_z1'] is None and out['h1_ma24'] is None


def test_gate():
    assert mtf.gate(0.01, 0.02) == 1 and mtf.gate(-0.01, -0.02) == -1
    assert mtf.gate(0.01, -0.02) == 0 and mtf.gate(None, 0.1) == 0


def test_rows_for_hold_16():
    b = make_bars(300)
    day = mtf.Frame([], [], [], [], D)
    rows = mtf.rows_for('X', b, day, [], [], {}, lambda m: True)
    assert len(rows) == 300 - 96 - 17
    d = dict(zip(mtf.COLUMNS, rows[0]))
    i = 96
    sd = st.stdev([b.c[j] / b.c[j - 1] - 1 for j in range(i - 95, i + 1)])
    assert d['t'] == b.t[i] + BAR
    assert math.isclose(d['gross'], b.o[i + 17] / b.o[i + 1] - 1)
    assert math.isclose(d['target'], d['gross'] / (sd * 4), rel_tol=1e-9)
    assert math.isclose(d['mae_l'], 1 - min(b.l[i + 1:i + 17]) / b.o[i + 1])
    assert d['gate'] == 0 and d['d_ma20'] is None
    later = dict(zip(mtf.COLUMNS, rows[-1]))
    assert later['h4_ma20'] is None or isinstance(later['h4_ma20'], float)


def ev(t, sym, side=1, s=1.0):
    return (t, sym, side, s, 0.01, 0.0, 0.02, 0.05)


def test_simulate_holds_16_bars():
    tr = mtf.simulate([ev(T0, 'A'), ev(T0 + 15 * BAR, 'A'), ev(T0 + 16 * BAR, 'A')])
    assert [x.t for x in tr] == [T0, T0 + 16 * BAR]


def test_checks_need_to_beat_gbm_and_pick():
    a = T0
    trades = [mtf.Trade(a + i * D, 'A' if i % 3 else 'B', 1, 0.004 if i % 2 else 0.003, 0.0, 0.1) for i in range(40)]
    s = mtf.evaluate(trades, (a, a + 40 * D), a + 20 * D, runs=50)
    assert math.isclose(s['stress'], s['mean'] - 0.001) and s['dd'] == 0.0
    good = {**s, 'n': 2000, 't': 4.0}
    stats = {'gbm': {**good, 'mean': 0.01}, 'mtf': good, 'mtf_gate': {**good, 'mean': 0.02, 't': 3.5}, 'mtf_shuf': {'n': 5, 't_pre': 0.5}}
    assert not mtf.checks_for('mtf', stats)['贏gbm'] and mtf.checks_for('mtf_gate', stats)['贏gbm']
    assert '贏gbm' not in mtf.checks_for('gbm', stats)
    assert mtf.pick(stats) == 'gbm'                                        # gbm t 4.0 最高
    assert mtf.pick({**stats, 'mtf_shuf': {'n': 5, 't_pre': 3.1}}) is None


def test_load_events_gate_filters_mtf(tmp_path):
    p = tmp_path / 'panel.csv'

    def line(t, sym, gate):
        row = dict.fromkeys(mtf.COLUMNS, '0')
        row.update(t=str(t), sym=sym, gross='0.01', fundsum='0', mae_l='0.01', mae_s='0.02', gate=str(gate))
        return ','.join(row[c] for c in mtf.COLUMNS)
    p.write_text('\n'.join([','.join(mtf.COLUMNS), line(1000, 'A', 1), line(1000, 'B', -1), line(1000, 'C', 0)]) + '\n', encoding='utf-8')
    preds = {(1000, 'A'): {'mtf': (1, 0.5), 'gbm': (-1, -0.2)}, (1000, 'B'): {'mtf': (1, 0.4)}, (1000, 'C'): {'mtf': (-1, -0.3)}}
    e = mtf.load_events(p, preds, (0, 2000))
    assert [x[1] for x in e['mtf']] == ['A', 'B', 'C']
    assert e['mtf_gate'] == [(1000, 'A', 1, 0.5, 0.01, 0.0, 0.01, 0.02)]
    assert e['gbm'] == [(1000, 'A', -1, 0.2, 0.01, 0.0, 0.01, 0.02)]
