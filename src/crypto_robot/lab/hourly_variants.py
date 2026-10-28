"""第六輪研究：三資產再平衡改成每小時檢查（DESIGN.md 6.3）。

使用者的問題（2026-10-28）：不限 01:35 一天一次，只要偏離達門檻隨時調回，收益會不會比較好？

2026-10-28 在跑任何結果前訂定並 commit（使用者同意）。看過研究期結果後**不得**為了讓某組通過而修改這裡。

比較對象是**同一條規則、一天檢查一次**（現在模擬帳戶與真單的做法）。
規則固定 ETH 50%＋USDT 37.5%＋台幣 12.5%（現在用的配置），門檻 5／10 個百分點。

**已知限制**：研究期 2021–2023 已看過五次（第五輪也用三資產跑過）；考卷期已用掉，這輪**沒有考卷**。
就算通過也只代表「值得再觀察」，不會直接改真單。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from itertools import product

from crypto_robot.agents.data.resample import HOUR_MS, TAIPEI_OFFSET_MS
from crypto_robot.agents.strategy.rebalance_multi import MultiParams

ETH, USDT = Decimal('0.5'), Decimal('0.375')
SLIPPAGE_STRESS = 2           # 第 5 項：滑價乘這個倍數（每日版也一樣）


def in_window(ts: int) -> bool:
    """1h K 線開盤時間落在台北 01:30～08:00 內（02:00～07:00 開盤的 6 根）。"""
    return 2 <= (ts + TAIPEI_OFFSET_MS) // HOUR_MS % 24 <= 7


@dataclass(frozen=True)
class HourlyVariant:
    name: str
    params: MultiParams
    can_trade: Callable[[int], bool] | None     # None：24 小時都可以下單


def all_variants() -> list[HourlyVariant]:
    """門檻 5／10 × 24 小時／只在 01:30～08:00 下單 = 4 組。"""
    out = []
    for band, (label, gate) in product(['0.05', '0.10'], [('24h', None), ('win', in_window)]):
        out.append(HourlyVariant(f'rebal3-eth50-cashusdt75-band{int(Decimal(band) * 100)}-1h-{label}',
                                 MultiParams((('eth', ETH), ('usdt', USDT)), Decimal(band)), gate))
    return out


# ---- 通過標準：五項全部成立 ----
MIN_REBALANCES = 8


def passes(n: int, ret: float, base_ret: float, mdd: float, base_mdd: float,
           halves: list[dict], stress_ret: float, stress_base_ret: float) -> tuple[bool, list[str]]:
    """base 是同規則的每日版。halves：前後半期各一個 dict，含 ret、base_ret。mdd 為負數。

    1. 研究期再平衡次數 ≥ MIN_REBALANCES
    2. 研究期報酬 > 每日版
    3. 前後半期報酬都 > 每日版（不能只靠其中一段）
    4. 研究期最大回撤不比每日版深
    5. 滑價乘 SLIPPAGE_STRESS（兩邊都乘）後報酬仍 > 每日版
    """
    fails = []
    if n < MIN_REBALANCES:
        fails.append(f'再平衡 {n} 次 < {MIN_REBALANCES}')
    if ret <= base_ret:
        fails.append('研究期沒有比每日版多賺')
    for label, h in zip(('前半', '後半'), halves):
        if h['ret'] <= h['base_ret']:
            fails.append(f'{label}沒有比每日版多賺')
    if abs(mdd) > abs(base_mdd):
        fails.append('回撤比每日版深')
    if stress_ret <= stress_base_ret:
        fails.append(f'滑價 ×{SLIPPAGE_STRESS} 後沒有比每日版多賺')
    return not fails, fails
