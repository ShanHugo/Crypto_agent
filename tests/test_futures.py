from decimal import Decimal as D

from crypto_robot.core.models import Bar
from crypto_robot.lab import futures as fu
from crypto_robot.lab.direction3h import DEV_START as T0, H, Series


def hourly(prices):
    out, prev = [], prices[0]
    for i, p in enumerate(prices):
        out.append(Bar(T0 + i * H, D(str(prev)), D(str(max(prev, p))), D(str(min(prev, p))), D(str(p)), D(1)))
        prev = p
    return out


def test_funding_between_and_rounding():
    f = fu.Funding([(T0 + 8 * H + 3, 0.001), (T0, 0.002), (T0 + 16 * H, -0.001)])
    assert f.ts == [T0, T0 + 8 * H, T0 + 16 * H]
    assert f.between(T0, T0 + 8 * H) == 0.001           # (a, b]：進場那一期不算、出場那一期算
    assert f.between(T0, T0 + 16 * H) == 0.0


def test_trade_net_long_pays_short_receives():
    eth = Series.of(hourly([100] * 10 + [110] * 20))
    f = fu.Funding([(T0 + 12 * H, 0.001)])
    s = T0 + 10 * H                                      # 進場價＝第 9 根收盤 100
    assert abs(fu.trade_net(eth, f, s, 8, 1) - (0.10 - 0.001 - fu.FEE)) < 1e-12
    assert abs(fu.trade_net(eth, f, s, 8, -1) - (-0.10 + 0.001 - fu.FEE)) < 1e-12


def test_fund_ext_signal():
    rates = [0.0001] * 90 + [0.001]
    f = fu.Funding([(T0 + i * 8 * H, r) for i, r in enumerate(rates)])
    v = fu.Variant('fund_ext', 8)
    assert fu.signal(v, None, f, f.ts[90], 90) == -1
    f2 = fu.Funding([(T0 + i * 8 * H, r) for i, r in enumerate(rates[:90] + [-0.001])])
    assert fu.signal(v, None, f2, f2.ts[90], 90) == 1


def test_fund_streak_signal():
    rates = [0.0, 0.0001] * 46 + [0.01, 0.01, 0.01]
    f = fu.Funding([(T0 + i * 8 * H, r) for i, r in enumerate(rates)])
    assert fu.signal(fu.Variant('fund_streak', 8), None, f, f.ts[94], 94) == -1
    assert fu.signal(fu.Variant('fund_streak', 8), None, f, f.ts[93], 93) == 0     # 只有 2 期偏高


def test_fake_signal_and_no_overlap():
    eth = Series.of(hourly([100] * 30 + [105] + [100] * 30))
    v = fu.Variant('fake', 6)
    s = T0 + 31 * H                                      # 第 30 根收盤 105 突破 24h 高 → 做空
    assert fu.signal(v, eth, None, s, None) == -1
    trades, pool = fu.simulate(eth, fu.Funding([]), v, T0, T0 + 55 * H)
    assert len(trades) == 1 and trades[0][:2] == (s, -1)
    assert abs(trades[0][2] - (1 - 100 / 105 - fu.FEE)) < 1e-12
    assert len(pool[1]) == len(pool[-1])


def test_variants_fixed():
    assert [v.name for v in fu.VARIANTS] == ['fund_ext-hold8', 'fund_ext-hold24', 'fund_streak-hold8',
                                             'fund_streak-hold24', 'fake-hold3', 'fake-hold6']
