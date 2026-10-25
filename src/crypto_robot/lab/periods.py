"""研究期與考卷期（DESIGN.md 6.3）。

2026-10-26 依資料品質決定（2020 年以前流動性太差），當時尚未看任何策略結果。**定案後不改**。
時間都是 UTC，區間為 [start, end)。
"""
from __future__ import annotations

from datetime import datetime, timezone


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


MARKET = 'ethtwd'
PERIOD_MIN = 60

DEV_START = _ms(2021, 1, 1)
DEV_END = _ms(2024, 1, 1)          # 研究期 2021-01-01 ～ 2023-12-31
HOLDOUT_START = DEV_END            # 考卷期 2024-01-01 起，只考一次
