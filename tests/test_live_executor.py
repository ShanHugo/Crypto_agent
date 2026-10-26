import asyncio
import base64
import json
from datetime import datetime
from decimal import Decimal as D

import pytest

from crypto_robot.core.models import Market
from crypto_robot.core.timeutil import TAIPEI
from crypto_robot.exchange.max.private import MaxTradingClient
from crypto_robot.exchange.max.public import MaxApiError
from crypto_robot.lab.multi_variants import by_name
from crypto_robot.live import executor as ex

MARKETS = {'eth': Market('ethtwd', 'eth', 'twd', 6, 1, D('0.003'), D(250), True),
           'usdt': Market('usdttwd', 'usdt', 'twd', 2, 3, D(8), D(250), True)}
PRICES = {'eth': D(80000), 'usdt': D('32.5')}
CFG = ex.LiveConfig(params=by_name('rebal3-eth50-cashusdt75-band5').params, markets={'eth': 'ethtwd', 'usdt': 'usdttwd'},
                    capital_cap=D(9000))
NOW = int(datetime(2026, 10, 27, 1, 36, tzinfo=TAIPEI).timestamp() * 1000)
FEE = D('0.0016')


class FakeExchange:
    """模擬 MAX：ioc_limit 在價格允許時以中間價 ±0.1% 全部成交，扣手續費；可以設定各種失敗。"""

    def __init__(self, balances, prices=PRICES, *, reject=None, timeout_create=None, lose=None, stuck=None):
        self.bal = {c: {'balance': D(str(v)), 'locked': D(0)} for c, v in balances.items()}
        self.prices = prices
        self.orders = {}
        self.sent = []
        self.cancelled = []
        self.reject, self.timeout_create, self.lose, self.stuck = reject or set(), timeout_create or set(), lose or set(), stuck or set()

    async def balances(self):
        return {c: dict(b) for c, b in self.bal.items() if b['balance'] or b['locked']}

    async def create_order(self, market, side, volume, *, ord_type, client_oid, price=None):
        asset = market.replace('twd', '')
        self.sent.append((asset, side, volume, price, ord_type, client_oid))
        if asset in self.reject:
            raise MaxApiError(422, '{"error":{"code":2007,"message":"balance not enough"}}', '/api/v3/wallet/spot/order')
        if asset in self.lose:
            raise asyncio.TimeoutError()                       # 送出後結果不明，而且交易所沒收到
        mid = self.prices[asset]
        fill = mid * (D('1.001') if side == 'buy' else D('0.999'))
        ok = fill <= price if side == 'buy' else fill >= price
        state = 'wait' if asset in self.stuck else ('done' if ok else 'cancel')
        executed = volume if state == 'done' else D(0)
        if executed:
            gross = executed * fill
            if side == 'buy':
                self.bal['twd']['balance'] -= gross * (1 + FEE)
                self.bal.setdefault(asset, {'balance': D(0), 'locked': D(0)})['balance'] += executed
            else:
                self.bal[asset]['balance'] -= executed
                self.bal['twd']['balance'] += gross * (1 - FEE)
        self.orders[client_oid] = {'id': len(self.orders) + 1, 'client_oid': client_oid, 'state': state,
                                   'executed_volume': str(executed), 'avg_price': str(fill if executed else 0)}
        if asset in self.timeout_create:
            raise asyncio.TimeoutError()                       # 交易所收到了，但回應逾時
        return self.orders[client_oid]

    async def order(self, client_oid):
        if client_oid not in self.orders:
            raise MaxApiError(404, '{"error":{"code":2004,"message":"order not found"}}', '/api/v3/order')
        return self.orders[client_oid]

    async def cancel(self, client_oid):
        self.cancelled.append(client_oid)
        self.orders[client_oid]['state'] = 'cancel'
        return self.orders[client_oid]


def run(fx, store, **kw):
    return asyncio.run(ex.run_live(fx, store, kw.pop('prices', PRICES), MARKETS, kw.pop('cfg', CFG), NOW, online=kw.pop('online', True),
                                   prev_prices=kw.pop('prev', None), poll=0, tries=3, clock=lambda: NOW, **kw))


def test_client_oid_and_limit_prices():
    oid = ex.new_client_oid(NOW)
    assert oid.startswith('cr2-20261027013600-') and len(oid) == 27
    assert ex.limit_price(D('76389.15'), 'buy', D('0.005'), 1) == D('76771.0')      # 76771.0957 捨去
    assert ex.limit_price(D('76389.15'), 'sell', D('0.005'), 1) == D('76007.3')     # 76007.20 進位
    assert ex.limit_price(D('32.75'), 'buy', D('0.005'), 3) == D('32.913')


def test_guards():
    h = {'eth': D(0), 'usdt': D(0)}
    g = lambda **k: ex.check_guards(k.get('h', h), k.get('cash', D(6000)), k.get('locked', D(0)), PRICES, k.get('prev'), CFG, k.get('online', True))  # noqa: E731
    assert g() is None
    assert 'online' in g(online=False)
    assert '凍結' in g(locked=D(1))
    assert '上限' in g(cash=D(10000))
    assert '太少' in g(cash=D(100))
    assert '差距' in g(prev={'eth': D(60000)})
    assert g(prev={'eth': D(78000)}) is None


