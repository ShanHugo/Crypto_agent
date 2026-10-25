"""1h K 線回測（DESIGN.md 3.4～3.6、6.2）。

成交假設（盡量和即時系統一致）：
- 進場：訊號 K 線收盤後，下一根開盤價市價買入。
- 停損價 = max(平均進場成本（含手續費）× (1 − stop_loss_pct), 訊號 K 線 low)。
  每根 K 線（含進場那根）：開盤 ≤ 停損價 → 以開盤價賣出（跳空）；否則最低價 ≤ 停損價 → 以停損價賣出。
- 死叉出場（選用）：下穿 K 線收盤後，下一根開盤價賣出。
- 成本：每次成交扣 taker 手續費，價格再往不利方向移動 slippage。
- 期末仍持有：以最後收盤價計算權益，不扣賣出成本，在報告中標示。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from crypto_robot.agents.strategy.ema_cross import StrategyParams, crosses, entry_signals
from crypto_robot.core.models import Bar

ZERO = Decimal(0)
ONE = Decimal(1)


@dataclass(frozen=True)
class ExitParams:
    stop_loss_pct: Decimal = Decimal('0.01')
    use_entry_bar_low: bool = True
    exit_on_cross_down: bool = False


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
    reason: str              # stop / gap / cross / open（期末未平倉）


@dataclass
class Result:
    initial: Decimal
    equity: list[tuple[int, Decimal]] = field(default_factory=list)   # 每根 K 線收盤時的權益
    trades: list[Trade] = field(default_factory=list)
    costs: Decimal = ZERO          # 手續費＋滑價（台幣）
    bars_in_position: int = 0


def run(bars: list[Bar], start_ms: int, end_ms: int, sp: StrategyParams, xp: ExitParams, cp: CostParams,
        *, initial: Decimal = Decimal(1_000_000), position_pct: Decimal = ONE) -> Result:
    """bars 要包含 start_ms 之前的暖機資料；只在 [start_ms, end_ms) 之間交易與記錄權益。"""
    signals = entry_signals(bars, sp)
    _, cross_down = crosses(bars, sp) if xp.exit_on_cross_down else (None, [False] * len(bars))
    res = Result(initial)
    cash, qty = initial, ZERO
    entry_signal_i: int | None = None     # 等下一根開盤買入
    exit_pending = False                   # 等下一根開盤賣出
    pos: dict | None = None

    def sell(b: Bar, price: Decimal, reason: str):
        nonlocal cash, qty, pos
        fill = price * (ONE - cp.slippage)
        gross = qty * fill
        fee = gross * cp.taker_fee
        cash += gross - fee
        res.costs += qty * (price - fill) + fee
        res.trades.append(Trade(pos['ts'], b.ts, pos['price'], fill, qty,
                                (gross - fee) / pos['spent'] - ONE, reason))
        qty, pos = ZERO, None

    for i, b in enumerate(bars):
        if b.ts < start_ms:
            continue
        if b.ts >= end_ms:
            break
        if pos is not None and exit_pending:
            sell(b, b.open, 'cross')
        exit_pending = False
        if pos is None and entry_signal_i is not None:
            fill = b.open * (ONE + cp.slippage)
            budget = cash * position_pct
            qty = budget / (fill * (ONE + cp.taker_fee))
            gross = qty * fill
            fee = gross * cp.taker_fee
            cash -= gross + fee
            res.costs += qty * (fill - b.open) + fee
            avg_cost = (gross + fee) / qty
            stop = avg_cost * (ONE - xp.stop_loss_pct)
            if xp.use_entry_bar_low:
                stop = max(stop, bars[entry_signal_i].low)
            pos = {'ts': b.ts, 'price': fill, 'spent': gross + fee, 'stop': stop}
        entry_signal_i = None
        if pos is not None:
            if b.open <= pos['stop']:
                sell(b, b.open, 'gap')
            elif b.low <= pos['stop']:
                sell(b, pos['stop'], 'stop')
        if pos is not None:
            res.bars_in_position += 1
            exit_pending = cross_down[i]
        elif signals[i]:
            entry_signal_i = i
        res.equity.append((b.ts, cash + qty * b.close))

    if pos is not None:
        last_ts, last_equity = res.equity[-1]
        value = last_equity - cash            # 持有量 × 最後收盤價，不扣賣出成本
        res.trades.append(Trade(pos['ts'], last_ts, pos['price'], value / qty, qty,
                                value / pos['spent'] - ONE, 'open'))
    return res


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
