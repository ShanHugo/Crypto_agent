"""MAX 公開 REST v3（不需金鑰）。回傳值都已正規化成 core.models。

端點與回傳格式在 2026-10-26 實測（DESIGN.md 第 1 節）。JSON 小數直接解析成 Decimal，不經過 float。
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

import aiohttp

from crypto_robot.core.models import Bar, Market, OrderBook, Ticker
from crypto_robot.core.num import loads, to_decimal

BASE_URL = 'https://max-api.maicoin.com'
MAX_BARS_PER_REQUEST = 10_000
log = logging.getLogger(__name__)


class MaxApiError(RuntimeError):
    def __init__(self, status: int, body: str, path: str):
        super().__init__(f'MAX {path} 回應 {status}：{body[:200]}')
        self.status = status
        self.body = body
        self.path = path


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, MaxApiError):
        return exc.status == 429 or exc.status >= 500
    return isinstance(exc, (aiohttp.ClientError, asyncio.TimeoutError))


class MaxPublicClient:
    def __init__(self, session: aiohttp.ClientSession, *, base_url: str = BASE_URL,
                 retries: int = 3, backoff_sec: float = 1.0):
        self._session = session
        self._base = base_url.rstrip('/')
        self._retries = retries
        self._backoff = backoff_sec

    async def _get(self, path: str, params: dict | list | None = None):
        for attempt in range(self._retries + 1):
            try:
                async with self._session.get(self._base + path, params=params,
                                             timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        raise MaxApiError(resp.status, text, path)
                    return loads(text)
            except Exception as exc:
                if attempt == self._retries or not _retryable(exc):
                    raise
                wait = self._backoff * 2 ** attempt
                log.warning('MAX %s 失敗（%s），%.0f 秒後重試', path, exc, wait)
                await asyncio.sleep(wait)

    async def server_time_ms(self) -> int:
        data = await self._get('/api/v3/timestamp')
        return int(data['timestamp']) * 1000

    async def markets(self) -> dict[str, Market]:
        data = await self._get('/api/v3/markets')
        return {m['id']: parse_market(m) for m in data}

    async def ohlcv(self, market: str, period_min: int = 60, *, since_ms: int | None = None,
                    limit: int = MAX_BARS_PER_REQUEST) -> list[Bar]:
        """K 線，由舊到新。since_ms 給定時從該時間（含）開始；未給則回傳最近 limit 根（最後一根可能未收盤）。"""
        params = {'market': market, 'period': period_min, 'limit': min(limit, MAX_BARS_PER_REQUEST)}
        if since_ms is not None:
            params['timestamp'] = since_ms // 1000
        data = await self._get('/api/v3/k', params)
        return [parse_bar(row) for row in data]

    async def ohlcv_range(self, market: str, period_min: int, start_ms: int, end_ms: int) -> list[Bar]:
        """抓 [start_ms, end_ms) 之間的 K 線，自動分頁。"""
        step = period_min * 60_000
        bars: list[Bar] = []
        cursor = start_ms
        while cursor < end_ms:
            page = await self.ohlcv(market, period_min, since_ms=cursor)
            page = [b for b in page if cursor <= b.ts < end_ms]
            if not page:
                break
            bars.extend(page)
            cursor = page[-1].ts + step
        return bars

    async def order_book(self, market: str, limit: int = 50) -> OrderBook:
        data = await self._get('/api/v3/depth', {'market': market, 'limit': limit})
        return parse_order_book(data)

    async def ticker(self, market: str) -> Ticker:
        data = await self._get('/api/v3/tickers', [('markets[]', market)])
        if not data:
            raise MaxApiError(200, '[]', '/api/v3/tickers')
        return parse_ticker(data[0])


def parse_market(m: dict) -> Market:
    return Market(
        id=m['id'], base=m['base_unit'], quote=m['quote_unit'],
        amount_places=int(m['base_unit_precision']), price_places=int(m['quote_unit_precision']),
        min_amount=to_decimal(m['min_base_amount']), min_cost=to_decimal(m['min_quote_amount']),
        active=m.get('status') == 'active',
    )


def parse_bar(row: list) -> Bar:
    ts, o, h, l, c, v = row
    return Bar(int(ts) * 1000, to_decimal(o), to_decimal(h), to_decimal(l), to_decimal(c), to_decimal(v))


def parse_order_book(d: dict) -> OrderBook:
    def side(rows, reverse):
        levels = [(to_decimal(p), to_decimal(q)) for p, q in rows]
        return tuple(sorted(levels, key=lambda x: x[0], reverse=reverse))
    # MAX 的 asks 是由高到低回傳，統一成由低到高
    return OrderBook(int(d['timestamp']) * 1000, side(d['bids'], True), side(d['asks'], False))


def parse_ticker(d: dict) -> Ticker:
    def opt(key) -> Decimal | None:
        v = d.get(key)
        return None if v in (None, '') else to_decimal(v)
    return Ticker(d['market'], int(d['at']) * 1000, opt('buy'), opt('sell'), opt('last'), opt('vol'))
