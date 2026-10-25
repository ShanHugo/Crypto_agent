"""第三輪研究：ETH／台幣比例再平衡（DESIGN.md 6.3）。

2026-10-26 在跑任何再平衡結果前訂定並 commit。看過研究期結果後**不得**為了讓某組通過而修改這裡。

基準是「同比例買進後不再調整」：要回答的問題是「再平衡本身（買低賣高）有沒有多賺」，
不是和全部押 ETH 比（比例低於 100% 在大漲期一定少賺，那是配置的選擇，不是再平衡的功勞）。

**已知污染**：研究期資料前兩輪已看過（知道 ETH 在 2021～2023 先大漲、2022 崩跌、2023 回升）。乾淨的檢驗只剩考卷期。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import product

from crypto_robot.agents.strategy.rebalance import RebalanceParams


@dataclass(frozen=True)
class RebalanceVariant:
    name: str
    params: RebalanceParams

    @property
    def warmup(self) -> int:
        return 0


def all_variants() -> list[RebalanceVariant]:
    """目標比例 30／50／70% × 偏離門檻 5／10／15／20 個百分點 = 12 組。"""
    out = []
    for target, band in product(['0.3', '0.5', '0.7'], ['0.05', '0.10', '0.15', '0.20']):
        name = f'rebal-eth{int(Decimal(target) * 100)}-band{int(Decimal(band) * 100)}'
        out.append(RebalanceVariant(name, RebalanceParams(Decimal(target), Decimal(band))))
    return out


def by_name(name: str) -> RebalanceVariant:
    for v in all_variants():
        if v.name == name:
            return v
    raise KeyError(f'沒有這組規則：{name}')


# ---- 通過標準：五項全部成立 ----
MIN_REBALANCES = 8


def passes(rebalances: int, ret: float, static_ret: float, mdd: float, static_mdd: float,
           halves: list[dict]) -> tuple[bool, list[str]]:
    """halves：前後半期各一個 dict，含 ret、static_ret。mdd 為負數。

    1. 研究期再平衡次數 ≥ MIN_REBALANCES
    2. 研究期報酬 > 同比例不再平衡（扣成本後再平衡有多賺）
    3. 前後半期都多賺（不能只靠其中一段）
    4. 研究期最大回撤不比同比例不再平衡深
    5. 前後半期報酬都 > 0（真的賺到錢，而不只是少虧）
    """
    fails = []
    if rebalances < MIN_REBALANCES:
        fails.append(f'再平衡 {rebalances} 次 < {MIN_REBALANCES}')
    if ret <= static_ret:
        fails.append('研究期沒有比不再平衡多賺')
    if abs(mdd) > abs(static_mdd):
        fails.append('回撤比不再平衡深')
    for label, h in zip(('前半', '後半'), halves):
        if h['ret'] <= h['static_ret']:
            fails.append(f'{label}沒有比不再平衡多賺')
        if h['ret'] <= 0:
            fails.append(f'{label}報酬 ≤ 0')
    return not fails, fails
