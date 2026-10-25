import json
from decimal import Decimal as D

import pytest

from crypto_robot.agents.strategy.ema_cross import StrategyParams, crosses, ema, entry_signals, volume_ok
from crypto_robot.core.models import Bar
from crypto_robot.lab.backtest import CostParams, ExitParams, buy_and_hold, run
from crypto_robot.lab.metrics import max_drawdown, segment_return, stats, t_stat
from crypto_robot.lab.research import run_holdout
from crypto_robot.lab.variants import BASELINE, all_variants, by_name, passes

P = 3_600_000
T0 = 1_700_000_000_000 // P * P
NO_COST = CostParams(D(0), D(0))
SP = StrategyParams(ema_fast=2, ema_slow=4, volume_lookback=2, use_volume_filter=False, warmup_bars=4)


def bar(i, o, h=None, l=None, c=None, v='1'):
    o = D(str(o))
    c = o if c is None else D(str(c))
    h = max(o, c) if h is None else D(str(h))
    l = min(o, c) if l is None else D(str(l))
    return Bar(T0 + i * P, o, h, l, c, D(v))


def series(closes, vols=None):
    """開盤 = 前一根收盤，高低 = 開收的範圍。"""
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(bar(i, prev, c=c, v=str(vols[i]) if vols else '1'))
        prev = c
    return out


# ---- 策略 ----

def test_ema_matches_hand_calculation():
    assert ema([D(10), D(20), D(30)], 3) == [D(10), D(15), D('22.5')]


def test_cross_up_and_down_after_warmup():
    bars = series([10] * 6 + [20, 20, 20] + [5, 5, 5])
    up, down = crosses(bars, SP)
    assert up.index(True) == 6
    assert down.index(True) == 9
    assert sum(up) == 1 and sum(down) == 1


def test_no_signal_during_warmup():
    bars = series([10, 20, 20, 20, 20])
    up, _ = crosses(bars, StrategyParams(ema_fast=2, ema_slow=4, warmup_bars=4, volume_lookback=2))
    assert not any(up)


def test_volume_filter_excludes_current_bar_and_zero_volume():
    bars = series([10] * 5, vols=[1, 3, 2, 5, 0])
    assert volume_ok(bars, 3, 2) is True         # 5 > (3+2)/2
    assert volume_ok(bars, 2, 2) is False        # 2 = (1+3)/2，必須嚴格大於
    assert volume_ok(bars, 4, 2) is False        # 成交量 0
    assert volume_ok(bars, 1, 2) is False        # 前面不夠 2 根


def test_entry_requires_volume_when_filter_on():
    closes = [10] * 6 + [20, 20]
    quiet = series(closes, vols=[5] * 6 + [1, 1])
    loud = series(closes, vols=[1] * 6 + [9, 1])
    sp = StrategyParams(ema_fast=2, ema_slow=4, volume_lookback=2, warmup_bars=4)
    assert not any(entry_signals(quiet, sp))
    assert entry_signals(loud, sp)[6]


def test_params_validation():
    with pytest.raises(ValueError):
        StrategyParams(ema_fast=50, ema_slow=20)
    with pytest.raises(ValueError):
        StrategyParams(ema_fast=20, ema_slow=50, warmup_bars=10)


# ---- 回測 ----

def run_simple(bars, xp=ExitParams(stop_loss_pct=D('0.05'), use_entry_bar_low=False), cp=NO_COST, **kw):
    return run(bars, bars[0].ts, bars[-1].ts + P, SP, xp, cp, initial=D(1000), **kw)


def test_enters_next_open_and_stops_at_stop_price():
    bars = series([10] * 6 + [20]) + [bar(7, 21, h=22, l=21), bar(8, 21, h=21, l=19, c=19.5)]
    res = run_simple(bars)
    [t] = res.trades
    assert t.entry_ts == bars[7].ts and t.entry_price == D(21)
    assert t.reason == 'stop' and t.exit_price == D('19.95')       # 21 × 0.95
    assert t.net_return == D('-0.05')
    assert res.equity[-1][1] == D(1000) * D('0.95')


def test_gap_below_stop_fills_at_open():
    bars = series([10] * 6 + [20]) + [bar(7, 21), bar(8, 15, h=16, l=14)]
    [t] = run_simple(bars).trades
    assert t.reason == 'gap' and t.exit_price == D(15)


