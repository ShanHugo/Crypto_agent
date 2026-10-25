from datetime import datetime
from decimal import Decimal as D

import pytest

from crypto_robot.agents.strategy.intraday import TAIPEI
from crypto_robot.agents.strategy.rebalance import RebalanceParams
from crypto_robot.core.models import Market, OrderBook
from crypto_robot.paper.fills import market_buy, market_sell
from crypto_robot.paper.rebalance_account import PaperStore, in_window, run_day

MARKET = Market('ethtwd', 'eth', 'twd', 6, 1, D('0.003'), D(250), True)
P = RebalanceParams(D('0.5'), D('0.10'))
FEE = D('0.0016')


def at(day, h=1, m=35):
    return int(datetime(2026, 10, day, h, m, tzinfo=TAIPEI).timestamp() * 1000)


def book(mid, depth='1'):
    mid = D(mid)
    return OrderBook(0, ((mid - 5, D(depth)), (mid - 10, D(depth))), ((mid + 5, D(depth)), (mid + 10, D(depth))))


def test_market_fills_walk_the_book():
    b = book('1000', '1')
    f = market_buy(b, D('1.5'), FEE)
    assert f.qty == D('1.5') and f.gross == D(1005) + D('0.5') * 1010 and f.levels == 2
    assert f.fee == f.gross * FEE
    short = market_sell(b, D(5), FEE)
    assert short.qty == D(2)                         # 深度只有 2 ETH


def test_window():
    assert in_window(at(26, 1, 30)) and in_window(at(26, 7, 29))
    assert not in_window(at(26, 1, 29)) and not in_window(at(26, 7, 30))


def test_init_buys_half_and_sets_benchmarks(tmp_path):
    s = PaperStore(tmp_path / 'p.db')
    r = run_day(s, book('77000'), MARKET, P, FEE, at(26), D(6000))
    assert r['action'] == 'init'
    assert r['weight'] == pytest.approx(D('0.5'), abs=D('0.01'))
    assert r['cash'] > 0 and r['equity'] < D(6000)                          # 扣了手續費與價差
    assert r['hodl_equity'] < r['equity'] or r['hodl_equity'] > 0
    acct = s.account()
    assert acct.eth == r['eth'] and acct.static_eth == acct.eth
    assert str(acct.eth) == str(acct.eth.quantize(D('0.000001')))           # 精度 6 位
    assert run_day(s, book('77000'), MARKET, P, FEE, at(26, 3), D(6000))['status'] == 'already'


def test_hold_then_sell_after_rally_then_buy_after_drop(tmp_path):
    s = PaperStore(tmp_path / 'p.db')
    run_day(s, book('77000'), MARKET, P, FEE, at(20), D(6000))
    assert run_day(s, book('85000'), MARKET, P, FEE, at(21), D(6000))['action'] == 'hold'
    r = run_day(s, book('120000'), MARKET, P, FEE, at(22), D(6000))
    assert r['action'] == 'sell' and r['weight'] == pytest.approx(D('0.5'), abs=D('0.01'))
    r = run_day(s, book('70000'), MARKET, P, FEE, at(24), D(6000))
    assert r['action'] == 'buy'
    assert any('漏跑' in n for n in r['note'])
    assert [t[1] for t in s.trades()] == ['buy', 'sell', 'buy']
    assert len(s.snapshots()) == 4


def test_too_small_and_dry_run(tmp_path):
    s = PaperStore(tmp_path / 'p.db')
    run_day(s, book('77000'), MARKET, P, FEE, at(20), D(600))               # 小帳戶：300 元的 ETH
    r = run_day(s, book('140000'), MARKET, P, FEE, at(21), D(600), write=False)
    assert r['action'] == 'too-small'                                        # 比例約 64%，要賣約 120 元 < 250
    assert not s.has_snapshot(r['date'])
    with pytest.raises(ValueError):
        run_day(PaperStore(tmp_path / 'q.db'), book('77000'), MARKET, P, FEE, at(20), D(300))
