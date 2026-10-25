from decimal import Decimal as D

import pytest

from crypto_robot.agents.data.resample import DAY_MS, HOUR_MS, day_start, to_daily
from crypto_robot.agents.strategy.daily_rules import MeanRevSMA, RsiRevert, TrendSMA, rsi, sma
from crypto_robot.core.models import Bar
from crypto_robot.lab.backtest import CostParams
from crypto_robot.lab.daily_backtest import run_rule
from crypto_robot.lab.daily_variants import all_variants, by_name, passes

NO_COST = CostParams(D(0), D(0))
DAY0 = 1_700_000_000_000 // DAY_MS * DAY_MS - 8 * HOUR_MS    # 某個台北日 00:00


def daily(closes):
    out, prev = [], D(str(closes[0]))
    for i, c in enumerate(closes):
        c = D(str(c))
        out.append(Bar(DAY0 + i * DAY_MS, prev, max(prev, c), min(prev, c), c, D(1)))
        prev = c
    return out


# ---- 日 K 合成 ----

def test_day_boundary_is_taipei_midnight():
    assert day_start(DAY0 + 5 * HOUR_MS) == DAY0
    assert day_start(DAY0 - 1) == DAY0 - DAY_MS
    assert (DAY0 + 8 * HOUR_MS) % DAY_MS == 0      # 台北 00:00 = UTC 16:00 前一天，對齊後 +8h 是 UTC 整日


def test_to_daily_aggregates_and_drops_incomplete_days():
    hours = [Bar(DAY0 - 2 * HOUR_MS + i * HOUR_MS, D(10 + i), D(20 + i), D(5 + i), D(11 + i), D(2))
             for i in range(2 + 24 + 3)]                   # 前一天只有 2 根、完整 1 天、下一天只有 3 根
    [d] = to_daily(hours)
    assert d.ts == DAY0
    assert (d.open, d.close) == (D(12), D(11 + 25))
    assert (d.high, d.low, d.volume) == (D(20 + 25), D(7), D(48))


# ---- 指標與規則 ----

def test_sma_and_rsi():
    assert sma([D(1), D(2), D(3), D(4)], 2) == [None, D('1.5'), D('2.5'), D('3.5')]
    up = rsi([D(i) for i in range(20)], 14)
    assert up[13] is None and up[14] == D(100)
    down = rsi([D(100 - i) for i in range(20)], 14)
    assert down[-1] == D(0)


def test_trend_sma_confirm_days():
    bars = daily([10, 10, 10, 12, 13, 9, 8])
    rule = TrendSMA(3, confirm=2).prepare(bars)
    assert rule.decide(3, None) is None          # 只站上 1 天
    assert rule.decide(4, None) == 'buy'
    assert rule.decide(5, {'entry_price': D(13), 'entry_i': 4}) is None
    assert rule.decide(6, {'entry_price': D(13), 'entry_i': 4}) == 'sell'


def test_meanrev_buy_low_sell_at_mean_stop_and_time():
    bars = daily([100, 100, 100, 100, 85, 70, 80, 101])
    r = MeanRevSMA(D('0.10'), n=4).prepare(bars)
    assert r.decide(4, None) == 'buy'                            # 85 ≤ 均線 96.25 × 0.9
    pos = {'entry_price': D(85), 'entry_i': 4}
    assert r.decide(6, pos) is None and r.decide(7, pos) == 'sell'    # 回到均線才賣
    assert MeanRevSMA(D('0.10'), n=4, stop=D('0.15')).prepare(bars).decide(5, pos) == 'sell'
    assert MeanRevSMA(D('0.10'), n=4, max_hold_days=2).prepare(bars).decide(6, pos) == 'sell'


def test_rsi_revert():
    bars = daily([100 - i for i in range(30)] + [70 + 3 * i for i in range(15)])
    r = RsiRevert(30, 55).prepare(bars)
    assert r.decide(29, None) == 'buy'
    assert r.decide(44, {'entry_price': D(71), 'entry_i': 30}) == 'sell'


# ---- 回測 ----

def test_run_rule_trades_next_open_with_costs():
    bars = daily([10, 10, 10, 12, 13, 9, 8, 8])
    res = run_rule(bars, bars[0].ts, bars[-1].ts + DAY_MS, TrendSMA(3, 1), NO_COST, initial=D(1000))
    [t] = res.trades
    assert t.entry_ts == bars[4].ts and t.entry_price == bars[4].open     # 第 3 天站上，隔天開盤買
    assert t.exit_ts == bars[6].ts and t.exit_price == bars[6].open       # 第 5 天跌破，隔天開盤賣
    assert res.equity[-1][1] == D(1000) * bars[6].open / bars[4].open
    with_cost = run_rule(bars, bars[0].ts, bars[-1].ts + DAY_MS, TrendSMA(3, 1),
                         CostParams(D('0.001'), D('0.001')), initial=D(1000))
    assert with_cost.equity[-1][1] < res.equity[-1][1] and with_cost.costs > 0


# ---- 規則組合與通過標準 ----

def test_daily_grid():
    vs = all_variants()
    assert len(vs) == 22 and len({v.name for v in vs}) == 22
    assert by_name('meanrev-sma20-dev15-hold30-stop15').rule == MeanRevSMA(D('0.15'), 20, 30, D('0.15'))


def h(ret, mdd, hodl_ret, hodl_mdd):
    return {'ret': ret, 'mdd': mdd, 'hodl_ret': hodl_ret, 'hodl_mdd': hodl_mdd}


def test_daily_passes():
    halves = [h(0.3, -0.3, 0.5, -0.8), h(0.5, -0.15, 1.2, -0.44)]       # 報酬輸，但報酬÷回撤贏
    assert passes(10, -0.4, -0.8, halves) == (True, [])
    ok, fails = passes(5, -0.6, -0.8, [h(0.3, -0.9, 0.5, -0.8), h(-0.1, -0.2, 1.2, -0.44)])
    assert not ok
    assert any('交易' in f for f in fails) and any('回撤' in f and '淺' in f for f in fails)
    assert any('後半報酬' in f for f in fails) and any('前半回撤' in f for f in fails)
