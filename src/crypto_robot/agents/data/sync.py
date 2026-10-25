"""把交易所的已收盤 K 線補進本地快取。"""
from __future__ import annotations

from crypto_robot.agents.data.quality import closed_only
from crypto_robot.exchange.max.public import MaxPublicClient
from crypto_robot.store.bars import BarStore


async def sync_bars(client: MaxPublicClient, store: BarStore, market: str, period_min: int,
                    start_ms: int, *, grace_ms: int = 10_000) -> int:
    """從快取最後一根的下一根（快取是空的就從 start_ms）抓到現在，只寫入已收盤的 K 線。回傳新寫入筆數。"""
    step = period_min * 60_000
    last = store.last_ts(market, period_min)
    since = start_ms if last is None else last + step
    server_ms = await client.server_time_ms()
    bars = await client.ohlcv_range(market, period_min, since, server_ms)
    return store.upsert(market, period_min, closed_only(bars, step, server_ms, grace_ms))
