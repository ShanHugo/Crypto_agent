import base64
import hashlib
import hmac
import json
from decimal import Decimal

import pytest

from crypto_robot.exchange.max.private import MaxPrivateClient, sign
from crypto_robot.exchange.max.public import MaxApiError
from fakes import FakeSession

KEY, SECRET = 'A' * 40, 'S' * 40


def test_sign_matches_official_recipe():
    params = {'nonce': 1700000000000, 'market': 'ethtwd'}
    payload, sig = sign(SECRET, '/api/v3/info', params)
    assert json.loads(base64.b64decode(payload)) == {**params, 'path': '/api/v3/info'}
    assert sig == hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def test_requires_keys_and_hides_them():
    with pytest.raises(ValueError):
        MaxPrivateClient(FakeSession({}), '', SECRET)
    c = MaxPrivateClient(FakeSession({}), KEY, SECRET)
    assert KEY not in repr(c) and SECRET not in repr(c)


def test_nonce_uses_server_offset_and_increases():
    c = MaxPrivateClient(FakeSession({}), KEY, SECRET)
    c.sync_clock(server_ms=10_000_000, local_ms=9_000_000)          # 本機慢 1000 秒
    n1, n2 = c._nonce(), c._nonce()
    assert n2 > n1
    import time
    assert abs(n1 - (int(time.time() * 1000) + 1_000_000)) < 5_000


async def test_info_and_balances_parse_and_send_signed_headers():
    s = FakeSession({
        '/api/v3/info': json.dumps({'email': 'x@example.com', 'level': 2, 'current_vip_level': {'level': 0, 'maker_fee': 0.0008, 'taker_fee': 0.0016}}),
        '/api/v3/wallet/spot/accounts': json.dumps([{'currency': 'twd', 'balance': '6000.5', 'locked': '0'},
                                                     {'currency': 'eth', 'balance': '0', 'locked': '0'},
                                                     {'currency': 'usdt', 'balance': '1.2', 'locked': '3'}]),
    })
    c = MaxPrivateClient(s, KEY, SECRET)
    info = await c.info()
    assert info == {'level': 2, 'vip_level': 0, 'maker_fee': Decimal('0.0008'), 'taker_fee': Decimal('0.0016')}   # 沒有 email
    bal = await c.balances()
    assert bal == {'twd': {'balance': Decimal('6000.5'), 'locked': Decimal(0)},
                   'usdt': {'balance': Decimal('1.2'), 'locked': Decimal(3)}}
    h = s.headers[0]
    assert h['X-MAX-ACCESSKEY'] == KEY and SECRET not in json.dumps(h)
    assert json.loads(base64.b64decode(h['X-MAX-PAYLOAD']))['path'] == '/api/v3/info'


async def test_auth_error_raises_without_retry():
    s = FakeSession({'/api/v3/info': [(401, '{"success":false,"error":{"code":2001}}')]})
    with pytest.raises(MaxApiError) as e:
        await MaxPrivateClient(s, KEY, SECRET).info()
    assert e.value.status == 401 and len(s.calls) == 1


def test_client_has_no_trading_methods():
    names = {n for n in dir(MaxPrivateClient) if not n.startswith('_')}
    assert not names & {'create_order', 'order', 'cancel', 'cancel_order', 'withdraw', 'post'}
