"""用當下盤口模擬市價單成交（DESIGN.md 5.7）：逐檔吃單，深度不夠的部分不成交。"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from crypto_robot.core.models import OrderBook

ZERO = Decimal(0)


@dataclass(frozen=True)
class Fill:
    qty: Decimal          # 成交的 ETH
    gross: Decimal        # 成交金額（台幣，未含手續費）
    fee: Decimal          # 手續費（台幣）
    levels: int           # 吃了幾檔

    @property
    def avg_price(self) -> Decimal:
        return self.gross / self.qty if self.qty else ZERO


def market_buy(book: OrderBook, qty: Decimal, fee_rate: Decimal) -> Fill:
    return _walk(book.asks, qty, fee_rate)


def market_sell(book: OrderBook, qty: Decimal, fee_rate: Decimal) -> Fill:
    return _walk(book.bids, qty, fee_rate)


def _walk(levels, qty: Decimal, fee_rate: Decimal) -> Fill:
    left, gross, used = qty, ZERO, 0
    for price, size in levels:
        if left <= 0:
            break
        take = min(left, size)
        gross += take * price
        left -= take
        used += 1
    filled = qty - left
    return Fill(filled, gross, gross * fee_rate, used)
