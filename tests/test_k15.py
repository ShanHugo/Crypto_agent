import math
import statistics as st

from crypto_robot.lab import k15, xsec
from crypto_robot.lab.hourly import ms

D, BAR = k15.D, k15.BAR


def daily_coin(sym, start, days, qv):
    k = [[start + i * D, 1.0, 1.0, qv, qv / 2] for i in range(days)]
    return xsec.Coin(sym, k, [])


def test_months_and_universe():
    assert k15.months('2020-11', '2021-02') == ['2020-11', '2020-12', '2021-01', '2021-02']
    assert k15.month_key(ms(2021, 3, 1) - 1) == '2021-02'
    t = ms(2021, 3, 1)
    coins = {'OLD': daily_coin('OLD', t - 200 * D, 200, 5.0),
             'BIG': daily_coin('BIG', t - 100 * D, 100, 9.0),
             'NEW': daily_coin('NEW', t - 60 * D, 60, 99.0),             # 未滿 90 天
             'GONE': daily_coin('GONE', t - 300 * D, 250, 99.0)}         # 月初前一天沒有 K 線（已下架）
    assert k15.universe(coins, ['2021-03']) == {'2021-03': ['BIG', 'OLD']}


def make_bars(n, t0=ms(2021, 1, 1)):
    t = [t0 + i * BAR for i in range(n)]
    c = [100 * (1.001 if i % 2 else 0.999) for i in range(n)]
    o = [100.0] * n
    h = [x + 0.5 for x in c]
    lo = [x - 0.5 for x in c]
    qv = [1000.0] * n
    tb = [600.0] * n
    return k15.Bars(t, o, h, lo, c, qv, tb)


