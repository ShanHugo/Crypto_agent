from decimal import Decimal as D

from crypto_robot.core.models import Bar
from crypto_robot.forecast import funding_long as fl
from crypto_robot.lab.hourly import H, Series
from crypto_robot.lab.futures import FEE, Funding

S0 = fl.START_MS


def setup(prices_after=110.0):
    """S0 前 100 期費率交替 0／0.0001，S0 起連 3 期 -0.001 → 第 3 期（S0+16h）做多。"""
    rates = [(S0 - (100 - i) * 8 * H, 0.0 if i % 2 else 0.0001) for i in range(100)]
    rates += [(S0 + i * 8 * H, -0.001) for i in range(3)]
    start = S0 - 30 * H
    n = 30 + 16 + 8 + 5
    prices = [100.0] * (30 + 16) + [prices_after] * (n - 46)
    bars = [Bar(start + i * H, D(str(p)), D(str(p)), D(str(p)), D(str(p)), D(1)) for i, p in enumerate(prices)]
    return Series.of(bars), Funding(rates)


def test_one_long_trade_after_start():
    eth, fund = setup()
    trades, pool = fl.simulate(eth, fund)
    assert len(trades) == 1 and trades[0]['ts'] == S0 + 16 * H
    assert abs(trades[0]['net'] - (0.10 - FEE)) < 1e-12
    assert len(pool) == 3


def test_update_appends_once(tmp_path):
    eth, fund = setup()
    p = tmp_path / 'log.jsonl'
    rows, _, added = fl.update(eth, fund, p)
    assert added == 1 and len(rows) == 1
    rows, _, added = fl.update(eth, fund, p)
    assert added == 0 and len(rows) == 1 and len(p.read_text().splitlines()) == 1


def test_status_checkpoints():
    rows = [{'net': 0.001}] * 49
    assert 'stopped' not in fl.status(rows, [0.0])
    assert fl.status(rows + [{'net': 0.001}], [0.0])['stopped'] is False
    assert fl.status(rows + [{'net': 0.001}], [0.002])['stopped'] is True        # 輸給每期都做多
    bad = [{'net': -0.001}] * 50
    assert fl.status(bad, [-0.01])['stopped'] is True
    good = [{'net': 0.002 + (0.001 if i % 2 else -0.001)} for i in range(150)]
    assert fl.status(good, [0.0])['final_pass'] is True
    assert '中途檢查 50 筆' in fl.text(fl.status([], []), 0)
    assert '還差 40 筆' in fl.text(fl.status([{'net': 0.001}] * 10, [0.0]), 2)
