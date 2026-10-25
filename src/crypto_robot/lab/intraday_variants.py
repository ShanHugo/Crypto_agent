"""第四輪研究：凌晨 01:30～07:30 時段買低賣高，不留倉過夜（DESIGN.md 6.3）。

2026-10-26 在跑任何結果前訂定並 commit，規則與標準經使用者確認（停損改 5%、加最低成交金額）。
看過研究期結果後**不得**為了讓某組通過而修改這裡。

**已知污染**：研究期的日 K 與 1h 資料前三輪已看過；5 分 K 只看過凌晨時段的流動性（成交額、成交量 0 的比例），
沒有看過任何價格行為。「不接刀開關」的靈感來自第二輪。乾淨的檢驗只剩考卷期。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import product

from crypto_robot.agents.strategy.intraday import BandRule, PullbackRule


@dataclass(frozen=True)
class IntradayVariant:
    name: str
    rule: object
    use_filter: bool


def all_variants() -> list[IntradayVariant]:
    """回檔買進 6 組＋偏離均線 2 組，各加不接刀開關 = 16 組。"""
    rules = [(f'pullback-d{d}-t{t}', PullbackRule(Decimal(d) / 100, Decimal(t) / 100))
             for d, t in product(['0.5', '1.0', '1.5'], ['0.5', '1.0'])]
    rules += [(f'band-k{k}', BandRule(Decimal(k))) for k in ['2', '2.5']]
    return [IntradayVariant(name + ('-trend' if f else ''), rule, f) for (name, rule), f in product(rules, [False, True])]


def by_name(name: str) -> IntradayVariant:
    for v in all_variants():
        if v.name == name:
            return v
    raise KeyError(f'沒有這組規則：{name}')


# ---- 通過標準：七項全部成立 ----
MIN_TRADES = 100
MIN_T_STAT = 3.0
MAX_DRAWDOWN = 0.10
MIN_AVG_FILL = 10_000       # 平均每筆成交金額（台幣），2026-10-26 使用者加入


def passes(trades: int, t: float, mdd: float, stress_ret: float, avg_fill: float,
           halves: list[dict]) -> tuple[bool, list[str]]:
    """halves：前後半期各一個 dict，含 ret、hold_ret（同樣可交易的夜晚，時段內單純持有、不計成本）。

    1. 研究期完成交易 ≥ MIN_TRADES
    2. 每筆淨報酬平均值 t ≥ MIN_T_STAT
    3. 前後半期累積報酬都 > 0
    4. 前後半期都贏時段內單純持有（不計成本，比較嚴）
    5. 最大回撤 ≤ MAX_DRAWDOWN
    6. 成本壓力測試（限價單也以市價計）累積報酬 > 0
    7. 平均每筆成交 ≥ MIN_AVG_FILL 台幣（凌晨流動性薄，金額太小不值得做）
    """
    fails = []
    if trades < MIN_TRADES:
        fails.append(f'交易 {trades} 筆 < {MIN_TRADES}')
    if t < MIN_T_STAT:
        fails.append(f't = {t:.2f} < {MIN_T_STAT}')
    for label, h in zip(('前半', '後半'), halves):
        if h['ret'] <= 0:
            fails.append(f'{label}報酬 ≤ 0')
        if h['ret'] <= h['hold_ret']:
            fails.append(f'{label}沒贏時段內持有')
    if abs(mdd) > MAX_DRAWDOWN:
        fails.append(f'回撤 {mdd:.1%} 超過 {MAX_DRAWDOWN:.0%}')
    if stress_ret <= 0:
        fails.append('壓力測試虧損')
    if avg_fill < MIN_AVG_FILL:
        fails.append(f'平均每筆 {avg_fill:,.0f} 元 < {MIN_AVG_FILL:,}')
    return not fails, fails
