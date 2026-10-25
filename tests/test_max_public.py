import json
from decimal import Decimal

import aiohttp
import pytest

from crypto_robot.core.num import amount_to_precision, loads, to_decimal
from crypto_robot.exchange.max.public import MaxApiError, MaxPublicClient
from fakes import DEPTH, MARKETS, TICKERS, FakeSession, k_rows

H = 3600


def client(routes):
    s = FakeSession(routes)
    return MaxPublicClient(s, backoff_sec=0), s


def test_to_decimal_rejects_float():
    assert to_decimal('76827.3') == Decimal('76827.3')
    assert to_decimal(3) == Decimal(3)
    with pytest.raises(TypeError):
        to_decimal(0.1)


def test_loads_never_produces_float():
    data = loads('[1792936800, 76604.0, 0.1]')
    assert data == [1792936800, Decimal('76604.0'), Decimal('0.1')]
    assert not any(isinstance(x, float) for x in data)


def test_precision_rounding():
    assert amount_to_precision(Decimal('0.1234569'), 6) == Decimal('0.123456')   # 數量一律捨去


async def test_markets_parsed_with_decimal_limits():
    c, _ = client({'/api/v3/markets': MARKETS})
    m = (await c.markets())['ethtwd']
    assert (m.base, m.quote, m.amount_places, m.price_places) == ('eth', 'twd', 6, 1)
    assert m.min_amount == Decimal('0.003') and m.min_cost == Decimal('250.0')
    assert m.active


async def test_ohlcv_normalized_to_ms_and_decimal():
    c, s = client({'/api/v3/k': '[[1792936800,76604.0,76857.4,76469.5,76857.4,8.189]]'})
    [bar] = await c.ohlcv('ethtwd', 60, since_ms=1792936800_000)
    assert bar.ts == 1792936800_000
    assert bar.high == Decimal('76857.4') and bar.volume == Decimal('8.189')
    assert s.calls[0][1]['timestamp'] == 1792936800   # MAX 用秒


async def test_ohlcv_range_pages_until_end():
    start = 1_700_000_000 // H * H

    def k(params):
        since = params['timestamp']
        n = min(3, (start + 7 * H - since) // H)   # 每頁最多 3 根，交易所總共 7 根
        return json.dumps(k_rows(since, max(n, 0)))

    c, s = client({'/api/v3/k': k})
    bars = await c.ohlcv_range('ethtwd', 60, start * 1000, (start + 10 * H) * 1000)
    assert [b.ts // 1000 for b in bars] == [start + i * H for i in range(7)]
    assert len(s.calls) == 4    # 3 + 3 + 1，第 4 次拿到空頁停止


async def test_ohlcv_range_respects_end():
    start = 1_700_000_000 // H * H
    c, _ = client({'/api/v3/k': lambda p: json.dumps(k_rows(p['timestamp'], 5))})
    bars = await c.ohlcv_range('ethtwd', 60, start * 1000, (start + 2 * H) * 1000)
    assert len(bars) == 2


async def test_order_book_sorted_best_first():
    c, _ = client({'/api/v3/depth': DEPTH})
    ob = await c.order_book('ethtwd')
    assert ob.asks[0] == (Decimal('76920.5'), Decimal('0.495672'))
    assert ob.bids[0][0] == Decimal('76890.2')
    assert [p for p, _ in ob.asks] == sorted(p for p, _ in ob.asks)


async def test_ticker():
    c, _ = client({'/api/v3/tickers': TICKERS})
    t = await c.ticker('ethtwd')
    assert (t.bid, t.ask, t.last) == (Decimal('76890.2'), Decimal('76920.5'), Decimal('76827.3'))


async def test_retries_server_errors_then_succeeds():
    c, s = client({'/api/v3/timestamp': [(502, 'bad gateway'), aiohttp.ClientConnectionError(), '{"timestamp":1792946586}']})
    assert await c.server_time_ms() == 1792946586_000
    assert len(s.calls) == 3


async def test_client_errors_not_retried():
    c, s = client({'/api/v3/k': [(400, '{"error":"bad market"}'), '[]']})
    with pytest.raises(MaxApiError) as e:
        await c.ohlcv('nope')
    assert e.value.status == 400 and len(s.calls) == 1


async def test_gives_up_after_retries():
    c, s = client({'/api/v3/timestamp': [(503, 'x')] * 4})
    with pytest.raises(MaxApiError):
        await c.server_time_ms()
    assert len(s.calls) == 4
