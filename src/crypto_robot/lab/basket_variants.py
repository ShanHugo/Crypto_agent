"""第九輪研究：BTC＋ETH＋USDT＋台幣 四資產比例再平衡（DESIGN.md 6.3）。

第六～八輪找時機、找飆漲幣都沒通過；八輪裡留下來的只有比例再平衡（分散＋漲多賣跌多買）。
這輪問：把虛擬貨幣的 50% 拆成 BTC＋ETH，會不會比現在真單的規則更好？

2026-10-30 在跑任何結果前訂定並 commit（使用者同意）。看過研究期結果後**不得**為了讓某組通過而修改這裡。
比較對象是**現在真單的規則** `rebal3-eth50-cashusdt75-band5`（ETH 50%、USDT 37.5%、台幣 12.5%，門檻 5）。

**已知限制**：研究期的 ETH 已看過六次；考卷期 2024-07 起 BTC 沒看過、ETH 看過（半乾淨）；BTC 和 ETH 高度相關。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from itertools import product

from crypto_robot.agents.strategy.rebalance_multi import MultiParams
from crypto_robot.lab.multi_variants import by_name as round5


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


DEV_START, DEV_MID, DEV_END = _ms(2021, 1, 1), _ms(2022, 10, 1), _ms(2024, 7, 1)
HOLDOUT_START = DEV_END
MARKETS = {'btc': 'btctwd', 'eth': 'ethtwd', 'usdt': 'usdttwd'}
USDT = Decimal('0.375')
BASELINE_NAME = 'rebal3-eth50-cashusdt75-band5'
BASELINE = round5(BASELINE_NAME).params


@dataclass(frozen=True)
class BasketVariant:
    name: str
    params: MultiParams


def all_variants() -> list[BasketVariant]:
    """BTC／ETH 25／25、15／35 × 門檻 5／10 = 4 組；USDT 37.5%、台幣 12.5% 不變。"""
    out = []
    for (btc, eth), band in product((('0.25', '0.25'), ('0.15', '0.35')), ('0.05', '0.10')):
        b, e, d = Decimal(btc), Decimal(eth), Decimal(band)
        out.append(BasketVariant(f'btc{int(b * 100)}-eth{int(e * 100)}-band{int(d * 100)}',
                                 MultiParams((('btc', b), ('eth', e), ('usdt', USDT)), d)))
    return out


def by_name(name: str) -> BasketVariant:
    for v in all_variants():
        if v.name == name:
            return v
    raise KeyError(f'沒有這組規則：{name}')


# ---- 研究期通過標準：五項全部成立 ----
MIN_REBALANCE_DAYS = 8
STRESS_MULT = 2


def passes_dev(days: int, ret: float, base_ret: float, mdd: float, base_mdd: float, halves: list[dict],
               stress_ret: float, stress_base_ret: float) -> tuple[bool, list[str]]:
    """halves：前後半期各一個 dict，含 ret、base_ret。mdd 為負數。

    1. 研究期有再平衡的天數 ≥ MIN_REBALANCE_DAYS
    2. 研究期報酬 > 現行規則
    3. 前後半期報酬都 > 現行規則
    4. 最大回撤不比現行規則深
    5. 滑價 × STRESS_MULT（兩邊都乘）後報酬仍 > 現行規則
    """
    fails = []
    if days < MIN_REBALANCE_DAYS:
        fails.append(f'再平衡 {days} 天 < {MIN_REBALANCE_DAYS}')
    if ret <= base_ret:
        fails.append('研究期沒有比現行規則多賺')
    for label, h in zip(('前半', '後半'), halves):
        if h['ret'] <= h['base_ret']:
            fails.append(f'{label}沒有比現行規則多賺')
    if abs(mdd) > abs(base_mdd):
        fails.append('回撤比現行規則深')
    if stress_ret <= stress_base_ret:
        fails.append(f'滑價 ×{STRESS_MULT} 後沒有比現行規則多賺')
    return not fails, fails


# ---- 考卷期：只考研究期通過組中「研究期報酬 − 現行規則」最大的一組，只考一次 ----
def passes_holdout(ret: float, base_ret: float, mdd: float, base_mdd: float) -> tuple[bool, list[str]]:
    fails = []
    if ret <= base_ret:
        fails.append('沒有比現行規則多賺')
    if abs(mdd) > abs(base_mdd):
        fails.append('回撤比現行規則深')
    return not fails, fails
