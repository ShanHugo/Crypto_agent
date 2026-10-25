"""第二輪研究：日 K、交易少，目標是降低回撤、或靠買低賣高賺價差（DESIGN.md 6.3）。

2026-10-26 在跑任何日 K 結果前訂定並 commit。看過研究期結果後**不得**為了讓某組通過而修改這裡。

**已知的污染**：研究期資料在第一輪（1h EMA 交叉）已經看過，其中「50/200 均線＋死叉出場回撤較小」的觀察
是 TrendSMA 這一類的靈感來源。所以 TrendSMA 在研究期表現好，可信度打折；乾淨的檢驗只剩考卷期（只考一次）。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import product

from crypto_robot.agents.strategy.daily_rules import MeanRevSMA, RsiRevert, TrendSMA
from crypto_robot.lab.variants import COSTS  # noqa: F401  成本假設和第一輪相同


@dataclass
class DailyVariant:
    name: str
    family: str          # trend / meanrev / rsi
    rule: object


def all_variants() -> list[DailyVariant]:
    """趨勢 6 組＋均線乖離 12 組＋RSI 4 組 = 22 組。"""
    out = []
    for n, confirm in product([50, 100, 200], [1, 3]):
        out.append(DailyVariant(f'trend-sma{n}-c{confirm}', 'trend', TrendSMA(n, confirm)))
    for dev, hold, stop in product(['0.10', '0.15', '0.20'], [None, 30], [None, '0.15']):
        name = f'meanrev-sma20-dev{int(Decimal(dev) * 100)}' + (f'-hold{hold}' if hold else '') + \
               (f'-stop{int(Decimal(stop) * 100)}' if stop else '')
        out.append(DailyVariant(name, 'meanrev', MeanRevSMA(Decimal(dev), 20, hold, Decimal(stop) if stop else None)))
    for low, high in product([25, 30], [55, 70]):
        out.append(DailyVariant(f'rsi14-{low}-{high}', 'rsi', RsiRevert(low, high)))
    return out


def by_name(name: str) -> DailyVariant:
    for v in all_variants():
        if v.name == name:
            return v
    raise KeyError(f'沒有這組規則：{name}')


# ---- 通過標準：五項全部成立 ----
MIN_TRADES = 8           # 研究期 3 年完成交易筆數（日線規則交易少，t 值沒有意義，改看回撤與前後半期）
MAX_DD_VS_HODL = 0.6     # 最大回撤不超過一直持有的 60%（持有 -80% → 規則要比 -48% 淺）


def passes(trades: int, mdd: float, hodl_mdd: float, halves: list[dict]) -> tuple[bool, list[str]]:
    """halves：前後半期各一個 dict，含 ret、mdd、hodl_ret、hodl_mdd（mdd 為負數）。

    1. 完成交易 ≥ MIN_TRADES
    2. 研究期最大回撤 ≤ 一直持有的 MAX_DD_VS_HODL 倍（|mdd| ≤ 0.6 × |hodl_mdd|）
    3. 前後半期報酬都 > 0
    4. 前後半期都不輸一直持有 ETH：報酬較高，或「報酬 ÷ |最大回撤|」較高
    5. 前後半期的最大回撤都比一直持有淺
    """
    fails = []
    if trades < MIN_TRADES:
        fails.append(f'交易 {trades} 筆 < {MIN_TRADES}')
    if abs(mdd) > MAX_DD_VS_HODL * abs(hodl_mdd):
        fails.append(f'回撤 {mdd:.0%} 不夠淺（需 ≤ {MAX_DD_VS_HODL * abs(hodl_mdd):.0%}）')
    for label, h in zip(('前半', '後半'), halves):
        if h['ret'] <= 0:
            fails.append(f'{label}報酬 ≤ 0')
        else:
            ratio = h['ret'] / abs(h['mdd']) if h['mdd'] else float('inf')
            hodl_ratio = h['hodl_ret'] / abs(h['hodl_mdd']) if h['hodl_mdd'] else float('inf')
            if h['ret'] < h['hodl_ret'] and ratio < hodl_ratio:
                fails.append(f'{label}輸給持有 ETH')
        if abs(h['mdd']) >= abs(h['hodl_mdd']):
            fails.append(f'{label}回撤沒有比持有淺')
    return not fails, fails
