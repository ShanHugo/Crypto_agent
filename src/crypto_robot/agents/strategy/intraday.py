"""凌晨時段買低賣高（研究與即時共用）：每天台北時間 01:30 開始、07:20 起強制平倉、07:30 前清空，不留倉過夜。

進場與停利用限價單（一次只掛一張，不是網格）；停損與強制平倉用市價單。
規則只負責算「掛單價格」，掛單、成交、重試由執行端處理。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

TAIPEI = timezone(timedelta(hours=8))
WINDOW_START = (1, 30)      # 開始判斷、掛單
EXIT_AT = (7, 20)           # 撤單並市價平倉（留 10 分鐘重試）
WINDOW_END = (7, 30)        # 必須清空
MAX_ROUND_TRIPS = 3         # 每晚最多來回次數
STOP_PCT = Decimal('0.05')  # 虧損達 5% 市價出場，當晚不再進場（2026-10-26 使用者決定）

ONE = Decimal(1)


def taipei_hm(ts: int) -> tuple[int, int]:
    d = datetime.fromtimestamp(ts / 1000, TAIPEI)
    return d.hour, d.minute


def taipei_date(ts: int) -> date:
    return datetime.fromtimestamp(ts / 1000, TAIPEI).date()


def in_trading_window(ts: int) -> bool:
    """K 線開始時間在 [01:30, 07:20) 之間：可以進場與停利。"""
    return WINDOW_START <= taipei_hm(ts) < EXIT_AT


def _mean_std(xs: list[Decimal]) -> tuple[Decimal, Decimal]:
    n = len(xs)
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / n
    return mean, var.sqrt()


@dataclass(frozen=True)
class PullbackRule:
    """相對 01:30 開盤價跌 drop 時限價買，漲到買價 +take 時限價賣。"""
    drop: Decimal
    take: Decimal
    lookback: int = 0

    def entry_level(self, ref: Decimal, closes: list[Decimal]) -> Decimal | None:
        return ref * (ONE - self.drop)

    def exit_level(self, entry_price: Decimal, closes: list[Decimal]) -> Decimal | None:
        return entry_price * (ONE + self.take)


@dataclass(frozen=True)
class BandRule:
    """收盤低於近 n 根均線 − k 倍標準差的位置限價買，回到均線限價賣。n=24 根 5 分 K = 2 小時。"""
    k: Decimal
    lookback: int = 24

    def entry_level(self, ref: Decimal, closes: list[Decimal]) -> Decimal | None:
        if len(closes) < self.lookback:
            return None
        mean, sd = _mean_std(closes[-self.lookback:])
        return mean - self.k * sd

    def exit_level(self, entry_price: Decimal, closes: list[Decimal]) -> Decimal | None:
        if len(closes) < self.lookback:
            return None
        return sum(closes[-self.lookback:]) / self.lookback


def trend_ok(daily_closes: list[Decimal], n: int = 20) -> bool:
    """不接刀開關：前一天日 K 收盤在 n 日均線之上，當晚才做。daily_closes 到前一天為止。"""
    if len(daily_closes) < n:
        return False
    return daily_closes[-1] > sum(daily_closes[-n:]) / n
