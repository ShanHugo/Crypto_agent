"""1h K 線合成日 K。一天以台北時間 00:00 切開（UTC 16:00），和每日風控基準同一個日界。"""
from __future__ import annotations

from crypto_robot.core.models import Bar

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
TAIPEI_OFFSET_MS = 8 * HOUR_MS


def day_start(ts: int) -> int:
    """ts 所在台北日的開始時間（UTC 毫秒）。"""
    return (ts + TAIPEI_OFFSET_MS) // DAY_MS * DAY_MS - TAIPEI_OFFSET_MS


def to_daily(hourly: list[Bar]) -> list[Bar]:
    """只輸出 24 根 1h K 線都在的完整日（未收盤或有缺漏的日子丟掉）。日 K 的 ts 是該日開始時間。"""
    out: list[Bar] = []
    group: list[Bar] = []

    def flush():
        if len(group) == 24:
            out.append(Bar(day_start(group[0].ts), group[0].open, max(b.high for b in group),
                           min(b.low for b in group), group[-1].close, sum(b.volume for b in group)))

    for b in hourly:
        if group and day_start(b.ts) != day_start(group[0].ts):
            flush()
            group = []
        group.append(b)
    if group:
        flush()
    return out