def test_rows_for_values_by_hand():
    b = make_bars(110)
    i = 100                                       # 拉高收盤、放量：突破 → brk 做多
    b.c[i], b.h[i], b.qv[i] = 103.0, 103.2, 2000.0
    b.o[i + 1], b.o[i + 5] = 103.0, 104.03
    for j in range(i + 1, i + 5):
        b.h[j], b.l[j] = 103.5, 102.5
    b.l[i + 2], b.h[i + 3] = 102.0, 104.5
    T = b.t[i] + BAR
    ft, fr = [T - 1, b.t[i + 1] + 1, b.t[i + 5], b.t[i + 5] + 1], [0.0001, 0.0002, 0.0003, 0.0009]
    rows = k15.rows_for('X', b, ft, fr, {T: 0.7}, lambda m: True)
    row = next(r for r in rows if r[0] == T)
    d = dict(zip(k15.COLUMNS, row))
    r = [b.c[j] / b.c[j - 1] - 1 for j in range(i - 95, i + 1)]
    sd = st.stdev(r)
    assert math.isclose(d['z1'], (103 / b.c[i - 1] - 1) / sd, rel_tol=1e-9)
    assert math.isclose(d['z4'], (103 / b.c[i - 4] - 1) / (sd * 2), rel_tol=1e-9)
    assert math.isclose(d['gross'], 104.03 / 103 - 1)
    assert math.isclose(d['target'], d['gross'] / (sd * 2))
    assert math.isclose(d['fundsum'], 0.0005)                    # (進場, 出場] 之間的兩次結算
    assert d['fund'] == 0.0001                                   # 收盤前最後一次
    assert math.isclose(d['mae_l'], 1 - 102 / 103) and math.isclose(d['mae_s'], 104.5 / 103 - 1)
    assert d['brk'] == 1 and math.isclose(d['vratio'], 2.0) and d['brk_s'] == d['vratio']
    assert d['btc_z4'] == 0.7 and d['hour'] == (T // 3_600_000) % 24
    assert math.isclose(d['taker4'], 2400 / 5000)
    assert 0 <= d['pos16'] <= 1


def test_rows_need_continuity_and_universe():
    b = make_bars(110)
    assert len(k15.rows_for('X', b, [], [], {}, lambda m: True)) == 110 - 96 - 5
    assert k15.rows_for('X', b, [], [], {}, lambda m: False) == []
    b.t = b.t[:50] + [x + BAR for x in b.t[50:]]                  # 中間缺一根：前 96 根不連續
    assert k15.rows_for('X', b, [], [], {}, lambda m: True) == []


def test_rev_signal():
    b = make_bars(110)
    for j in range(97, 101):
        b.c[j] = b.c[j - 1] * 0.99                                 # 連跌 4 根，遠超 2.5 倍波動
    d = dict(zip(k15.COLUMNS, next(r for r in k15.rows_for('X', b, [], [], {}, lambda m: True) if r[0] == b.t[100] + BAR)))
    assert d['rev'] == 1 and d['rev_s'] > k15.REV_Z


def ev(t, sym, side=1, s=1.0, gross=0.01, fund=0.0):
    return (t, sym, side, s, gross, fund, 0.02, 0.05)


def test_simulate_slots_and_duplicates():
    t = ms(2021, 1, 1)
    evs = [ev(t, f'C{i}', s=i) for i in range(12)] + [ev(t + BAR, 'C11'), ev(t + 4 * BAR, 'C11'), ev(t + 4 * BAR, 'Z')]
    tr = k15.simulate(evs)
    first = [x.sym for x in tr if x.t == t]
    assert len(first) == 10 and 'C0' not in first and 'C1' not in first   # 強度小的被擠掉
    assert not [x for x in tr if x.t == t + BAR]                          # 滿了／同幣已有單
    assert {x.sym for x in tr if x.t == t + 4 * BAR} == {'C11', 'Z'}       # 出場那刻就空出來
    short = k15.simulate([ev(t, 'S', side=-1, gross=0.01, fund=0.001)])[0]
    assert short.mae == 0.05 and math.isclose(short.net(), -0.01 - 0.001 + 0.001)


def test_evaluate_and_pick():
    a = ms(2021, 1, 1)
    trades = [k15.Trade(a + i * D, 'A' if i % 3 else 'B', 1, 0.004 if i % 2 else 0.002, 0.0, 0.1) for i in range(40)]
    s = k15.evaluate(trades, (a, a + 40 * D), a + 20 * D, runs=50)
    nets = [x.net() for x in trades]
    assert s['n'] == 40 and math.isclose(s['mean'], sum(nets) / 40)
    days = [k15.SIZE * x for x in nets]
    assert math.isclose(s['t'], k15.t_stat(days))
    assert math.isclose(s['stress'], s['mean'] - 0.001)
    assert s['dd'] == 0.0 and s['liq_10x'] == 1.0
    assert s['best'] == 'A' and math.isclose(s['drop_best'], st.fmean(x.net() for x in trades if x.sym == 'B'))
    assert s['p95'] < s['mean']
    good = {**s, 'n': 2000, 't': 4.0}
    assert k15.pick({'brk': good, 'rev': {**good, 't': 3.5}, 'gbm': {'n': 0}, 'gbm_shuf': {'n': 10, 't_pre': 1.0}}) == 'brk'
    assert k15.pick({'brk': good, 'gbm_shuf': {'n': 10, 't_pre': -3.2}}) is None
    assert not all(k15.dev_checks({**good, 'second': -0.001}).values())


def test_load_events_reads_panel_and_preds(tmp_path):
    p = tmp_path / 'panel.csv'
    row = dict.fromkeys(k15.COLUMNS, '0')
    row.update(t='1000', sym='A', gross='0.01', fundsum='0', mae_l='0.01', mae_s='0.02', brk='-1', brk_s='2', rev='0', rev_s='0')
    p.write_text(','.join(k15.COLUMNS) + '\n' + ','.join(row[c] for c in k15.COLUMNS) + '\n', encoding='utf-8')
    e = k15.load_events(p, {(1000, 'A'): {'gbm': (1, -0.3), 'gbm_shuf': (0, 0.0)}}, (0, 2000))
    assert e['brk'] == [(1000, 'A', -1, 2.0, 0.01, 0.0, 0.01, 0.02)]
    assert e['gbm'] == [(1000, 'A', 1, 0.3, 0.01, 0.0, 0.01, 0.02)] and e['gbm_shuf'] == [] and e['rev'] == []
