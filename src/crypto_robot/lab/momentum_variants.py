"""第十輪研究：MAX 山寨幣「追漲幅前幾名」（橫斷面動能，DESIGN.md 6.3）。

使用者的想法（2026-10-30）：看到漲幅前 5 名還是心癢癢的——漲完才買，還來得及嗎？
每週一（UTC）收盤，在流動性夠的幣裡挑過去 L 天漲最多的前 K 名，等金額持有一週；還在名單的幣不動，只換掉掉出的。

2026-10-30 在跑任何結果前訂定並 commit（使用者同意）。看過研究期結果後**不得**為了讓某組通過而修改這裡。
訂定前只看過每週有幾個幣流動性夠（研究期中位數 11 個），沒有看過排名或報酬。

**已知限制**：只有現在還在 MAX 上的幣（倖存者偏差）；研究期山寨幣資料第七輪用過（只看了突破日）；
每週換手，成本會累積。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from itertools import product


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


DAY_MS = 86_400_000
PERIOD_MIN = 1440
DEV_START, DEV_MID, DEV_END = _ms(2021, 7, 1), _ms(2023, 1, 1), _ms(2024, 7, 1)
HOLDOUT_START = DEV_END

COINS = ('aave ada alice ape apt arb avax bch bnb btc comp doge dot ena ens etc gala gmt gram grt gst hbar hype '
         'link ltc mana max near ondo pol render sand shib sol sui tao trx uni wld xlm xrp xtz yfi').split()
BENCH = 'eth'

TAKER_FEE = Decimal('0.0016')
SLIPPAGE = Decimal('0.005')            # 每邊
STRESS_SLIPPAGE = Decimal('0.01')
LIQ_DAYS = 30
MIN_LIQ = Decimal(500_000)             # 前 30 天每日成交額中位數（台幣）


@dataclass(frozen=True)
class MomentumVariant:
    name: str
    lookback: int      # L：漲幅回看天數
    top: int           # K：買前幾名；可選的幣少於 2K 個就持有現金


def all_variants() -> list[MomentumVariant]:
    """L 7／30 × K 3／5 = 4 組。"""
    return [MomentumVariant(f'mom-l{l}-top{k}', l, k) for l, k in product((7, 30), (3, 5))]


def by_name(name: str) -> MomentumVariant:
    for v in all_variants():
        if v.name == name:
            return v
    raise KeyError(f'沒有這組規則：{name}')


# ---- 研究期通過標準：六項全部成立 ----
MIN_WEEKS, MIN_T = 80, 3.0
RANDOM_RUNS, RANDOM_PCT = 1000, 0.95


def passes_dev(weeks: int, t: float, halves: list[float], random_pct: float, stress_total: float, basket_total: float,
               mdd: float, eth_mdd: float) -> tuple[bool, list[str]]:
    """超額＝每週扣成本後報酬 − 同一週全部可選幣等權重報酬（不扣成本），只算有交易的週。mdd 為負數。

    1. 有交易的週數 ≥ MIN_WEEKS
    2. 每週超額報酬 t ≥ MIN_T
    3. 前後半期平均超額報酬都 > 0
    4. 總報酬贏過隨機挑 K 個幣（同樣成本規則）RANDOM_RUNS 次中的 RANDOM_PCT
    5. 滑價每邊 STRESS_SLIPPAGE 後總報酬仍 > 全部等權重
    6. 最大回撤不比一直持有 ETH 深
    """
    fails = []
    if weeks < MIN_WEEKS:
        fails.append(f'有交易 {weeks} 週 < {MIN_WEEKS}')
    if t < MIN_T:
        fails.append(f't = {t:.2f} < {MIN_T}')
    for label, h in zip(('前半', '後半'), halves):
        if h <= 0:
            fails.append(f'{label}沒有比全部等權重多賺')
    if random_pct < RANDOM_PCT:
        fails.append(f'只贏過 {random_pct:.0%} 的隨機挑選')
    if stress_total <= basket_total:
        fails.append('滑價加倍後沒有比全部等權重多賺')
    if abs(mdd) > abs(eth_mdd):
        fails.append('回撤比持有 ETH 深')
    return not fails, fails


# ---- 考卷期：只考研究期通過組中 t 最高的一組，只考一次 ----
HOLDOUT_RANDOM_PCT = 0.90


def passes_holdout(total: float, basket_total: float, eth_total: float, random_pct: float,
                   mdd: float, eth_mdd: float) -> tuple[bool, list[str]]:
    fails = []
    if total <= basket_total:
        fails.append('沒有比全部等權重多賺')
    if total <= eth_total:
        fails.append('沒有比持有 ETH 多賺')
    if random_pct < HOLDOUT_RANDOM_PCT:
        fails.append(f'只贏過 {random_pct:.0%} 的隨機挑選')
    if abs(mdd) > abs(eth_mdd):
        fails.append('回撤比持有 ETH 深')
    return not fails, fails
