"""K 線資料品質（DESIGN.md 3.3、D4）：純函式，研究與即時共用。

- 缺漏、時間戳不連續或未對齊、重複、價格異常 → blocking：暫停產生訊號。
- 成交量 0 → 不擋：K 線有效，只是該根不產生進場訊號。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from crypto_robot.core.models import Bar

BLOCKING = ('gap', 'misaligned', 'duplicate', 'out_of_order', 'bad_ohlc', 'jump')


@dataclass(frozen=True)
class Issue:
    kind: str      # gap / misaligned / duplicate / out_of_order / bad_ohlc / jump / zero_volume
    ts: int        # 問題發生的 K 線時間；gap 是缺漏區間的第一根
    detail: str


@dataclass
class QualityReport:
    bars: int
    issues: list[Issue] = field(default_factory=list)

    @property
    def blocking(self) -> list[Issue]:
        return [i for i in self.issues if i.kind in BLOCKING]

    @property
    def ok(self) -> bool:
        return not self.blocking

    def count(self, kind: str) -> int:
        return sum(1 for i in self.issues if i.kind == kind)


def check_bars(bars: list[Bar], period_ms: int, *, max_jump_pct: Decimal = Decimal('0.15')) -> QualityReport:
    """檢查一段應該連續的 K 線。max_jump_pct：開盤價和前一根收盤價差距的上限。"""
    report = QualityReport(len(bars))
    add = report.issues.append
    prev: Bar | None = None
    for b in bars:
        if b.ts % period_ms:
            add(Issue('misaligned', b.ts, f'時間戳沒有對齊 {period_ms // 60_000} 分鐘'))
        if not (b.low <= min(b.open, b.close) and max(b.open, b.close) <= b.high) or b.low <= 0:
            add(Issue('bad_ohlc', b.ts, f'O={b.open} H={b.high} L={b.low} C={b.close}'))
        if b.volume < 0:
            add(Issue('bad_ohlc', b.ts, f'成交量為負：{b.volume}'))
        elif b.volume == 0:
            add(Issue('zero_volume', b.ts, '成交量 0'))
        if prev is not None:
            delta = b.ts - prev.ts
            if delta == 0:
                add(Issue('duplicate', b.ts, '重複的 K 線'))
            elif delta < 0:
                add(Issue('out_of_order', b.ts, '時間倒退'))
            elif delta > period_ms:
                add(Issue('gap', prev.ts + period_ms, f'缺 {delta // period_ms - 1} 根'))
            if prev.close > 0 and abs(b.open - prev.close) / prev.close > max_jump_pct:
                add(Issue('jump', b.ts, f'開盤 {b.open} 對前收 {prev.close} 跳動超過 {max_jump_pct:%}'))
        prev = b
    return report


def missing_ranges(bars: list[Bar], period_ms: int) -> list[tuple[int, int]]:
    """缺漏區間 [start, end)，給補資料用。"""
    out = []
    for a, b in zip(bars, bars[1:]):
        if b.ts - a.ts > period_ms:
            out.append((a.ts + period_ms, b.ts))
    return out


def is_closed(bar_ts: int, period_ms: int, server_ms: int, grace_ms: int = 10_000) -> bool:
    """K 線已收盤：開始時間 + 週期 + 寬限 ≤ 交易所伺服器時間。"""
    return bar_ts + period_ms + grace_ms <= server_ms


def closed_only(bars: list[Bar], period_ms: int, server_ms: int, grace_ms: int = 10_000) -> list[Bar]:
    return [b for b in bars if is_closed(b.ts, period_ms, server_ms, grace_ms)]
