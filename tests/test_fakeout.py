from decimal import Decimal as D

from crypto_robot.core.models import Bar
from crypto_robot.lab import fakeout as fo
from crypto_robot.lab.direction3h import DEV_START as T0, FEE, H, Series

PRICES = [100] * 30 + [95, 96, 97, 101] + [101] * 30     # 第 30 根跌破、第 33 根收回


def hourly(prices):
    out, prev = [], prices[0]
    for i, p in enumerate(prices):
        out.append(Bar(T0 + i * H, D(str(prev)), D(str(max(prev, p))), D(str(min(prev, p))), D(str(p)), D(1)))
        prev = p
    return out


ETH = Series.of(hourly(PRICES))


def test_entries():
    assert fo.entry_ok(ETH, 30, 'break') and fo.entry_ok(ETH, 30, 'deep')
    assert not fo.entry_ok(ETH, 29, 'break')
    assert fo.entry_ok(ETH, 33, 'confirm') and not fo.entry_ok(ETH, 33, 'break')


def test_exits():
    assert fo.exit_ret(ETH, 30, fo.Variant('break', 'hold', 3)) == (101 / 95 - 1, 3)
    assert fo.exit_ret(ETH, 30, fo.Variant('break', 'tpsl', tp=0.01, sl=0.01)) == (0.01, 1)   # 第 31 根高 96 ≥ 95.95
    assert fo.exit_ret(ETH, 30, fo.Variant('break', 'tpsl', tp=0.2, sl=0.2)) == (101 / 95 - 1, 24)


def test_stop_first_when_both_hit():
    bars = hourly([100] * 60)
    bars[31] = Bar(T0 + 31 * H, D(100), D(106), D(94), D(100), D(1))    # 同一根碰到 +5% 與 -5%
    assert fo.exit_ret(Series.of(bars), 30, fo.Variant('break', 'tpsl', tp=0.05, sl=0.05)) == (-0.05, 1)


def test_simulate_one_trade_and_pool():
    trades, pool = fo.simulate(ETH, fo.Variant('break', 'hold', 3), T0, T0 + 64 * H)
    assert trades == [(T0 + 31 * H, 101 / 95 - 1 - FEE)]
    assert len(pool) >= 1


def test_variants_fixed():
    assert len(fo.VARIANTS) == 12 and len({v.name for v in fo.VARIANTS}) == 12
