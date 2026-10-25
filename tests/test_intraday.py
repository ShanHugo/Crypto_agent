from datetime import date, datetime
from decimal import Decimal as D

import pytest

from crypto_robot.agents.strategy.intraday import TAIPEI, BandRule, PullbackRule, in_trading_window, trend_ok
from crypto_robot.core.models import Bar
from crypto_robot.lab.backtest import CostParams
from crypto_robot.lab.intraday_backtest import MAKER_FEE, avg_fill, run_nights
from crypto_robot.lab.intraday_variants import all_variants, by_name, passes

COSTS = CostParams(D('0.0016'), D('0.001'))
NIGHT = date(2022, 3, 2)
STEP = 5 * 60_000


def ts(h, m, d=NIGHT):
    return int(datetime(d.year, d.month, d.day, h, m, tzinfo=TAIPEI).timestamp() * 1000)


def night(over=None, vol='100', price='100', drop=()):
    """前一天 23:00 到當天 07:25 的 5 分 K，預設價格不動、量充足；over[(h, m)] = (o, h, l, c[, v])。"""
    over = over or {}
    out, t = [], ts(23, 0, date(2022, 3, 1))
    while t <= ts(7, 25):
        hm = (datetime.fromtimestamp(t / 1000, TAIPEI).hour, datetime.fromtimestamp(t / 1000, TAIPEI).minute)
        if hm not in drop:
            o, h, l, c, *v = over.get(hm, (price,) * 4)
            out.append(Bar(t, D(o), D(h), D(l), D(c), D(v[0] if v else vol)))
        t += STEP
    return out


def daily_up(n=25):
    return [Bar(ts(0, 0, date(2022, 2, 1)) + i * 86_400_000, D(i + 1), D(i + 1), D(i + 1), D(i + 1), D(1))
            for i in range(n)]


def run(bars, rule=PullbackRule(D('0.01'), D('0.01')), use_filter=False, daily=None, **kw):
    return run_nights(bars, daily or daily_up(), ts(0, 0), ts(8, 0), rule, use_filter, COSTS, **kw)


def test_window_bounds():
    assert in_trading_window(ts(1, 30)) and in_trading_window(ts(7, 15))
    assert not in_trading_window(ts(1, 25)) and not in_trading_window(ts(7, 20))


def test_pullback_buy_then_take_profit_with_maker_fees():
    bars = night({(2, 0): ('100', '100', '98.9', '99'), (3, 0): ('99.5', '100.5', '99.5', '100')})
    out = run(bars)
    [t] = out.result.trades
    assert t.reason == 'take' and t.entry_price == D(99)
    # 下一根（02:05）開盤 100 已高於停利價 99.99，以開盤價成交
    expected = (D(100) * (1 - MAKER_FEE)) / (D(99) * (1 + MAKER_FEE)) - 1
    assert t.net_return == pytest.approx(expected)
    assert out.nights == 1 and out.hold.equity[-1][1] == D(100000)        # 時段內持有：01:30 和 07:20 都是 100


def test_touch_is_not_a_fill_and_gap_fills_at_open():
    assert run(night({(2, 0): ('100', '100', '99', '99')})).result.trades == []        # 只碰到 99 不算
    out = run(night({(2, 0): ('98', '98', '97.9', '98')}))
    assert out.result.trades[0].entry_price == D(98)                                    # 開盤就低於掛價


def test_volume_cap_limits_buy_and_forced_close_at_0720():
    bars = night({(2, 0): ('100', '100', '98.9', '99', '50')})
    out = run(bars, rule=PullbackRule(D('0.01'), D('0.05')))
    [t] = out.result.trades
    assert t.qty == D(5) and t.reason == 'close'                                        # 10% × 50 ETH
    assert float(avg_fill(out)) == pytest.approx(99 * 5 * (1 + 0.0008))
    tiny = night({(2, 0): ('100', '100', '98.9', '99', '2')})                            # 0.2 ETH ≈ 20 元 < 250
    assert run(tiny, rule=PullbackRule(D('0.01'), D('0.05'))).result.trades == []


def test_stop_loss_and_no_reentry():
    bars = night({(2, 0): ('100', '100', '98.9', '99'), (2, 30): ('99', '99', '94', '94'),
                  (3, 0): ('94', '94', '90', '92')})
    [t] = run(bars, rule=PullbackRule(D('0.01'), D('0.05'))).result.trades     # 03:00 再跌也不再進場
    assert t.reason == 'stop'
    assert t.exit_price < D(99) * D('0.95')


def test_max_three_round_trips():
    over = {}
    for k in range(5):
        over[(2 + k, 0)] = ('100', '100', '98.9', '99')
        over[(2 + k, 30)] = ('99.5', '100.5', '99.5', '100')
    assert len(run(night(over)).result.trades) == 3


def test_incomplete_night_skipped_and_filter():
    out = run(night(drop={(3, 0)}))
    assert out.skipped == 1 and out.nights == 0
    falling = [Bar(b.ts, b.open, b.high, b.low, D(30 - i), b.volume) for i, b in enumerate(daily_up())]
    out = run(night({(2, 0): ('100', '100', '98.9', '99')}), use_filter=True, daily=falling)
    assert out.filtered == 1 and out.result.trades == []
    out = run(night({(2, 0): ('100', '100', '98.9', '99')}), use_filter=True)
    assert out.nights == 1 and len(out.result.trades) == 1


def test_stress_costs_more():
    bars = night({(2, 0): ('100', '100', '98.9', '99'), (3, 0): ('99.5', '100.5', '99.5', '100')})
    normal, stress = run(bars), run(bars, stress=True)
    assert stress.result.trades[0].net_return < normal.result.trades[0].net_return


def test_band_rule_levels():
    closes = [D(100)] * 23 + [D(124)]
    r = BandRule(D('2'))
    assert r.exit_level(D(90), closes) == D(101)
    assert r.entry_level(D(100), closes) == D(101) - 2 * (D(23 * 1 + 23 * 23) / 24).sqrt()
    assert r.entry_level(D(100), closes[:10]) is None


def test_trend_ok():
    assert trend_ok([D(i) for i in range(1, 21)])
    assert not trend_ok([D(i) for i in range(20, 0, -1)])
    assert not trend_ok([D(1)] * 5)


def test_intraday_grid_and_passes():
    vs = all_variants()
    assert len(vs) == 16 and len({v.name for v in vs}) == 16
    assert by_name('pullback-d1.0-t0.5-trend').rule == PullbackRule(D('0.01'), D('0.005'))
    good = [{'ret': 0.05, 'hold_ret': 0.01}, {'ret': 0.04, 'hold_ret': -0.2}]
    assert passes(150, 3.2, -0.05, 0.01, 20_000, good) == (True, [])
    ok, fails = passes(50, 1.0, -0.2, -0.01, 3_000, [{'ret': -0.01, 'hold_ret': 0.0}, {'ret': 0.01, 'hold_ret': 0.02}])
    assert not ok and len(fails) == 8