def test_entry_bar_low_raises_stop():
    bars = series([10] * 6 + [20]) + [bar(7, 21), bar(8, 20, h=21, l=10)]
    xp = ExitParams(stop_loss_pct=D('0.5'), use_entry_bar_low=True)       # 21 × 0.5 = 10.5 > 訊號 K 線 low 10
    [t] = run_simple(bars, xp).trades
    assert t.exit_price == D('10.5')
    bars2 = series([10] * 6 + [20]) + [bar(7, 21), bar(8, 20, h=21, l=11)]
    bars2[6] = bar(6, 10, h=20, l=12, c=20)                                 # 訊號 K 線 low 12 > 10.5
    [t2] = run_simple(bars2, xp).trades
    assert t2.exit_price == D(12)


def test_cross_down_exit_next_open():
    bars = series([10] * 6 + [20, 20, 20, 5]) + [bar(10, 6), bar(11, 6)]
    xp = ExitParams(stop_loss_pct=D('0.9'), use_entry_bar_low=False, exit_on_cross_down=True)
    [t] = run_simple(bars, xp).trades
    assert t.reason == 'cross' and t.exit_ts == bars[10].ts and t.exit_price == D(6)


def test_costs_applied_both_sides():
    bars = series([10] * 6 + [20]) + [bar(7, 20), bar(8, 20, l=10)]
    cp = CostParams(taker_fee=D('0.01'), slippage=D('0.01'))
    res = run_simple(bars, cp=cp)
    [t] = res.trades
    assert t.entry_price == D('20.2')
    assert res.equity[-1][1] < D(1000) * D('0.95')
    assert res.costs > 0


def test_open_position_at_end_marked_to_close():
    bars = series([10] * 6 + [20]) + [bar(7, 20, c=22)]
    res = run_simple(bars)
    [t] = res.trades
    assert t.reason == 'open' and t.exit_price == D(22)
    s = stats(res)
    assert s.trades == 0 and s.open_at_end


def test_only_one_position_and_no_trading_before_start():
    bars = series([10] * 6 + [20, 20, 20, 5, 5, 5, 30, 30])
    res = run(bars, bars[8].ts, bars[-1].ts + P, SP, ExitParams(D('0.9'), False), NO_COST, initial=D(1000))
    assert res.equity[0][0] == bars[8].ts
    assert [t.entry_ts for t in res.trades] == [bars[13].ts]           # 第 6 根的訊號在起點前，不算


def test_buy_and_hold():
    bars = series([10, 10, 20, 15])
    res = buy_and_hold(bars, bars[0].ts, bars[-1].ts + P, NO_COST, initial=D(1000))
    assert res.equity[-1][1] == D(1500)
    assert max_drawdown([float(v) for _, v in res.equity]) == pytest.approx(-0.25)
    assert segment_return(res, bars[2].ts, bars[4].ts if len(bars) > 4 else bars[-1].ts + P) == pytest.approx(0.5)


def test_t_stat():
    assert t_stat([0.01]) == 0.0
    assert t_stat([0.01, 0.03]) == pytest.approx(2.0)
    assert t_stat([-0.5] * 5) == float('-inf')       # 固定停損：每筆一樣


# ---- 規則組合與通過標準 ----

def test_variant_grid():
    vs = all_variants()
    assert len(vs) == 64 and len({v.name for v in vs}) == 64
    base = by_name(BASELINE)
    assert (base.strategy.ema_fast, base.strategy.ema_slow, base.strategy.use_volume_filter) == (20, 50, True)
    assert base.exit == ExitParams(D('0.01'), True, False)


def h(ret, mdd, hodl_ret, hodl_mdd):
    return {'ret': ret, 'mdd': mdd, 'hodl_ret': hodl_ret, 'hodl_mdd': hodl_mdd}


def test_passes_rules():
    good = [h(0.2, -0.1, 0.1, -0.3), h(0.1, -0.05, 0.3, -0.5)]         # 後半報酬輸但風險調整後贏
    assert passes(40, 3.5, good) == (True, [])
    ok, fails = passes(10, 1.0, [h(-0.1, -0.2, 0.5, -0.3), h(0.1, -0.2, 0.5, -0.3)])
    assert not ok and len(fails) == 4


def test_holdout_runs_once(tmp_path, monkeypatch):
    path = tmp_path / 'holdout.json'
    path.write_text(json.dumps({'variant': 'x', 'passed': False}), encoding='utf-8')
    monkeypatch.setattr('crypto_robot.lab.research.evaluate', lambda *a: pytest.fail('不應重算'))
    assert run_holdout([], by_name(BASELINE), 0, path, 'abc')['variant'] == 'x'
