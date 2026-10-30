"""第七輪研究：MAX 台幣市場的山寨幣「橫盤後帶量突破」（DESIGN.md 6.3）。

使用者的想法（2026-10-30）：找出接下來可能大漲的幣（漏斗篩選的第 4 步：技術面）。
第 1～3 步（基本面、鏈上、社群）拿不到當時的歷史資料，沒辦法回測，這輪不做。

2026-10-30 在跑任何結果前訂定並 commit（使用者同意）。看過研究期結果後**不得**為了讓某組通過而修改這裡。
訂定前只看過各幣的上市日與每年成交額（資料品質），沒有看過任何報酬。

**已知限制**：只有現在還在 MAX 上的幣（下架、歸零的不在資料裡，結果偏樂觀）；
歷史買賣價差查不到，成本用 2026-10-30 的小幣價差估計。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from itertools import product


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


DAY_MS = 86_400_000
PERIOD_MIN = 1440                       # MAX 日 K（UTC 日界）

# 山寨幣資料第一次使用：研究期與考卷期在看任何結果前切好，考卷期只考一次
DEV_START, DEV_MID, DEV_END = _ms(2021, 7, 1), _ms(2023, 1, 1), _ms(2024, 7, 1)
HOLDOUT_START = DEV_END

# 2026-10-30 MAX 上的非穩定幣台幣市場（ETH 是比較對象，不放進來）
COINS = ('aave ada alice ape apt arb avax bch bnb btc comp doge dot ena ens etc gala gmt gram grt gst hbar hype '
         'link ltc mana max near ondo pol render sand shib sol sui tao trx uni wld xlm xrp xtz yfi').split()
BENCH = 'eth'

TAKER_FEE = Decimal('0.0016')
SLIPPAGE = Decimal('0.005')             # 每邊；目前小幣買賣價差一半約 0.25%～1%
STRESS_SLIPPAGE = Decimal('0.01')

LIQ_DAYS = 30
MIN_LIQ = Decimal(500_000)              # 前 30 天每日成交額中位數（台幣）
MAX_RANGE = Decimal('1.4')              # 橫盤：前 N 天最高 ÷ 最低
VOL_MULT = Decimal(3)                   # 帶量：今天成交額 ≥ 前 30 天中位數 × 3


@dataclass(frozen=True)
class BreakoutVariant:
    name: str
    base_days: int       # N：橫盤天數
    hold_days: int       # H：持有天數


def all_variants() -> list[BreakoutVariant]:
    """N 60／120 × H 14／30 = 4 組。"""
    return [BreakoutVariant(f'breakout-n{n}-h{h}', n, h) for n, h in product((60, 120), (14, 30))]


def by_name(name: str) -> BreakoutVariant:
    for v in all_variants():
        if v.name == name:
            return v
    raise KeyError(f'沒有這組規則：{name}')


# ---- 研究期通過標準：六項全部成立 ----
MIN_EVENTS, MIN_COINS, MIN_T = 30, 5, 3.0
RANDOM_RUNS, RANDOM_PCT = 1000, 0.95


def passes_dev(n: int, coins: int, t: float, halves: list[float], random_pct: float,
               stress_excess: float, drop_best_excess: float) -> tuple[bool, list[str]]:
    """excess＝每次信號扣成本後報酬 − 同期間持有 ETH 的報酬，取平均。

    1. 信號 ≥ MIN_EVENTS 次，來自 ≥ MIN_COINS 個幣
    2. 平均超額報酬 > 0 且 t ≥ MIN_T（t 為正才代表 > 0）
    3. 前後半期平均超額報酬都 > 0
    4. 平均報酬高於隨機進場（同幣、同次數）RANDOM_RUNS 次中的 RANDOM_PCT
    5. 滑價每邊 STRESS_SLIPPAGE 後平均超額報酬 > 0
    6. 拿掉超額報酬合計最多的那個幣後，平均超額報酬 > 0
    """
    fails = []
    if n < MIN_EVENTS or coins < MIN_COINS:
        fails.append(f'信號 {n} 次／{coins} 個幣，不到 {MIN_EVENTS} 次／{MIN_COINS} 個幣')
    if t < MIN_T:
        fails.append(f't = {t:.2f} < {MIN_T}')
    for label, h in zip(('前半', '後半'), halves):
        if h <= 0:
            fails.append(f'{label}沒有比 ETH 多賺')
    if random_pct < RANDOM_PCT:
        fails.append(f'只贏過 {random_pct:.0%} 的隨機進場')
    if stress_excess <= 0:
        fails.append('滑價加倍後沒有比 ETH 多賺')
    if drop_best_excess <= 0:
        fails.append('拿掉最好的幣後沒有比 ETH 多賺')
    return not fails, fails


# ---- 考卷期（只考研究期通過組中 t 最高的一組，只考一次） ----
HOLDOUT_MIN_EVENTS, HOLDOUT_RANDOM_PCT = 15, 0.90


def passes_holdout(n: int, mean_excess: float, random_pct: float) -> tuple[bool, list[str]]:
    fails = []
    if n < HOLDOUT_MIN_EVENTS:
        fails.append(f'信號 {n} 次 < {HOLDOUT_MIN_EVENTS}')
    if mean_excess <= 0:
        fails.append('沒有比 ETH 多賺')
    if random_pct < HOLDOUT_RANDOM_PCT:
        fails.append(f'只贏過 {random_pct:.0%} 的隨機進場')
    return not fails, fails
