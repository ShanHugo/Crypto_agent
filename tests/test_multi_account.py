from datetime import datetime
from decimal import Decimal as D

import pytest

from crypto_robot.core.models import Market, OrderBook
from crypto_robot.core.timeutil import TAIPEI
from crypto_robot.lab.multi_variants import SHADOW, by_name
from crypto_robot.paper.multi_account import MultiPaperStore, day_report, run_day

MARKETS = {'eth': Market('ethtwd', 'eth', 'twd', 6, 1, D('0.003'), D(250), True),
           'usdt': Market('usdttwd', 'usdt', 'twd', 2, 3, D(8), D(250), True)}
P = by_name(SHADOW).params
FEE = D('0.0016')


def at(day, h=1, m=35):
    return int(datetime(2026, 10, day, h, m, tzinfo=TAIPEI).timestamp() * 1000)


def book(mid, depth):
    mid, step = D(mid), D(mid) / 10000
    return OrderBook(0, ((mid - step, D(depth)), (mid - 2 * step, D(depth))), ((mid + step, D(depth)), (mid + 2 * step, D(depth))))


def books(eth='77000', usdt='32.75'):
    return {'eth': book(eth, '5'), 'usdt': book(usdt, '100000')}


def test_init_buys_targets(tmp_path):
    s = MultiPaperStore(tmp_path / 's.db')
    r = run_day(s, books(), MARKETS, P, FEE, at(27), D(6000))
    assert r['action'] == 'init' and {t['asset'] for t in r['trades']} == {'eth', 'usdt'}
    assert r['weights']['eth'] == pytest.approx(D('0.5'), abs=D('0.01'))
    assert r['weights']['usdt'] == pytest.approx(D('0.375'), abs=D('0.01'))
    assert r['cash'] > 0 and r['equity'] < D(6000)
    assert str(r['holdings']['usdt']) == str(r['holdings']['usdt'].quantize(D('0.01')))      # USDT 精度 2 位
    assert run_day(s, books(), MARKETS, P, FEE, at(27, 3), D(6000))['status'] == 'already'


def test_rebalance_after_eth_rally_and_report(tmp_path):
    s = MultiPaperStore(tmp_path / 's.db')
    run_day(s, books(), MARKETS, P, FEE, at(26), D(6000))
    assert run_day(s, books('85000'), MARKETS, P, FEE, at(27), D(6000))['action'] == 'hold'
    r = run_day(s, books('130000'), MARKETS, P, FEE, at(28), D(6000))
    assert r['action'] == 'rebalance'
    sides = {(t['asset'], t['side']) for t in r['trades']}
    assert ('eth', 'sell') in sides
    assert r['weights']['eth'] == pytest.approx(D('0.5'), abs=D('0.01'))
    rep = day_report(s, r['date'])
    assert rep['equity'] == r['equity'] and rep['trades'] == r['trades'] and rep['weights'] == r['weights']
    assert day_report(s, r['date'].replace(day=25)) is None


def test_dry_run_does_not_write(tmp_path):
    s = MultiPaperStore(tmp_path / 's.db')
    r = run_day(s, books(), MARKETS, P, FEE, at(27), D(6000), write=False)
    assert r['action'] == 'init' and s.account() is None and not s.has_snapshot(r['date'])


def test_import_two_asset_account_then_rebalances_into_usdt(tmp_path):
    from crypto_robot.agents.strategy.rebalance import RebalanceParams
    from crypto_robot.paper import rebalance_account as ra
    from crypto_robot.paper.multi_account import import_two_asset
    old = ra.PaperStore(tmp_path / 'old.db')
    eth_market = MARKETS['eth']
    ra.run_day(old, books()['eth'], eth_market, RebalanceParams(D('0.5'), D('0.10')), FEE, at(26), D(6000))
    new = MultiPaperStore(tmp_path / 'new.db')
    params = by_name('rebal3-eth50-cashusdt75-band5').params
    assert import_two_asset(old, new, params) == 1
    acct = new.account()
    assert acct['holdings']['usdt'] == 0 and acct['holdings']['eth'] == old.account().eth and acct['cash'] == old.account().cash
    assert [t['asset'] for t in new.trade_rows()] == ['eth'] and new.history()[0]['action'] == 'init'
    with pytest.raises(ValueError):
        import_two_asset(old, new, params)                                   # 不覆蓋
    r = run_day(new, books(), MARKETS, params, FEE, at(27), D(6000))
    assert r['action'] == 'rebalance' and ('usdt', 'buy') in {(t['asset'], t['side']) for t in r['trades']}
    assert r['weights']['usdt'] == pytest.approx(D('0.375'), abs=D('0.01'))
