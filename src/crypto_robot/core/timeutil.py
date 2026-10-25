"""台北時間工具。交易時段、每日紀錄都以台北時間為準。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

TAIPEI = timezone(timedelta(hours=8))


def taipei_hm(ts: int) -> tuple[int, int]:
    d = datetime.fromtimestamp(ts / 1000, TAIPEI)
    return d.hour, d.minute


def taipei_date(ts: int) -> date:
    return datetime.fromtimestamp(ts / 1000, TAIPEI).date()
