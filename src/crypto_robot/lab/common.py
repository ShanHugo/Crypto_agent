"""研究共用：成本假設、研究期切點、取資料區間、格式化。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from crypto_robot.agents.data.quality import check_bars
from crypto_robot.core.models import Bar
from crypto_robot.lab import periods
from crypto_robot.lab.backtest import CostParams

COSTS = CostParams(taker_fee=Decimal('0.0016'), slippage=Decimal('0.001'))   # MAX 等級 0 taker＋保守滑價
PERIOD_MS = periods.PERIOD_MIN * 60_000
DEV_MID = int(datetime(2022, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)   # 研究期前半／後半切點


def day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%d')


def pct(x: float) -> str:
    return f'{x:+.1%}'


def window(bars: list[Bar], start_ms: int, end_ms: int, warmup: int) -> list[Bar]:
    """[start_ms − 暖機, end_ms) 的 K 線；資料有 blocking 問題就停止，不產生不可靠的報告。"""
    out = [b for b in bars if start_ms - warmup * PERIOD_MS <= b.ts < end_ms]
    report = check_bars(out, PERIOD_MS)
    if not report.ok:
        first = report.blocking[0]
        raise RuntimeError(f'資料有 {len(report.blocking)} 個問題（第一個：{first.kind} {day(first.ts)}），先用 fetch_bars.py 修正')
    if not out or out[0].ts > start_ms - warmup * PERIOD_MS:
        raise RuntimeError('暖機資料不足')
    return out
