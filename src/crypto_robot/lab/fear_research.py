"""第八輪研究（恐懼與貪婪指數＋RSI）：事件研究，BTC、ETH 分開算。

- 第 i 天收盤：當天的恐懼與貪婪指數（UTC 00:00 公布）< 25，交叉確認組另外要 RSI(14) < 30。
- 隔天開盤買、持有 H 天後開盤賣；持有期間不接新信號；進出場那幾天的日 K 必須連續。
- 信號日與賣出日都要在區間內。基準：同期間任意一天進場持有 H 天（同樣扣成本）。
報告統計用 float（只做報告，不碰交易路徑）。
"""
from __future__ import annotations

import random
import statistics
from decimal import Decimal

from crypto_robot.core.models import Bar
from crypto_robot.lab.breakout_research import contiguous
from crypto_robot.lab.fear_variants import (
    DAY_MS, FEAR_MAX, RANDOM_RUNS, RSI_MAX, RSI_PERIOD, SLIPPAGE, STRESS_SLIPPAGE, TAKER_FEE, FearVariant, passes_dev,
)
from crypto_robot.lab.metrics import t_stat

ONE = Decimal(1)


def parse_fng(payload: dict) -> dict[int, int]:
    """alternative.me 的 /fng/?limit=0 回應 → {UTC 日開始毫秒: 指數}。"""
    return {int(x['timestamp']) // 86_400 * DAY_MS: int(x['value']) for x in payload['data']}


def rsi(closes: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    """Wilder RSI；前 period 根沒有數值。"""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag, al = sum(gains[:period]) / period, sum(losses[:period]) / period
    for i in range(period, len(closes)):
        if i > period:
            ag = (ag * (period - 1) + gains[i - 1]) / period
            al = (al * (period - 1) + losses[i - 1]) / period
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def net_return(entry: Bar, exit_: Bar, slippage: Decimal) -> float:
    return float(exit_.open * (ONE - slippage) * (ONE - TAKER_FEE) / (entry.open * (ONE + slippage) * (ONE + TAKER_FEE))) - 1


def windows(bars: list[Bar], hold: int, start_ms: int, end_ms: int) -> list[int]:
    """可以當進場的信號日 i（隔天到 H 天後都有資料、都在區間內）。"""
    return [i for i in range(len(bars)) if start_ms <= bars[i].ts < end_ms and contiguous(bars, i, i + 1 + hold)
            and bars[i + 1 + hold].ts < end_ms]


def find_signals(bars: list[Bar], fng: dict[int, int], v: FearVariant, start_ms: int, end_ms: int) -> list[int]:
    r = rsi([float(b.close) for b in bars])
    ok = set(windows(bars, v.hold_days, start_ms, end_ms))
    out, i = [], 0
    while i < len(bars):
        f = fng.get(bars[i].ts)
        if i in ok and f is not None and f < FEAR_MAX and (not v.use_rsi or (r[i] is not None and r[i] < RSI_MAX)):
            out.append(i)
            i += 1 + v.hold_days            # 持有期間不接新信號
            continue
        i += 1
    return out


def evaluate_asset(bars: list[Bar], fng: dict[int, int], v: FearVariant, start_ms: int, end_ms: int, mid_ms: int,
                   runs: int = RANDOM_RUNS, seed: int = 7) -> dict:
    h = v.hold_days
    pool = windows(bars, h, start_ms, end_ms)
    sig = find_signals(bars, fng, v, start_ms, end_ms)
    ret = lambda i, s=SLIPPAGE: net_return(bars[i + 1], bars[i + 1 + h], s)
    mean = lambda xs: statistics.fmean(xs) if xs else 0.0
    base, base_s = mean([ret(i) for i in pool]), mean([ret(i, STRESS_SLIPPAGE) for i in pool])
    rets = [ret(i) for i in sig]
    excess = [x - base for x in rets]
    rng = random.Random(seed)
    real = mean(rets)
    beat = sum(real > mean([ret(rng.choice(pool)) for _ in sig]) for _ in range(runs)) / runs if sig else 0.0
    halves = [mean([x for i, x in zip(sig, excess) if bars[i].ts < mid_ms]),
              mean([x for i, x in zip(sig, excess) if bars[i].ts >= mid_ms])]
    stress = mean([ret(i, STRESS_SLIPPAGE) - base_s for i in sig])
    ok, fails = passes_dev(len(sig), beat, t_stat(excess), halves, stress)
    return {'n': len(sig), 'mean_ret': real, 'base': base, 'mean_excess': mean(excess), 't': t_stat(excess),
            'random_pct': beat, 'halves': halves, 'stress_excess': stress, 'passed': ok, 'fails': fails,
            'dates': [bars[i].ts for i in sig]}


def evaluate(data: dict[str, list[Bar]], fng: dict[int, int], v: FearVariant, start_ms: int, end_ms: int, mid_ms: int) -> dict:
    per = {a: evaluate_asset(bars, fng, v, start_ms, end_ms, mid_ms) for a, bars in data.items()}
    return {'variant': v.name, 'assets': per, 'passed': all(r['passed'] for r in per.values()),
            'min_t': min(r['t'] for r in per.values())}


def dev_report(rows: list[dict], generated: str) -> str:
    from crypto_robot.lab.common import day, pct
    from crypto_robot.lab.fear_variants import DEV_END, DEV_MID, DEV_START, MIN_EVENTS, MIN_T, RANDOM_PCT
    lines = [
        f'# 研究期報告（第八輪，恐懼與貪婪指數＋RSI，BTC／ETH）（{generated}）', '',
        f'- 研究期 {day(DEV_START)} ～ {day(DEV_END - 1)}，前半／後半以 {day(DEV_MID)} 切開；MAX 台幣市場日 K（UTC 日界）。',
        f'- 信號：當天恐懼與貪婪指數 < {FEAR_MAX}（`-rsi30` 另外要 RSI({RSI_PERIOD}) < {RSI_MAX}）；隔天開盤買、持有 H 天後開盤賣，持有期間不接新信號。',
        f'- 超額報酬＝信號報酬 − 同期間任意一天進場持有 H 天的平均報酬（都扣 taker {TAKER_FEE:%}＋滑價 {SLIPPAGE:%}）。',
        f'- 通過標準在看結果前訂定（`src/crypto_robot/lab/fear_variants.py`），BTC、ETH 各自五項全過才算：'
        f'信號 ≥ {MIN_EVENTS} 次；贏過 {RANDOM_PCT:.0%} 的隨機進場；t ≥ {MIN_T:g}；前後半期都 > 0；滑價加倍仍 > 0。',
        '- **已知限制**：信號很少；BTC 與 ETH 常同一天出信號，不是獨立的；ETH 研究期價格已看過六次。', '',
        '| 規則 | 幣 | 通過 | 信號 | 平均報酬 | 任意進場 | 平均超額 | t | 贏過隨機 | 前半超額 | 後半超額 | 滑價×2 超額 | 未通過原因 |',
        '|---|---|---|---|---|---|---|---|---|---|---|---|---|',
    ]
    for row in rows:
        for a, r in row['assets'].items():
            h = r['halves']
            lines.append(
                f'| {row["variant"]} | {a.upper()} | {"✅" if r["passed"] else "—"} | {r["n"]} | {pct(r["mean_ret"])} | {pct(r["base"])} '
                f'| {pct(r["mean_excess"])} | {r["t"]:.2f} | {r["random_pct"]:.0%} | {pct(h[0])} | {pct(h[1])} | {pct(r["stress_excess"])} '
                f'| {"；".join(r["fails"])} |')
    lines += ['', '## 信號日期', '']
    for row in rows:
        for a, r in row['assets'].items():
            lines.append(f'- {row["variant"]} {a.upper()}：' + '、'.join(day(ts) for ts in r['dates']))
    passed = [row['variant'] for row in rows if row['passed']]
    lines += ['', '## 結論', '',
              (f'通過：{", ".join("`" + p + "`" for p in passed)}。考卷期只考其中 t（BTC、ETH 較小者）最高的一組，只考一次。'
               if passed else '**沒有任何一組通過。** 不放寬標準，也不進考卷期。'),
              '', '歷史統計，過去有效不代表未來有效。']
    return '\n'.join(lines) + '\n'
