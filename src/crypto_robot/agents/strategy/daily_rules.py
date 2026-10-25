"""日 K 規則（研究與即時共用）。每天收盤後判斷，隔天開盤成交。

三類：
- TrendSMA：收盤站上 N 日均線才持有，跌破就賣（目標：避開長空頭，降低回撤）。
- MeanRevSMA：跌到 N 日均線下方一定幅度時買、回到均線時賣（買低賣高）。
- RsiRevert：RSI 過低時買、回升到一定程度時賣（買低賣高）。

decide(i, pos) 回傳 'buy'／'sell'／None；pos 是目前部位（None 表示空手），含 entry_price、entry_i。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from crypto_robot.core.models import Bar

ONE = Decimal(1)


def sma(values: list[Decimal], n: int) -> list[Decimal | None]:
    out: list[Decimal | None] = [None] * len(values)
    total = Decimal(0)
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def rsi(values: list[Decimal], n: int = 14) -> list[Decimal | None]:
    """Wilder RSI。"""
    out: list[Decimal | None] = [None] * len(values)
    if len(values) <= n:
        return out
    gains = [max(values[i] - values[i - 1], Decimal(0)) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], Decimal(0)) for i in range(1, len(values))]
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for i in range(n, len(values)):
        if i > n:
            avg_g = (avg_g * (n - 1) + gains[i - 1]) / n
            avg_l = (avg_l * (n - 1) + losses[i - 1]) / n
        out[i] = Decimal(100) if avg_l == 0 else Decimal(100) - Decimal(100) / (ONE + avg_g / avg_l)
    return out


@dataclass
class TrendSMA:
    n: int
    confirm: int = 1          # 連續幾天站上／跌破才動作

    @property
    def warmup(self) -> int:
        return self.n + self.confirm

    def prepare(self, bars: list[Bar]):
        self._bars = bars
        self._ma = sma([b.close for b in bars], self.n)
        return self

    def decide(self, i: int, pos: dict | None) -> str | None:
        window = range(i - self.confirm + 1, i + 1)
        if any(j < 0 or self._ma[j] is None for j in window):
            return None
        above = all(self._bars[j].close > self._ma[j] for j in window)
        below = all(self._bars[j].close < self._ma[j] for j in window)
        if pos is None and above:
            return 'buy'
        if pos is not None and below:
            return 'sell'
        return None


@dataclass
class MeanRevSMA:
    dev: Decimal                       # 收盤 ≤ 均線 × (1 − dev) 時買
    n: int = 20
    max_hold_days: int | None = None   # 持有超過幾天就賣（None = 不限）
    stop: Decimal | None = None        # 收盤 ≤ 進場價 × (1 − stop) 時賣（None = 不設）

    @property
    def warmup(self) -> int:
        return self.n

    def prepare(self, bars: list[Bar]):
        self._bars = bars
        self._ma = sma([b.close for b in bars], self.n)
        return self

    def decide(self, i: int, pos: dict | None) -> str | None:
        ma, close = self._ma[i], self._bars[i].close
        if ma is None:
            return None
        if pos is None:
            return 'buy' if close <= ma * (ONE - self.dev) else None
        if close >= ma:
            return 'sell'
        if self.stop is not None and close <= pos['entry_price'] * (ONE - self.stop):
            return 'sell'
        if self.max_hold_days is not None and i - pos['entry_i'] >= self.max_hold_days:
            return 'sell'
        return None


@dataclass
class RsiRevert:
    low: int                 # RSI < low 時買
    high: int                # RSI > high 時賣
    n: int = 14

    @property
    def warmup(self) -> int:
        return 3 * self.n    # Wilder 平滑需要較長暖機

    def prepare(self, bars: list[Bar]):
        self._rsi = rsi([b.close for b in bars], self.n)
        return self

    def decide(self, i: int, pos: dict | None) -> str | None:
        r = self._rsi[i]
        if r is None:
            return None
        if pos is None and r < self.low:
            return 'buy'
        if pos is not None and r > self.high:
            return 'sell'
        return None
