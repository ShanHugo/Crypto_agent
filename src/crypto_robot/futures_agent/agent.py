"""合約 Agent 每一輪（每根 K 線收盤）的決策：快照 → 安全檢查 → 策略 → 動作清單。純函式，不連網、不下單。

安全規則（順序就是優先順序）：
1. 已永久停止（stopped）→ 什麼都不做。
2. 權益 ≤ 起始資金 − 最多可賠金額 → 全部平倉並永久停止（不補錢、不轉帳）。
3. 有持倉卻沒有交易所端停損單 → 先補停損（當機、斷網時靠它保命），即使在暫停中。
4. 暫停檔、資料過舊 → 不開新倉、不換方向（已有持倉留著，靠停損單保護）。
5. 策略要的方向和現在不同 → 先平倉，再依波動調整後的槓桿開新倉並同時掛停損單；沒有波動預測就不開新倉。

部位大小（第十七輪，DESIGN 6.3：EWMA 波動預測研究期與考卷都通過）：槓桿＝min(槓桿上限, 目標波動 ÷ 預測波動)，預期越動盪部位越小。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Protocol

ZERO = Decimal(0)


@dataclass(frozen=True)
class AgentConfig:
    symbol: str = 'ETHUSDT'
    capital: Decimal = ZERO          # 營運者手動轉入合約帳戶的金額（USDT），第 5 關前填
    loss_cap: Decimal = ZERO         # 最多可賠（USDT，約 10,000 台幣）；權益跌到 capital − loss_cap 就永久停止
    max_leverage: Decimal = Decimal(1)
    target_vol: Decimal = Decimal('0.5')     # 年化目標波動；第 3、4 關定案前的暫定值
    stop_pct: Decimal = Decimal('0.05')      # 交易所端停損距離進場價
    qty_step: Decimal = Decimal('0.001')     # ETHUSDT 數量最小單位（第 4 關前用 exchangeInfo 查核）
    max_data_age_ms: int = 2 * 3600 * 1000


@dataclass(frozen=True)
class Position:
    qty: Decimal = ZERO              # 正＝多、負＝空
    entry: Decimal = ZERO
    has_stop: bool = False


@dataclass(frozen=True)
class Snapshot:
    now_ms: int
    bar_ms: int                      # 最新已收盤 K 線的時間
    price: Decimal                   # 標記價格
    equity: Decimal                  # 合約帳戶權益（USDT，含未實現損益）
    position: Position
    vol: Decimal = ZERO              # 預測年化波動（ewma_vol）；0＝沒有預測
    paused: bool = False
    stopped: bool = False


@dataclass(frozen=True)
class Action:
    kind: str                        # halt／hold／close／open／place_stop／stop_forever
    side: int = 0                    # 1 多、-1 空
    qty: Decimal = ZERO
    stop: Decimal = ZERO
    note: str = ''


class Strategy(Protocol):
    def decide(self, snap: Snapshot) -> int: ...    # 想要的方向：1 多、-1 空、0 不持倉


class NoStrategy:
    """第 3 關還沒有通過的合約規則：永遠不持倉。"""

    def decide(self, snap: Snapshot) -> int:
        return 0


def direction(p: Position) -> int:
    return (p.qty > 0) - (p.qty < 0)


def stop_price(entry: Decimal, side: int, cfg: AgentConfig) -> Decimal:
    return entry * (1 - side * cfg.stop_pct)


def ewma_vol(daily_returns: list[Decimal], lam: Decimal = Decimal('0.94'), warmup: int = 30) -> Decimal:
    """台北日報酬（舊到新）的 EWMA 預測年化波動；前 warmup 天的平均平方報酬當起點，不夠就回傳 0。"""
    if len(daily_returns) <= warmup:
        return ZERO
    var = sum(r * r for r in daily_returns[:warmup]) / warmup
    for r in daily_returns[warmup:]:
        var = lam * var + (1 - lam) * r * r
    return (var * 365).sqrt()


def leverage(vol: Decimal, cfg: AgentConfig) -> Decimal:
    return ZERO if vol <= 0 else min(cfg.max_leverage, cfg.target_vol / vol)


def size(equity: Decimal, price: Decimal, cfg: AgentConfig, vol: Decimal) -> Decimal:
    """名目金額 ≤ 權益 × 波動調整後的槓桿，數量無條件捨去到最小單位。"""
    if equity <= 0 or price <= 0:
        return ZERO
    return (equity * leverage(vol, cfg) / price / cfg.qty_step).to_integral_value(ROUND_DOWN) * cfg.qty_step


def tick(snap: Snapshot, cfg: AgentConfig, strategy: Strategy) -> list[Action]:
    pos, cur = snap.position, direction(snap.position)
    if snap.stopped:
        return [Action('halt', note='已永久停止')]
    if cfg.capital > 0 and snap.equity <= cfg.capital - cfg.loss_cap:
        return ([Action('close', note='觸及最多可賠金額')] if cur else []) + [Action('stop_forever', note='觸及最多可賠金額')]
    pre = [Action('place_stop', cur, abs(pos.qty), stop_price(pos.entry, cur, cfg), '持倉沒有停損單')] if cur and not pos.has_stop else []
    if snap.paused:
        return pre + [Action('hold', note='暫停中')]
    if snap.now_ms - snap.bar_ms > cfg.max_data_age_ms:
        return pre + [Action('hold', note='資料過舊')]
    want = strategy.decide(snap)
    if want == cur:
        return pre + [Action('hold')]
    acts = [Action('close', cur, abs(pos.qty))] if cur else pre
    qty = size(snap.equity, snap.price, cfg, snap.vol) if want else ZERO
    if qty > 0:
        acts.append(Action('open', want, qty, stop_price(snap.price, want, cfg)))
    return acts or [Action('hold', note='沒有波動預測' if want and snap.vol <= 0 else '數量太小')]
