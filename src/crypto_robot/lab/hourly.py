"""1h K 線研究共用：時間常數、研究期切點、台北每 3 小時的時點、Series（float 版 K 線）。

原本在第十二輪的 lab/direction3h.py（2026-11-02 刪除，要找回看 DESIGN 6.3）；第十四輪與資金費率前瞻紀錄仍在用。
研究統計用 float（不碰交易路徑）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from crypto_robot.core.models import Bar


def ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


H = 3_600_000
DEV_START, DEV_MID, DEV_END = ms(2021, 1, 1), ms(2022, 7, 1), ms(2024, 1, 1)


@dataclass
class Series:
    """一個市場的 1h K 線。"""
    idx: dict[int, int]
    o: list[float]
    h: list[float]
    l: list[float]
    c: list[float]
    v: list[float]

    @classmethod
    def of(cls, bars: list[Bar]) -> 'Series':
        return cls({b.ts: i for i, b in enumerate(bars)}, [float(b.open) for b in bars], [float(b.high) for b in bars],
                   [float(b.low) for b in bars], [float(b.close) for b in bars], [float(b.volume) for b in bars])

    def span(self, first_ts: int, last_ts: int) -> int | None:
        """first_ts～last_ts 每一根都在就回傳 first_ts 的位置，有缺就 None。"""
        i, j = self.idx.get(first_ts), self.idx.get(last_ts)
        return i if i is not None and j is not None and j - i == (last_ts - first_ts) // H else None


def slots(start: int, end: int) -> list[int]:
    """台北 00、03…21 點（UTC 小時 % 3 == 1）的時點。"""
    s = start + ((1 - start // H) % 3) * H
    return list(range(s, end, 3 * H))
