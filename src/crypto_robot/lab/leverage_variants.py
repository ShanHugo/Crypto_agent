"""第十一輪研究（槓桿第 3 關，DESIGN.md 6.6）：ETH 槓桿版比例再平衡（借 USDT 買 ETH）。

營運者 2026-11-01 決定提前做第 3 關的回測（只回測、不碰錢；第 4、5 關仍要等第 1、2 關通過）。
2026-11-01 在跑任何結果前訂定並 commit（使用者同意）。看過結果後**不得**為了讓某組通過而修改這裡。

MAX 借滿益（2026-11-01 查）：台幣不能借；USDT 目前可借額度 0（回測假設借得到）；利率只查得到現在的，沒有歷史；
維持率（資產 ÷ 負債）1.3 追繳、1.2 強制平倉，來自第三方文章與 2022 年官方部落格，沒有官方最新數字。

**已知限制**：ETH 沒有乾淨的考卷期（2022 大跌大家都知道）；利率固定用現在的數字；強平的處理是假設。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import product


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


START, MID = _ms(2021, 1, 1), _ms(2024, 1, 1)      # 全部資料，前後半以 2024-01-01 切開；結束＝資料最後一個完整台北日
MARKETS = {'eth': 'ethtwd', 'usdt': 'usdttwd'}

TAKER_FEE, SLIPPAGE = 0.0016, 0.001
USDT_HOURLY_RATE = 0.0000182        # 2026-11-01 MAX /wallet/m/interest_rates（年化約 16%）
STRESS_RATE_MULT = 2
LIQUIDATION_AD = 1.2                # 資產 ÷ 負債 ≤ 這個值就強平
MARGIN_CALL_AD = 1.3
LIQUIDATION_PENALTY = 0.05          # 強平殘值先扣資產的 5%（假設）
MAX_DRAWDOWN = -0.60


@dataclass(frozen=True)
class LeverageVariant:
    name: str
    lev: float        # L：ETH 部位 ÷ 權益
    band: float       # B：實際槓桿偏離 ±B 就調回


def all_variants() -> list[LeverageVariant]:
    """L 1.5／2 × B 0.2／0.5 = 4 組。"""
    return [LeverageVariant(f'lev{l:g}-band{b:g}', l, b) for l, b in product((1.5, 2.0), (0.2, 0.5))]


def passes(liquidated: bool, min_ad: float, halves: list[float], base_halves: list[float], mdd: float,
           stress_halves: list[float]) -> tuple[bool, list[str]]:
    """base＝同一個模擬、不開槓桿（1 倍＝一直持有 ETH）。mdd 為負數。

    1. 整段沒有強平
    2. 維持率最低值 > MARGIN_CALL_AD（沒收過追繳）
    3. 前後半期報酬都 > 1 倍
    4. 最大回撤不超過 MAX_DRAWDOWN
    5. 利率 × STRESS_RATE_MULT 後前後半期報酬仍 > 1 倍
    """
    fails = []
    if liquidated:
        fails.append('被強制平倉')
    if min_ad <= MARGIN_CALL_AD:
        fails.append(f'維持率最低 {min_ad:.2f}，碰到追繳線 {MARGIN_CALL_AD}')
    for label, h, b, s in zip(('前半', '後半'), halves, base_halves, stress_halves):
        if h <= b:
            fails.append(f'{label}沒有比 1 倍多賺')
        if s <= b:
            fails.append(f'{label}利率加倍後沒有比 1 倍多賺')
    if mdd < MAX_DRAWDOWN:
        fails.append(f'最大回撤 {mdd:.0%} 超過 {MAX_DRAWDOWN:.0%}')
    return not fails, fails
