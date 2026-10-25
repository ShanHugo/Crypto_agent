"""回測結果的統計。這裡用 float 沒問題：只做報告，不碰交易路徑。"""
from __future__ import annotations

import math
from dataclasses import dataclass

from crypto_robot.lab.backtest import Result

YEAR_MS = 365.25 * 24 * 3_600_000


@dataclass(frozen=True)
class Stats:
    total_return: float
    cagr: float
    max_drawdown: float      # 負數
    trades: int              # 已平倉筆數（不含期末未平倉）
    win_rate: float
    avg_trade: float         # 已平倉每筆平均淨報酬
    t_stat: float            # 每筆淨報酬平均值的 t 值
    exposure: float          # 持有部位的時間比例
    cost_pct: float          # 手續費＋滑價占期初資金比例
    open_at_end: bool


def max_drawdown(values: list[float]) -> float:
    peak, worst = -math.inf, 0.0
    for v in values:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst


def t_stat(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean = sum(xs) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1))
    return 0.0 if sd == 0 else mean / (sd / math.sqrt(n))


def stats(res: Result) -> Stats:
    initial = float(res.initial)
    eq = [float(v) for _, v in res.equity]
    if not eq:
        return Stats(0, 0, 0, 0, 0, 0, 0, 0, 0, False)
    total = eq[-1] / initial - 1
    years = (res.equity[-1][0] - res.equity[0][0] + 3_600_000) / YEAR_MS
    cagr = (eq[-1] / initial) ** (1 / years) - 1 if years > 0 and eq[-1] > 0 else -1.0
    closed = [float(t.net_return) for t in res.trades if t.reason != 'open']
    return Stats(
        total_return=total, cagr=cagr, max_drawdown=max_drawdown([initial] + eq),
        trades=len(closed),
        win_rate=sum(1 for r in closed if r > 0) / len(closed) if closed else 0.0,
        avg_trade=sum(closed) / len(closed) if closed else 0.0,
        t_stat=t_stat(closed),
        exposure=res.bars_in_position / len(eq),
        cost_pct=float(res.costs) / initial,
        open_at_end=any(t.reason == 'open' for t in res.trades),
    )


def segment_return(res: Result, start_ms: int, end_ms: int) -> float:
    """權益在 [start_ms, end_ms) 之間的報酬（以區間前一根收盤權益為起點）。"""
    before = [v for ts, v in res.equity if ts < start_ms]
    inside = [v for ts, v in res.equity if start_ms <= ts < end_ms]
    if not inside:
        return 0.0
    base = before[-1] if before else res.initial
    return float(inside[-1] / base) - 1


def segment_drawdown(res: Result, start_ms: int, end_ms: int) -> float:
    """[start_ms, end_ms) 之間的最大回撤（以區間前一根收盤權益為起點）。"""
    before = [float(v) for ts, v in res.equity if ts < start_ms]
    inside = [float(v) for ts, v in res.equity if start_ms <= ts < end_ms]
    base = before[-1] if before else float(res.initial)
    return max_drawdown([base] + inside)
