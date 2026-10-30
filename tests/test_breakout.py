from decimal import Decimal as D

from crypto_robot.core.models import Bar
from crypto_robot.lab.breakout_research import Event, evaluate, find_events, is_signal, net_return
from crypto_robot.lab.breakout_variants import DAY_MS, BreakoutVariant, all_variants, passes_dev, passes_holdout

V = BreakoutVariant('t', 60, 5)


def bar(i, price, vol=10_000, high=None):
    p = D(str(price))
    return Bar(i * DAY_MS, p, D(str(high)) if high else p, p, p, D(vol))


def coin(breakout_day=70, n=90):
    """價格 100 橫盤，每天成交額 100 萬；breakout_day 收盤 120、成交額 4 倍。"""
    out = []
    for i in range(n):
        if i == breakout_day:
            out.append(Bar(i * DAY_MS, D(100), D(121), D(100), D(120), D(40_000) * 100 / 120))
        else:
            out.append(bar(i, 120 if i > breakout_day else 100))
    return out


def test_signal_conditions():
    bars = coin()
    assert is_signal(bars, 70, 60) and not is_signal(bars, 69, 60)
    quiet = coin()
    quiet[70] = Bar(70 * DAY_MS, D(100), D(121), D(100), D(120), D(10_000))      # 沒帶量
    assert not is_signal(quiet, 70, 60)
    thin = [Bar(b.ts, b.open, b.high, b.low, b.close, b.volume / 10) for b in coin()]   # 流動性不足
    assert not is_signal(thin, 70, 60)
    wide = coin()
    wide[30] = bar(30, 100, high=150)                                              # 區間 > 40%
    assert not is_signal(wide, 70, 60)


def test_events_entry_exit_and_period_end():
    ev = find_events('x', coin(), V, 0, 90 * DAY_MS)
    assert len(ev) == 1 and ev[0].entry.ts == 71 * DAY_MS and ev[0].exit.ts == 76 * DAY_MS
    assert find_events('x', coin(), V, 0, 76 * DAY_MS) == []                      # 賣出日超出區間就不算
    gap = [b for b in coin() if b.ts != 73 * DAY_MS]
    assert find_events('x', gap, V, 0, 90 * DAY_MS) == []                         # 持有期間缺資料


def test_net_return_costs():
    e, x = bar(0, 100), bar(1, 110)
    assert abs(net_return(e, x, D(0)) - (110 * 0.9984 / (100 * 1.0016) - 1)) < 1e-12


def test_evaluate_excess_vs_eth():
    data = {'eth': [bar(i, 100) for i in range(90)], 'x': coin()}
    r = evaluate(data, V, 0, 90 * DAY_MS, 45 * DAY_MS)
    assert r['n'] == 1 and r['coins'] == 1 and r['mean_eth'] == 0
    assert r['mean_excess'] == r['mean_ret'] < 0                                   # 進場後價格持平，只剩成本
    assert not r['passed']


def test_grid_and_criteria():
    assert [v.name for v in all_variants()] == ['breakout-n60-h14', 'breakout-n60-h30', 'breakout-n120-h14', 'breakout-n120-h30']
    assert passes_dev(40, 6, 3.2, [0.01, 0.02], 0.97, 0.01, 0.005) == (True, [])
    ok, fails = passes_dev(10, 2, 1.0, [-0.01, 0.0], 0.5, -0.01, -0.01)
    assert not ok and len(fails) == 7
    assert passes_holdout(20, 0.01, 0.95) == (True, [])
    assert len(passes_holdout(5, -0.01, 0.5)[1]) == 3
