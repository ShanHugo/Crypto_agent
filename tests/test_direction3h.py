from decimal import Decimal as D

from crypto_robot.core.models import Bar
from crypto_robot.lab import direction3h as d3

H = d3.H
T0 = d3.DEV_START        # 2021-01-01 00:00 UTC，UTC 小時 0


def hourly(prices, start=T0, vols=None):
    """開盤＝前一根收盤，高低＝開收的高低，方便手算。"""
    out, prev = [], prices[0]
    for i, p in enumerate(prices):
        v = vols[i] if vols else 1
        out.append(Bar(start + i * H, D(str(prev)), D(str(max(prev, p))), D(str(min(prev, p))), D(str(p)), D(str(v))))
        prev = p
    return out


def test_slots_are_taipei_every_3h():
    s = d3.slots(T0, T0 + 24 * H)
    assert [x // H % 24 for x in s] == [1, 4, 7, 10, 13, 16, 19, 22]   # 台北 09、12…06 點


def test_rsi_all_up_is_100():
    r = d3._rsi([float(i) for i in range(1, 20)])
    assert r[13] is None and r[14] == 100.0


def test_features_on_steady_rise():
    eth = d3.Series.of(hourly([100 + i for i in range(40)]))
    s = T0 + 25 * H + H                         # 需要 s−25h 起的資料：最早 T0+25h，UTC 小時 % 3 == 1 → 26h
    fut, f = d3.features(eth, None, None, s)
    now = 100 + 25                              # s−1h 那根（索引 25）收盤
    assert fut == (100 + 28) / now - 1          # s+2h 那根收盤
    assert f['mom3'] == 1 and f['rev3'] == -1 and f['mom24'] == 1
    assert f['ema20'] == 1 and f['ema9_21'] == 1 and f['mom24_ema20'] == 1
    assert f['break24'] == 1 and f['fake24'] == -1
    assert f['rsi14'] == -1                     # 一路漲 → RSI 100 → 猜跌
    assert f['big_mom'] == 1 and f['big_rev'] == -1     # 3 小時漲 3/122 ≈ 2.5% > 2%
    assert f['btc3'] == 0 and f['usdt3'] == 0


def test_gap_skips_block():
    bars = hourly([100 + i for i in range(40)])
    del bars[10]
    eth = d3.Series.of(bars)
    assert d3.features(eth, None, None, T0 + 26 * H) is None


def test_volume_spike_follows_bar_direction():
    vols = [1] * 40
    vols[25] = 5
    eth = d3.Series.of(hourly([100 + i for i in range(40)], vols=vols))
    assert d3.features(eth, None, None, T0 + 26 * H)[1]['vol_spike'] == 1


def test_evaluate_hand_example():
    mid = T0 + 2 * H
    rows = [(T0, 0.01, {'x': 1}), (T0 + H, -0.02, {'x': -1}), (T0 + 2 * H, 0.03, {'x': -1}), (T0 + 3 * H, 0.0, {'x': 0})]
    m = d3.evaluate(rows, 'x', mid)
    assert m['n'] == 3 and m['hit'] == 2 / 3 and m['up_base'] == 2 / 3
    assert m['half_hit'] == [1.0, 0.0]
    assert m['n_long'] == 1 and abs(m['long_net'] - (0.01 - d3.FEE)) < 1e-12
    assert not d3.passed(m)                     # 次數太少


def test_fit_slots():
    rows = [(T0 + H, 0.01, {}), (T0 + 25 * H, 0.02, {}), (T0 + 4 * H, -0.01, {})]
    assert d3.fit_slots(rows) == {1: 1, 4: -1}
