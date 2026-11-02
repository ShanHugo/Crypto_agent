from decimal import Decimal as D

from crypto_robot.agents.data.resample import DAY_MS
from crypto_robot.core.models import Bar
from crypto_robot.lab import kronos_daily as kd
from crypto_robot.lab.common import COSTS
from crypto_robot.lab.direction3h import FEE, H, Series
from crypto_robot.lab.multi_backtest import rebalance_days, run_multi

S0 = kd.PERIODS['dev'][0]


def test_day_slots_are_taipei_midnight():
    ss = kd.day_slots(*kd.PERIODS['dev'])
    assert ss[0] == S0 and (S0 + 8 * H) % DAY_MS == 0
    assert ss[-1] < kd.PERIODS['dev'][1] and ss[1] - ss[0] == DAY_MS


def test_signal_mean_and_agree():
    assert kd.signal('h1_mean', [0.01] * 30) == 1
    assert kd.signal('h1_mean', [FEE / 2] * 30) == 0
    mixed = [0.05] * 23 + [-0.01] * 7                   # 平均看多，但只有 23 個同方向
    assert kd.signal('h1_mean', mixed) == 1 and kd.signal('h1_agree', mixed) == 0
    assert kd.signal('d1_agree', [-0.02] * 24 + [0.01] * 6) == -1


def test_dir1_rows_use_close_before_slot_and_24h_later():
    start = S0 - 2 * H
    bars = [Bar(start + i * H, D(1), D(1), D(1), D(100 + i), D(1)) for i in range(30)]
    eth = Series.of(bars)
    rows = kd.dir1_rows(eth, {S0: {'ret': [0.01] * 30}}, 'h1_mean', [S0])
    assert rows == [(S0, 125 / 101 - 1, {'h1_mean': 1})]


def test_high_vol_days_need_60_history():
    slots = [S0 + i * DAY_MS for i in range(62)]
    preds = {s: {'range': [0.02]} for s in slots}
    preds[slots[60]] = {'range': [0.05]}
    preds[slots[30]] = {'range': [0.09]}               # 前 60 天不判斷
    assert kd.high_vol_days(preds, slots) == {slots[60] - DAY_MS}


def test_spearman_and_vol_check():
    assert abs(kd.spearman([1, 2, 3, 4], [10, 20, 30, 40]) - 1) < 1e-12
    days, preds, slots = [], {}, []
    for i in range(10):
        r = D(1 + i % 3)
        days.append(Bar(S0 + i * DAY_MS, D(100), D(100) + r, D(100) - r, D(100), D(1)))
        if i:
            slots.append(S0 + i * DAY_MS)
            preds[S0 + i * DAY_MS] = {'range': [float(2 * r / 100)]}   # 完全猜中隔天價差
    vc = kd.vol_check(days, preds, slots)
    assert vc['n'] == 9 and vc['kronos'] > vc['naive'] and vc['passed']


def daily_series(prices):
    return {'eth': [Bar(S0 + i * DAY_MS, D(p), D(p), D(p), D(p), D(1)) for i, p in enumerate(prices)],
            'usdt': [Bar(S0 + i * DAY_MS, D(30), D(30), D(30), D(30), D(1)) for i in range(len(prices))]}


def test_decide_hook_replaces_band_check():
    days = daily_series([100, 150, 150, 150])           # 第 2 天 ETH 漲 50% 會觸發 band5
    end = S0 + 4 * DAY_MS
    assert rebalance_days(run_multi(days, S0, end, kd.BASE, COSTS)) == 1
    assert rebalance_days(run_multi(days, S0, end, kd.BASE, COSTS, decide=lambda *a: False)) == 0


def test_filter_delays_sell_when_up_predicted():
    days = daily_series([100, 150, 150, 150])
    end = S0 + 4 * DAY_MS
    slots = [S0 + i * DAY_MS for i in range(1, 5)]
    preds = {s: {'ret': [0.0], 'range': [0.01]} for s in slots}
    preds[S0 + 2 * DAY_MS] = {'ret': [0.02], 'range': [0.01]}   # 要賣 ETH 那天預測隔天漲 2% → 延後
    for name, delays in (('filter0.32', 1), ('filter1', 1)):
        rule = kd.rebalance_rule(name, preds, slots)
        res = run_multi(days, S0, end, kd.BASE, COSTS, decide=rule)
        assert rule.delays == [S0 + DAY_MS] and len(rule.delays) == delays
        assert rebalance_days(res) == 1                 # 隔天再看，預測 0 → 照常賣


def test_dir1_passed_thresholds():
    ok = {'n': 100, 'hit': 0.55, 'half_hit': [0.52, 0.6], 't': 2.5, 'long_net': 0.002, 'rand95': 0.0}
    assert kd.dir1_passed(ok, 'h1_mean')
    assert kd.dir1_passed({**ok, 'n': 60}, 'h1_agree') and not kd.dir1_passed({**ok, 'n': 99}, 'h1_mean')
    for k, v in [('hit', 0.549), ('t', 2.49), ('half_hit', [0.51, 0.6]), ('long_net', 0.0009)]:
        assert not kd.dir1_passed({**ok, k: v}, 'h1_mean')
    assert kd.dir1_ho_passed({'n': 50, 'hit': 0.53, 't': 2.0, 'long_net': 0.0001, 'rand95': 0.0})


def test_pick_prefers_dir1():
    res = {'dir1': {'h1_mean': {'passed': True, 'metrics': {'max': {'t': 3}, 'binance': {'t': 2.6}}},
                    'd1_mean': {'passed': True, 'metrics': {'max': {'t': 5}, 'binance': {'t': 2.5}}}},
           'dir23': {'vol_skip': {'passed': True, 'ret': 0.5, 'base_ret': 0.1}}}
    assert kd.pick(res) == 'h1_mean'
    res['dir1'] = {}
    assert kd.pick(res) == 'vol_skip'


def test_variants_fixed():
    assert kd.VARIANTS == ['h1_mean', 'h1_agree', 'd1_mean', 'd1_agree', 'vol_band10', 'vol_skip', 'filter0.32', 'filter1']
