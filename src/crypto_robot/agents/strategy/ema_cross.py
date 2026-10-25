"""EMA 交叉＋量能進場（DESIGN.md 3.4）。純函式：研究回測與即時交易呼叫同一份。

輸入必須是已收盤、已通過品質檢查的連續 K 線。回傳值和 bars 逐根對齊。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from crypto_robot.core.models import Bar


@dataclass(frozen=True)
class StrategyParams:
    ema_fast: int = 20
    ema_slow: int = 50
    volume_lookback: int = 20
    use_volume_filter: bool = True
    warmup_bars: int = 150      # 至少 3 × ema_slow，EMA 起始值的影響才夠小

    def __post_init__(self):
        if not 0 < self.ema_fast < self.ema_slow:
            raise ValueError('ema_fast 必須小於 ema_slow')
        if self.warmup_bars < max(self.ema_slow, self.volume_lookback):
            raise ValueError('warmup_bars 太短')


def ema(values: list[Decimal], n: int) -> list[Decimal]:
    """以第一個值為起點的指數移動平均，alpha = 2 / (n + 1)。"""
    if not values:
        return []
    alpha = Decimal(2) / Decimal(n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(out[-1] + alpha * (v - out[-1]))
    return out


def crosses(bars: list[Bar], p: StrategyParams) -> tuple[list[bool], list[bool]]:
    """(上穿, 下穿)：當根 EMA 快線相對慢線的位置和前一根不同。暖機期內一律 False。"""
    closes = [b.close for b in bars]
    fast, slow = ema(closes, p.ema_fast), ema(closes, p.ema_slow)
    up = [False] * len(bars)
    down = [False] * len(bars)
    for i in range(max(1, p.warmup_bars), len(bars)):
        up[i] = fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]
        down[i] = fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]
    return up, down


def volume_ok(bars: list[Bar], i: int, lookback: int) -> bool:
    """當根成交量 > 前 lookback 根平均（不含當根）。成交量 0 一定不成立（D4）。"""
    if i < lookback:
        return False
    prev = bars[i - lookback:i]
    return bars[i].volume * lookback > sum(b.volume for b in prev)


def entry_signals(bars: list[Bar], p: StrategyParams) -> list[bool]:
    up, _ = crosses(bars, p)
    if not p.use_volume_filter:
        return [u and bars[i].volume > 0 for i, u in enumerate(up)]
    return [u and volume_ok(bars, i, p.volume_lookback) for i, u in enumerate(up)]
