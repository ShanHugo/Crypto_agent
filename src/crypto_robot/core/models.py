"""交易所無關的資料格式。時間一律是 UTC 毫秒，數字一律是 Decimal。"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Bar:
    ts: int            # K 線開始時間（UTC 毫秒）
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal    # 基礎幣數量（ETH）


@dataclass(frozen=True)
class Market:
    id: str            # 交易所代號，例如 ethtwd
    base: str
    quote: str
    amount_places: int
    price_places: int
    min_amount: Decimal
    min_cost: Decimal
    active: bool


@dataclass(frozen=True)
class OrderBook:
    ts: int
    bids: tuple[tuple[Decimal, Decimal], ...]   # 價格由高到低
    asks: tuple[tuple[Decimal, Decimal], ...]   # 價格由低到高


@dataclass(frozen=True)
class Ticker:
    market: str
    ts: int
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    volume: Decimal | None   # 近 24 小時基礎幣成交量
