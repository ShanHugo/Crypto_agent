"""多資產比例再平衡（研究與模擬共用）：例如 ETH 50%、USDT 37.5%、台幣 12.5%。

任何一項（含台幣）占總權益的比例偏離目標達門檻時，全部調回目標比例：漲多的賣、跌深的買。
台幣是計價貨幣，目標比例 = 1 − 其他資產目標比例的總和。每天收盤判斷，隔天開盤以當時價格重算買賣量再成交。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal(0)
ONE = Decimal(1)


@dataclass(frozen=True)
class MultiParams:
    targets: tuple[tuple[str, Decimal], ...]   # (資產, 目標比例)，例如 (('eth', 0.5), ('usdt', 0.375))
    band: Decimal                              # 偏離門檻（比例的絕對差）

    @property
    def cash_target(self) -> Decimal:
        return ONE - sum(t for _, t in self.targets)

    def __post_init__(self):
        if not self.targets or any(t <= 0 for _, t in self.targets):
            raise ValueError('每項資產的目標比例都要大於 0')
        if self.cash_target <= 0:
            raise ValueError('資產目標比例總和要小於 1（剩下的是台幣）')
        if not ZERO < self.band < min([t for _, t in self.targets] + [self.cash_target]):
            raise ValueError('band 太大：觸發點會低於 0%')


def equity(qty: dict[str, Decimal], cash: Decimal, prices: dict[str, Decimal]) -> Decimal:
    return cash + sum(qty[a] * prices[a] for a in qty)


def weights(qty: dict[str, Decimal], cash: Decimal, prices: dict[str, Decimal]) -> dict[str, Decimal]:
    """各資產與台幣（key 'twd'）占總權益的比例。"""
    eq = equity(qty, cash, prices)
    if eq <= 0:
        return {**{a: ZERO for a in qty}, 'twd': ZERO}
    return {**{a: qty[a] * prices[a] / eq for a in qty}, 'twd': cash / eq}


def needs_rebalance(qty: dict[str, Decimal], cash: Decimal, prices: dict[str, Decimal], p: MultiParams) -> bool:
    w = weights(qty, cash, prices)
    targets = dict(p.targets) | {'twd': p.cash_target}
    return any(abs(w[a] - t) >= p.band for a, t in targets.items())


def target_deltas(qty: dict[str, Decimal], cash: Decimal, prices: dict[str, Decimal], p: MultiParams) -> dict[str, Decimal]:
    """調回目標比例，各資產要買（正）或賣（負）的數量。不計手續費（下一次再平衡會修正）。"""
    eq = equity(qty, cash, prices)
    return {a: (t * eq - qty[a] * prices[a]) / prices[a] for a, t in p.targets}
