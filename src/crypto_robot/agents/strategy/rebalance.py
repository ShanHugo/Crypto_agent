"""ETH／台幣比例再平衡（研究與即時共用）。

ETH 市值占總權益的比例偏離目標達到門檻時，買賣 ETH 調回目標比例：漲多了賣、跌深了買。
每天收盤判斷，隔天開盤以當時價格重新計算買賣量再成交。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal(0)


@dataclass(frozen=True)
class RebalanceParams:
    target: Decimal        # ETH 目標比例，例如 0.5
    band: Decimal          # 偏離門檻（比例的絕對差），例如 0.1 → 50% 目標在 40% 以下或 60% 以上觸發

    def __post_init__(self):
        if not ZERO < self.target < 1:
            raise ValueError('target 必須在 0 和 1 之間')
        if not ZERO < self.band < min(self.target, 1 - self.target):
            raise ValueError('band 太大：觸發點會超出 0～100%')


def eth_weight(qty: Decimal, cash: Decimal, price: Decimal) -> Decimal:
    equity = cash + qty * price
    return ZERO if equity <= 0 else qty * price / equity


def needs_rebalance(qty: Decimal, cash: Decimal, price: Decimal, p: RebalanceParams) -> bool:
    return abs(eth_weight(qty, cash, price) - p.target) >= p.band


def target_delta(qty: Decimal, cash: Decimal, price: Decimal, p: RebalanceParams) -> Decimal:
    """調回目標比例要買（正）或賣（負）的 ETH 數量。不計手續費（金額小，下一次再平衡會修正）。"""
    equity = cash + qty * price
    return (p.target * equity - qty * price) / price
