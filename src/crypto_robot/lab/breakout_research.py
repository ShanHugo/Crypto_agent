"""第七輪研究（山寨幣橫盤後帶量突破）：事件研究，每次信號單獨計算，不模擬整個帳戶。

- 每天收盤檢查每個幣（breakout_variants 的條件）；有信號就隔天開盤買、持有 H 天後開盤賣。
- 同一個幣持有期間不接新信號；進出場那幾天的日 K 必須連續，缺資料就不算。
- 信號日在區間內、而且賣出日也在區間內才算（研究期的信號不會用到考卷期的價格）。
- 比較：同期間持有 ETH（不扣成本）；隨機進場：同一個幣、同樣次數，在符合流動性條件的日子隨機挑。
報告統計用 float（只做報告，不碰交易路徑）。
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass
from decimal import Decimal

from crypto_robot.core.models import Bar
from crypto_robot.lab.breakout_variants import (
    BENCH, DAY_MS, DEV_END, DEV_MID, DEV_START, LIQ_DAYS, MAX_RANGE, MIN_LIQ, RANDOM_RUNS, SLIPPAGE,
    STRESS_SLIPPAGE, TAKER_FEE, VOL_MULT, BreakoutVariant, passes_dev,
)
from crypto_robot.lab.metrics import t_stat

ONE = Decimal(1)


@dataclass(frozen=True)
class Event:
    coin: str
    signal_ts: int
    entry: Bar           # 隔天的日 K（開盤買）
    exit: Bar            # H 天後的日 K（開盤賣）


def turnover(b: Bar) -> Decimal:
    return b.volume * b.close


def contiguous(bars: list[Bar], a: int, b: int) -> bool:
    """bars[a..b]（含）每天都有。"""
    return a >= 0 and b < len(bars) and bars[b].ts - bars[a].ts == (b - a) * DAY_MS


def liquid(bars: list[Bar], i: int) -> bool:
    """第 i 天之前 LIQ_DAYS 天每日成交額中位數 ≥ MIN_LIQ。"""
    return i >= LIQ_DAYS and statistics.median(turnover(b) for b in bars[i - LIQ_DAYS:i]) >= MIN_LIQ


def is_signal(bars: list[Bar], i: int, n: int) -> bool:
    if i < max(n, LIQ_DAYS) or not contiguous(bars, i - max(n, LIQ_DAYS), i) or not liquid(bars, i):
        return False
    hi = max(b.high for b in bars[i - n:i])
    lo = min(b.low for b in bars[i - n:i])
    med = statistics.median(turnover(b) for b in bars[i - LIQ_DAYS:i])
    return lo > 0 and hi / lo <= MAX_RANGE and bars[i].close > hi and turnover(bars[i]) >= VOL_MULT * med


def find_events(coin: str, bars: list[Bar], v: BreakoutVariant, start_ms: int, end_ms: int) -> list[Event]:
    out, i = [], 0
    while i < len(bars):
        ts = bars[i].ts
        if start_ms <= ts < end_ms and is_signal(bars, i, v.base_days):
            j, k = i + 1, i + 1 + v.hold_days
            if contiguous(bars, i, k) and bars[k].ts < end_ms:
                out.append(Event(coin, ts, bars[j], bars[k]))
                i = k               # 持有期間不接新信號
                continue
        i += 1
    return out


def net_return(entry: Bar, exit_: Bar, slippage: Decimal) -> float:
    buy = entry.open * (ONE + slippage) * (ONE + TAKER_FEE)
    sell = exit_.open * (ONE - slippage) * (ONE - TAKER_FEE)
    return float(sell / buy) - 1


def bench_return(eth: dict[int, Bar], e: Event) -> float | None:
    a, b = eth.get(e.entry.ts), eth.get(e.exit.ts)
    return float(b.open / a.open) - 1 if a and b else None


def random_pct(events: list[Event], data: dict[str, list[Bar]], hold: int, start_ms: int, end_ms: int,
               runs: int = RANDOM_RUNS, seed: int = 7) -> float:
    """信號平均報酬贏過幾成的隨機進場（同幣、同次數、符合流動性的日子）。"""
    if not events:
        return 0.0
    pools: dict[str, list[tuple[Bar, Bar]]] = {}
    for c in {e.coin for e in events}:
        bars = data[c]
        pools[c] = [(bars[i + 1], bars[i + 1 + hold]) for i in range(len(bars))
                    if start_ms <= bars[i].ts < end_ms and liquid(bars, i) and contiguous(bars, i, i + 1 + hold)
                    and bars[i + 1 + hold].ts < end_ms]
    real = statistics.fmean(net_return(e.entry, e.exit, SLIPPAGE) for e in events)
    rng = random.Random(seed)
    beat = 0
    for _ in range(runs):
        sample = [net_return(*rng.choice(pools[e.coin]), SLIPPAGE) for e in events]
        beat += real > statistics.fmean(sample)
    return beat / runs


def evaluate(data: dict[str, list[Bar]], v: BreakoutVariant, start_ms: int, end_ms: int, mid_ms: int) -> dict:
    eth = {b.ts: b for b in data[BENCH]}
    events = []
    for c, bars in data.items():
        if c != BENCH:
            events += find_events(c, bars, v, start_ms, end_ms)
    events = [e for e in events if bench_return(eth, e) is not None]
    events.sort(key=lambda e: e.signal_ts)
    rows = [(e, net_return(e.entry, e.exit, SLIPPAGE), bench_return(eth, e),
             net_return(e.entry, e.exit, STRESS_SLIPPAGE)) for e in events]
    excess = [r - b for _, r, b, _ in rows]
    mean = lambda xs: statistics.fmean(xs) if xs else 0.0
    by_coin: dict[str, list[float]] = {}
    for (e, *_), x in zip(rows, excess):
        by_coin.setdefault(e.coin, []).append(x)
    best = max(by_coin, key=lambda c: sum(by_coin[c])) if by_coin else None
    drop = [x for (e, *_), x in zip(rows, excess) if e.coin != best]
    halves = [mean([x for (e, *_), x in zip(rows, excess) if e.signal_ts < mid_ms]),
              mean([x for (e, *_), x in zip(rows, excess) if e.signal_ts >= mid_ms])]
    r = {
        'variant': v.name, 'n': len(rows), 'coins': len(by_coin),
        'mean_ret': mean([r for _, r, _, _ in rows]), 'mean_eth': mean([b for _, _, b, _ in rows]),
        'mean_excess': mean(excess), 't': t_stat(excess), 'halves': halves,
        'random_pct': random_pct(events, data, v.hold_days, start_ms, end_ms),
        'stress_excess': mean([s - b for _, _, b, s in rows]),
        'best_coin': best, 'drop_best_excess': mean(drop),
        'by_coin': {c: (len(xs), mean(xs)) for c, xs in sorted(by_coin.items())},
        'events': [(e.coin, e.signal_ts, r, b) for e, r, b, _ in rows],
    }
    r['passed'], r['fails'] = passes_dev(r['n'], r['coins'], r['t'], halves, r['random_pct'],
                                         r['stress_excess'], r['drop_best_excess'])
    return r


def run_dev(data: dict[str, list[Bar]], variants: list[BreakoutVariant]) -> list[dict]:
    return [evaluate(data, v, DEV_START, DEV_END, DEV_MID) for v in variants]


def dev_report(rows: list[dict], generated: str) -> str:
    from crypto_robot.lab.breakout_variants import MIN_COINS, MIN_EVENTS, MIN_T, RANDOM_PCT
    from crypto_robot.lab.common import day, pct
    lines = [
        f'# 研究期報告（第七輪，山寨幣橫盤後帶量突破）（{generated}）', '',
        f'- 研究期 {day(DEV_START)} ～ {day(DEV_END - 1)}，前半／後半以 {day(DEV_MID)} 切開；MAX 台幣市場日 K（UTC 日界），ETH 是比較對象。',
        f'- 信號：前 30 天每日成交額中位數 ≥ {MIN_LIQ:,.0f} 元；前 N 天最高 ÷ 最低 ≤ {MAX_RANGE}；收盤突破前 N 天最高；'
        f'當天成交額 ≥ 中位數 × {VOL_MULT}。隔天開盤買、持有 H 天後開盤賣，同一個幣持有期間不接新信號。',
        f'- 成本：taker {TAKER_FEE:%}＋滑價每邊 {SLIPPAGE:%}；比較對象「同期間持有 ETH」不扣成本。',
        f'- 通過標準在看結果前訂定（`src/crypto_robot/lab/breakout_variants.py`），六項全過才算：信號 ≥ {MIN_EVENTS} 次且 ≥ {MIN_COINS} 個幣；'
        f'超額報酬 t ≥ {MIN_T:g}；前後半期都 > 0；贏過 {RANDOM_PCT:.0%} 的隨機進場；滑價加倍仍 > 0；拿掉最好的幣仍 > 0。',
        '- **已知限制**：只有現在還在 MAX 上的幣（下架的不在，結果偏樂觀）；成本用 2026-10-30 的價差估計。', '',
        '| 規則 | 通過 | 信號 | 幣數 | 平均報酬 | 同期 ETH | 平均超額 | t | 前半超額 | 後半超額 | 贏過隨機 | 滑價×2 超額 | 拿掉最好的幣 | 未通過原因 |',
        '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|',
    ]
    for r in rows:
        h = r['halves']
        lines.append(
            f'| {r["variant"]} | {"✅" if r["passed"] else "—"} | {r["n"]} | {r["coins"]} | {pct(r["mean_ret"])} | {pct(r["mean_eth"])} '
            f'| {pct(r["mean_excess"])} | {r["t"]:.2f} | {pct(h[0])} | {pct(h[1])} | {r["random_pct"]:.0%} | {pct(r["stress_excess"])} '
            f'| {pct(r["drop_best_excess"])}（{r["best_coin"] or "—"}） | {"；".join(r["fails"])} |')
    lines += ['', '## 各幣信號次數與平均超額報酬', '']
    for r in rows:
        lines.append(f'- {r["variant"]}：' + '、'.join(f'{c.upper()} {n} 次 {pct(x)}' for c, (n, x) in r['by_coin'].items()))
    passed = [r for r in rows if r['passed']]
    lines += ['', '## 結論', '',
              (f'通過：{", ".join("`" + r["variant"] + "`" for r in passed)}。考卷期只考其中 t 最高的一組，只考一次。'
               if passed else '**沒有任何一組通過。** 不放寬標準，也不進考卷期。'),
              '', '歷史統計，過去有效不代表未來有效。']
    return '\n'.join(lines) + '\n'
