"""第八輪研究：恐懼與貪婪指數（＋RSI）在 BTC／ETH 上的「恐慌時買進」（DESIGN.md 6.3）。

來源：使用者 2026-10-30 提供的 Bybit 文章（多方法交叉確認：RSI < 30＋恐懼與貪婪指數 < 25）。
指數來自 alternative.me（2018-02 起每天一筆，UTC 00:00 公布）；訂定前只確認過資料完整度，沒有對照過價格。

2026-10-30 在跑任何結果前訂定並 commit（使用者同意）。看過研究期結果後**不得**為了讓某組通過而修改這裡。

**已知限制**：信號很少（極度恐懼一年只有幾段）；BTC 與 ETH 常同一天出信號，兩邊不是獨立的；
這個信號接近現在的再平衡（跌多就買），這輪不和再平衡比較。ETH 研究期價格已看過六次，BTC 沒研究過。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import product

from crypto_robot.lab.breakout_variants import DAY_MS, DEV_END, DEV_MID, DEV_START, HOLDOUT_START, PERIOD_MIN  # noqa: F401  同一組期間

ASSETS = ('btc', 'eth')
TAKER_FEE = Decimal('0.0016')
SLIPPAGE = Decimal('0.001')
STRESS_SLIPPAGE = Decimal('0.002')
FEAR_MAX = 25            # 恐懼與貪婪指數 < 25
RSI_PERIOD, RSI_MAX = 14, 30


@dataclass(frozen=True)
class FearVariant:
    name: str
    use_rsi: bool        # True：同時要 RSI(14) < 30（交叉確認）
    hold_days: int


def all_variants() -> list[FearVariant]:
    """交叉確認／只看恐懼 × 持有 30／90 天 = 4 組。"""
    return [FearVariant(f'fear25{"-rsi30" if rsi else ""}-h{h}', rsi, h) for rsi, h in product((True, False), (30, 90))]


def by_name(name: str) -> FearVariant:
    for v in all_variants():
        if v.name == name:
            return v
    raise KeyError(f'沒有這組規則：{name}')


# ---- 研究期通過標準：BTC、ETH 各自五項全過，這組才算通過 ----
MIN_EVENTS, MIN_T = 8, 3.0
RANDOM_RUNS, RANDOM_PCT = 1000, 0.95


def passes_dev(n: int, random_pct: float, t: float, halves: list[float], stress_excess: float) -> tuple[bool, list[str]]:
    """excess＝信號報酬 − 同期間任意一天進場持有 H 天的平均報酬（都扣成本）。

    1. 信號 ≥ MIN_EVENTS 次
    2. 平均報酬高於隨機進場（同幣、同次數）RANDOM_RUNS 次中的 RANDOM_PCT
    3. 超額報酬 t ≥ MIN_T
    4. 前後半期平均超額報酬都 > 0
    5. 滑價加倍後平均超額報酬 > 0
    """
    fails = []
    if n < MIN_EVENTS:
        fails.append(f'信號 {n} 次 < {MIN_EVENTS}')
    if random_pct < RANDOM_PCT:
        fails.append(f'只贏過 {random_pct:.0%} 的隨機進場')
    if t < MIN_T:
        fails.append(f't = {t:.2f} < {MIN_T}')
    for label, h in zip(('前半', '後半'), halves):
        if h <= 0:
            fails.append(f'{label}沒有比任意進場多賺')
    if stress_excess <= 0:
        fails.append('滑價加倍後沒有比任意進場多賺')
    return not fails, fails


# ---- 考卷期：只考研究期通過組中 t 最高的一組（取 BTC、ETH 較小的 t），只考一次；兩個幣都要過 ----
HOLDOUT_MIN_EVENTS, HOLDOUT_RANDOM_PCT = 4, 0.90


def passes_holdout(n: int, mean_excess: float, random_pct: float) -> tuple[bool, list[str]]:
    fails = []
    if n < HOLDOUT_MIN_EVENTS:
        fails.append(f'信號 {n} 次 < {HOLDOUT_MIN_EVENTS}')
    if mean_excess <= 0:
        fails.append('沒有比任意進場多賺')
    if random_pct < HOLDOUT_RANDOM_PCT:
        fails.append(f'只贏過 {random_pct:.0%} 的隨機進場')
    return not fails, fails
