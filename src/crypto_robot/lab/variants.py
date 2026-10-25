"""研究期要比較的規則組合與通過標準（DESIGN.md 6.3）。

2026-10-26 在跑任何策略結果前訂定並 commit。看過研究期結果後**不得**為了讓某組通過而修改這裡；
真的要改，就在報告裡寫明「看過結果後修改」，並把該次結果視為新的研究、重新計算檢驗次數。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import product

from crypto_robot.agents.strategy.ema_cross import StrategyParams
from crypto_robot.lab.backtest import CostParams, ExitParams

BASELINE = 'ema20_50-vol-sl1-low'     # 原始規格的規則
COSTS = CostParams(taker_fee=Decimal('0.0016'), slippage=Decimal('0.001'))


@dataclass(frozen=True)
class Variant:
    name: str
    strategy: StrategyParams
    exit: ExitParams


def all_variants() -> list[Variant]:
    """2 組均線 × 4 種停損 × 停損用不用訊號 K 線低點 × 量能過濾開關 × 死叉出場開關 = 64 組。"""
    out = []
    for (fast, slow), sl, low, vol, cross in product(
            [(20, 50), (50, 200)], ['0.01', '0.02', '0.03', '0.05'], [True, False], [True, False], [False, True]):
        name = f'ema{fast}_{slow}-{"vol" if vol else "novol"}-sl{int(Decimal(sl) * 100)}' \
               f'{"-low" if low else ""}{"-xdown" if cross else ""}'
        out.append(Variant(
            name,
            StrategyParams(ema_fast=fast, ema_slow=slow, use_volume_filter=vol, warmup_bars=3 * slow),
            ExitParams(stop_loss_pct=Decimal(sl), use_entry_bar_low=low, exit_on_cross_down=cross),
        ))
    return out


def by_name(name: str) -> Variant:
    for v in all_variants():
        if v.name == name:
            return v
    raise KeyError(f'沒有這組規則：{name}')


# ---- 研究期通過標準：四項全部成立 ----
MIN_TRADES = 30          # 研究期完成交易筆數
MIN_T_STAT = 3.0         # 每筆淨報酬平均值的 t 值（同時比較 64 組，用 Stock_Robot 的 |t| ≥ 3）


def passes(trades: int, t: float, halves: list[dict]) -> tuple[bool, list[str]]:
    """halves：前後半期各一個 dict，含 ret、mdd、hodl_ret、hodl_mdd（mdd 為負數）。

    1. 完成交易 ≥ MIN_TRADES
    2. 每筆淨報酬 t ≥ MIN_T_STAT
    3. 前後半期報酬都 > 0
    4. 前後半期都不輸一直持有 ETH：報酬 ≥ 持有報酬，或「報酬 ÷ |最大回撤|」≥ 持有的同一比值
    """
    fails = []
    if trades < MIN_TRADES:
        fails.append(f'交易 {trades} 筆 < {MIN_TRADES}')
    if t < MIN_T_STAT:
        fails.append(f't = {t:.2f} < {MIN_T_STAT}')
    for label, h in zip(('前半', '後半'), halves):
        if h['ret'] <= 0:
            fails.append(f'{label}報酬 ≤ 0')
            continue
        ratio = h['ret'] / abs(h['mdd']) if h['mdd'] else float('inf')
        hodl_ratio = h['hodl_ret'] / abs(h['hodl_mdd']) if h['hodl_mdd'] else float('inf')
        if h['ret'] < h['hodl_ret'] and ratio < hodl_ratio:
            fails.append(f'{label}輸給持有 ETH')
    return not fails, fails
