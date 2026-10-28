from decimal import Decimal as D

from crypto_robot.agents.strategy.rebalance_multi import MultiParams
from crypto_robot.core.models import Bar
from crypto_robot.lab.backtest import CostParams
from crypto_robot.lab.hourly_variants import all_variants, in_window, passes
from crypto_robot.lab.multi_backtest import run_multi

HOUR = 3_600_000
NO_COST = CostParams(D(0), D(0))
P3 = MultiParams((('eth', D('0.5')), ('usdt', D('0.375'))), D('0.10'))
UTC16 = 16 * HOUR                       # 台北 00:00


def hourly(prices, start=UTC16):
    return [Bar(start + i * HOUR, D(str(p)), D(str(p)), D(str(p)), D(str(p)), D(1)) for i, p in enumerate(prices)]


def test_in_window_is_taipei_0200_to_0700_opens():
    opens = [h for h in range(24) if in_window(UTC16 + h * HOUR)]    # h = 台北幾點
    assert opens == [2, 3, 4, 5, 6, 7]


def test_can_trade_holds_trigger_until_window():
    eth = hourly([100] + [200] * 5)                                   # 台北 01:00 收盤觸發
    usdt = hourly([30] * 6)
    free = run_multi({'eth': eth, 'usdt': usdt}, UTC16, UTC16 + 6 * HOUR, P3, NO_COST, initial=D(1000))
    gated = run_multi({'eth': eth, 'usdt': usdt}, UTC16, UTC16 + 6 * HOUR, P3, NO_COST, initial=D(1000),
                      can_trade=lambda ts: in_window(ts) and ts >= UTC16 + 4 * HOUR)
    first = lambda r: min(t.entry_ts for t in r.trades if t.reason.startswith('rebal'))
    assert first(free) == UTC16 + 2 * HOUR                            # 下一根開盤
    assert first(gated) == UTC16 + 4 * HOUR                           # 等到可以交易的那根


def test_grid_and_passes():
    vs = all_variants()
    assert len(vs) == 4 and len({v.name for v in vs}) == 4
    assert sum(v.can_trade is None for v in vs) == 2
    good = [{'ret': 0.3, 'base_ret': 0.2}, {'ret': 0.1, 'base_ret': 0.05}]
    assert passes(10, 0.5, 0.4, -0.3, -0.4, good, 0.45, 0.4) == (True, [])
    ok, fails = passes(3, 0.3, 0.4, -0.5, -0.4, [{'ret': 0.1, 'base_ret': 0.2}] * 2, 0.1, 0.2)
    assert not ok and len(fails) == 6
