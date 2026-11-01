"""第十一輪：ETH 槓桿再平衡的逐小時模擬（研究用，用 float；不碰交易路徑）。

- 期初第一根開盤：自有資金＋借 USDT，買到 ETH 部位 = L × 權益（手續費＋滑價算在成交金額上）。
- 每小時：USDT 負債按小時利率複利；用該小時 ETH 最低價、USDT/TWD 最高價算維持率（資產 ÷ 負債），
  ≤ LIQUIDATION_AD 就強平：殘值 = max(0, 資產 − 負債 − 資產 × 罰金)，帳戶結束。
- 每天台北 00:00 那根收盤檢查實際槓桿，偏離 L 達 ±B 就在下一根開盤調回（多借買 ETH，或賣 ETH 還款）。
- lev = 1 時沒有負債、不會再平衡，就是一直持有 ETH（扣一次進場成本）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from crypto_robot.agents.data.resample import TAIPEI_OFFSET_MS
from crypto_robot.core.models import Bar
from crypto_robot.lab.leverage_variants import LIQUIDATION_AD, LIQUIDATION_PENALTY, SLIPPAGE, TAKER_FEE

HOUR_MS = 3_600_000
COST = TAKER_FEE + SLIPPAGE


@dataclass
class LevResult:
    equity: list[tuple[int, float]] = field(default_factory=list)
    liquidated_at: int | None = None
    min_ad: float = float('inf')
    rebalances: int = 0
    interest: float = 0.0          # 累計利息（台幣，按當時匯率）


def align(eth: list[Bar], usdt: list[Bar], start_ms: int, end_ms: int) -> list[tuple[Bar, Bar]]:
    u = {b.ts: b for b in usdt}
    return [(e, u[e.ts]) for e in eth if start_ms <= e.ts < end_ms and e.ts in u]


def simulate(rows: list[tuple[Bar, Bar]], lev: float, band: float, hourly_rate: float, initial: float = 10_000.0) -> LevResult:
    res = LevResult()
    q = debt = 0.0                 # ETH 數量、USDT 負債
    pending = False
    for n, (e, u) in enumerate(rows):
        eo, uo = float(e.open), float(u.open)
        if n == 0:                                         # 自有資金＋借 (L−1) 倍，成本從買到的 ETH 扣
            q = lev * initial * (1 - COST) / eo
            debt = (lev - 1) * initial / uo
        elif pending:
            eq = q * eo - debt * uo
            delta = lev * eq - q * eo                      # 要多買（正）或賣（負）的 ETH 金額（台幣），全部用借款／還款
            q += delta / eo
            debt += (delta + abs(delta) * COST) / uo       # 成本算進負債（等於權益減少）
            pending = False
            res.rebalances += 1
        if debt > 0:
            res.interest += debt * hourly_rate * uo
            debt *= 1 + hourly_rate
            assets, liab = q * float(e.low), debt * float(u.high)
            ad = assets / liab
            res.min_ad = min(res.min_ad, ad)
            if ad <= LIQUIDATION_AD:
                res.liquidated_at = e.ts
                left = max(0.0, assets - liab - assets * LIQUIDATION_PENALTY)
                res.equity.append((e.ts, left))
                res.equity += [(x.ts, left) for x, _ in rows[n + 1:]]
                return res
        ec, uc = float(e.close), float(u.close)
        eq = q * ec - debt * uc
        res.equity.append((e.ts, eq))
        if (e.ts + HOUR_MS + TAIPEI_OFFSET_MS) % 86_400_000 == 0 and eq > 0:     # 台北 00:00 收盤
            pending = abs(q * ec / eq - lev) >= band
    return res


def segment(res: LevResult, a: int, b: int, initial: float = 10_000.0) -> float:
    before = [v for ts, v in res.equity if ts < a]
    inside = [v for ts, v in res.equity if a <= ts < b]
    base = before[-1] if before else initial
    return inside[-1] / base - 1 if inside and base > 0 else -1.0
