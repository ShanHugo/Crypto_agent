"""回測共用的資料結構與「一直持有」基準。"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from crypto_robot.core.models import Bar

ZERO = Decimal(0)
ONE = Decimal(1)


@dataclass(frozen=True)
class CostParams:
    taker_fee: Decimal = Decimal('0.0016')
    slippage: Decimal = Decimal('0.001')


@dataclass(frozen=True)
class Trade:
    entry_ts: int
    exit_ts: int
    entry_price: Decimal     # 實際成交價（含滑價，不含手續費）
    exit_price: Decimal
    qty: Decimal
    net_return: Decimal      # 扣手續費與滑價後的報酬率
    reason: str


@dataclass
class Result:
    initial: Decimal
    equity: list[tuple[int, Decimal]] = field(default_factory=list)   # 每根 K 線收盤時的權益
    trades: list[Trade] = field(default_factory=list)
    costs: Decimal = ZERO          # 手續費＋滑價（台幣）
    bars_in_position: int = 0


def buy_and_hold(bars: list[Bar], start_ms: int, end_ms: int, cp: CostParams,
                 *, initial: Decimal = Decimal(1_000_000)) -> Result:
    """基準：期初第一根開盤全部買入、一直持有（只扣一次買入成本）。"""
    res = Result(initial)
    qty = None
    for b in bars:
        if b.ts < start_ms:
            continue
        if b.ts >= end_ms:
            break
        if qty is None:
            fill = b.open * (ONE + cp.slippage)
            qty = initial / (fill * (ONE + cp.taker_fee))
            res.costs = initial - qty * b.open
        res.bars_in_position += 1
        res.equity.append((b.ts, qty * b.close))
    return res
