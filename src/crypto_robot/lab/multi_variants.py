"""第五輪研究：ETH＋USDT＋台幣 三資產比例再平衡（DESIGN.md 6.3）。

使用者的想法（2026-10-26）：股市走向會影響匯率，也會影響虛擬貨幣；把現金的一部分換成 USDT（≈ 美元），
閒錢也有在動，並可能在 ETH 大跌、台幣同時貶值時抵掉一部分損失。

2026-10-26 在跑任何結果前訂定並 commit。看過研究期結果後**不得**為了讓某組通過而修改這裡。

比較對象是**現在的兩資產再平衡（ETH＋台幣，同一個門檻）**：要回答的問題是「把一部分台幣換成 USDT 有沒有更好」。
同比例不再平衡只列出參考。

**已知限制**：研究期 2021–2023 的 ETH 資料已看過四次（知道 2022 崩跌）；USDT/TWD 只看過年度高低點與流動性。
考卷期（2024 起）已在第三輪用掉，這輪**沒有考卷**；不論結果，`SHADOW` 都會放進影子帳戶往後觀察（使用者決定）。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import product

from crypto_robot.agents.strategy.rebalance_multi import MultiParams

ETH_TARGET = Decimal('0.5')
SHADOW = 'rebal3-eth50-cashusdt75-band10'     # 使用者指定的配置：剩下現金的 75% 放 USDT


@dataclass(frozen=True)
class MultiVariant:
    name: str
    params: MultiParams       # 三資產
    baseline: MultiParams     # 對照：兩資產（ETH＋台幣，同門檻）


def all_variants() -> list[MultiVariant]:
    """現金中 USDT 占 50%／75% × 門檻 5／10 個百分點 = 4 組。ETH 固定 50%（和現在的模擬帳戶相同）。"""
    out = []
    for share, band in product(['0.5', '0.75'], ['0.05', '0.10']):
        usdt = (1 - ETH_TARGET) * Decimal(share)
        out.append(MultiVariant(
            f'rebal3-eth50-cashusdt{int(Decimal(share) * 100)}-band{int(Decimal(band) * 100)}',
            MultiParams((('eth', ETH_TARGET), ('usdt', usdt)), Decimal(band)),
            MultiParams((('eth', ETH_TARGET),), Decimal(band)),
        ))
    return out


def by_name(name: str) -> MultiVariant:
    for v in all_variants():
        if v.name == name:
            return v
    raise KeyError(f'沒有這組規則：{name}')


# ---- 通過標準：五項全部成立 ----
MIN_REBALANCE_DAYS = 8


def passes(days: int, ret: float, base_ret: float, mdd: float, base_mdd: float,
           halves: list[dict]) -> tuple[bool, list[str]]:
    """halves：前後半期各一個 dict，含 ret、base_ret（兩資產）。mdd 為負數。

    1. 研究期有再平衡的天數 ≥ MIN_REBALANCE_DAYS
    2. 研究期報酬 > 兩資產再平衡
    3. 前後半期報酬都 > 兩資產再平衡（不能只靠其中一段）
    4. 研究期最大回撤不比兩資產深
    5. 前後半期報酬都 > 0
    """
    fails = []
    if days < MIN_REBALANCE_DAYS:
        fails.append(f'再平衡 {days} 天 < {MIN_REBALANCE_DAYS}')
    if ret <= base_ret:
        fails.append('研究期沒有比兩資產多賺')
    if abs(mdd) > abs(base_mdd):
        fails.append('回撤比兩資產深')
    for label, h in zip(('前半', '後半'), halves):
        if h['ret'] <= h['base_ret']:
            fails.append(f'{label}沒有比兩資產多賺')
        if h['ret'] <= 0:
            fails.append(f'{label}報酬 ≤ 0')
    return not fails, fails