def test_plan_initial_buys_hold_and_oversized_trade():
    orders, notes, reason = ex.plan_orders({'eth': D(0), 'usdt': D(0)}, D(6000), PRICES, MARKETS, CFG)
    assert reason is None and [(o.asset, o.side) for o in orders] == [('eth', 'buy'), ('usdt', 'buy')]
    assert orders[0].volume == D('0.0375') and orders[0].price == D('80400.0')
    held = {'eth': D('0.0375'), 'usdt': D('69.23')}                                  # 3000 + 2250 + 750
    assert ex.plan_orders(held, D(750), PRICES, MARKETS, CFG)[0] == []
    _, _, reason = ex.plan_orders({'eth': D(0), 'usdt': D('184.6')}, D(0), PRICES, MARKETS, CFG)   # 全部 USDT → 要賣 62.5%
    assert reason and '60%' in reason


def test_happy_path_then_already(tmp_path):
    fx = FakeExchange({'twd': 6000})
    store = ex.LiveStore(tmp_path / 'live.db')
    r = run(fx, store)
    assert r['status'] == 'done' and not r['errors']
    assert [s[:2] for s in fx.sent] == [('eth', 'buy'), ('usdt', 'buy')] and all(s[4] == 'ioc_limit' for s in fx.sent)
    assert r['weights_after']['eth'] == pytest.approx(D('0.5'), abs=D('0.01'))
    assert {o['status'] for o in store.orders()} == {'done'}
    assert run(fx, store)['status'] == 'already' and len(fx.sent) == 2


def test_sells_first_and_buys_resized_to_cash(tmp_path):
    fx = FakeExchange({'twd': 300, 'eth': D('0.07')})        # ETH 5600／權益 5900 → 賣 ETH、買 USDT
    r = run(fx, ex.LiveStore(tmp_path / 'live.db'))
    assert [s[:2] for s in fx.sent][0] == ('eth', 'sell') and ('usdt', 'buy') in [s[:2] for s in fx.sent]
    assert fx.bal['twd']['balance'] >= 0 and r['status'] == 'done'


def test_blocked_writes_run_and_sends_nothing(tmp_path):
    fx = FakeExchange({'twd': 6000})
    store = ex.LiveStore(tmp_path / 'live.db')
    r = run(fx, store, online=False)
    assert r['status'] == 'blocked' and fx.sent == [] and store.run(r['date'])['status'] == 'blocked'
    assert run(fx, store)['status'] == 'already'           # 擋下的那天不會在重試時又下單


def test_rejected_and_unknown_orders_are_recorded(tmp_path):
    fx = FakeExchange({'twd': 6000}, reject={'eth'}, lose={'usdt'})
    store = ex.LiveStore(tmp_path / 'live.db')
    r = run(fx, store)
    status = {o['asset']: o['status'] for o in store.orders()}
    assert status == {'eth': 'rejected', 'usdt': 'unknown'} and len(r['errors']) == 2
    assert len(fx.sent) == 2                               # unknown 不會盲目重送


def test_timeout_but_order_exists_is_settled(tmp_path):
    fx = FakeExchange({'twd': 6000}, timeout_create={'eth'})
    store = ex.LiveStore(tmp_path / 'live.db')
    run(fx, store)
    assert {o['asset']: o['status'] for o in store.orders()}['eth'] == 'done' and len(fx.sent) == 2


def test_stuck_order_gets_cancelled(tmp_path):
    fx = FakeExchange({'twd': 6000}, stuck={'eth'})
    store = ex.LiveStore(tmp_path / 'live.db')
    run(fx, store)
    assert len(fx.cancelled) == 1 and {o['asset']: o['status'] for o in store.orders()}['eth'] == 'cancel'


def test_retry_after_crash_only_reconciles(tmp_path):
    fx = FakeExchange({'twd': 6000})
    store = ex.LiveStore(tmp_path / 'live.db')
    day = datetime(2026, 10, 27).date()
    oid = 'cr2-20261027013600-deadbeef'
    store.save_run(day, NOW, 'running', '送單中')
    store.add_order(oid, day, ex.PlannedOrder('eth', 'ethtwd', 'buy', D('0.03'), D(80400), D(2400)), NOW)
    fx.orders[oid] = {'id': 9, 'client_oid': oid, 'state': 'done', 'executed_volume': '0.03', 'avg_price': '80080'}
    r = run(fx, store)
    assert r['status'] == 'reconciled' and fx.sent == [] and store.orders()[0]['status'] == 'done'


async def test_trading_client_signs_post_body():
    class S:
        def __init__(self):
            self.req = None

        def request(self, method, url, data=None, headers=None, timeout=None):
            self.req = (method, url, json.loads(data), headers)
            outer = self

            class R:
                status = 201

                async def text(self):
                    return json.dumps({'id': 1, 'state': 'wait', 'client_oid': outer.req[2]['client_oid']})

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False
            return R()
    s = S()
    c = MaxTradingClient(s, 'K' * 40, 'S' * 40)
    await c.create_order('ethtwd', 'buy', D('0.0375'), ord_type='ioc_limit', client_oid='cr2-x', price=D('80400.0'))
    method, url, body, headers = s.req
    assert method == 'POST' and url.endswith('/api/v3/wallet/spot/order')
    assert body['volume'] == '0.0375' and body['price'] == '80400.0' and 'nonce' in body
    assert json.loads(base64.b64decode(headers['X-MAX-PAYLOAD'])) == {**body, 'path': '/api/v3/wallet/spot/order'}
    with pytest.raises(ValueError):
        await c.create_order('ethtwd', 'buy', D(1), ord_type='stop_market', client_oid='x')
    assert not any('withdraw' in n for n in dir(MaxTradingClient))
