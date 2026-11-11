"""`trend` 波段前瞻紀錄（DESIGN.md 6.28，2026-11-12 營運者破例同意）。**只記錄、不下單**；規則與標準看過結果後不改。

規則＝第三十一輪 `trend`（6.3、6.27）：
- 每月 1 日，USDT 永續（不含 TradFi、穩定幣、指數）用前 30 天成交金額取前 30 名，上市滿 90 天；名單存檔後不重算。
- 每天 UTC 0 點（T）：週線（往回每 7 天一個收盤、20 點平均）和日線 50 日均線同向；日收盤是最近 20 天最高 → 做多、最低 → 做空。
- T 那根 15 分 K 開盤進場；停損 2 × ATR(14)（日 K 真實波幅），用 15 分 K 高低價檢查；沒停損就 T＋10 天開盤出場。
- 同時最多 10 筆、每筆 10% 資金、1 倍；同一天照「30 天標準化報酬」絕對值挑；同幣出場前不重複開。
- 成本吃單每邊 0.05%＋持倉期間實際結算的資金費率。
資料全是公開的，每週一從起點重算一次，只把新完成的交易補記到 jsonl（只新增、不修改）。
研究統計用 float（不碰交易路徑）。
"""
from __future__ import annotations

import bisect
import json
import math
import random
import statistics as st
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

D = 86_400_000
BAR = 900_000
START_MS = int(datetime(2026, 11, 13, tzinfo=timezone.utc).timestamp() * 1000)   # commit 之後的第一個 UTC 0 點
TOP, AGE = 30, 90
HOLD, ATR_N, STOP_K = 10, 14, 2.0
COST, SLOTS, SIZE = 0.0005, 10, 0.1
CHECK_N, FINAL_N, FINAL_T, MAX_DD = 100, 300, 2.0, -0.30
RUNS, SEED = 1000, 100


def month_key(t: int) -> str:
    return datetime.fromtimestamp(t / 1000, timezone.utc).strftime('%Y-%m')


def month_start(key: str) -> int:
    y, m = map(int, key.split('-'))
    return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)


@dataclass
class Daily:
    """幣安 1d K 線：開盤時間、開、高、低、收、成交金額。"""
    t: list[int]
    o: list[float]
    h: list[float]
    l: list[float]  # noqa: E741
    c: list[float]
    qv: list[float]

    @classmethod
    def from_klines(cls, rows: list[list]) -> 'Daily':
        rows = [r for r in rows if float(r[4]) > 0]
        return cls([int(r[0]) for r in rows], *[[float(r[i]) for r in rows] for i in (1, 2, 3, 4, 7)])

    def __post_init__(self):
        self.pos = {t: i for i, t in enumerate(self.t)}


@dataclass
class K15:
    t: list[int]
    o: list[float]
    h: list[float]
    l: list[float]  # noqa: E741
    c: list[float]

    @classmethod
    def from_klines(cls, rows: list[list]) -> 'K15':
        return cls([int(r[0]) for r in rows], *[[float(r[i]) for r in rows] for i in (1, 2, 3, 4)])


def universe(key: str, daily: dict[str, Daily]) -> list[str]:
    """這個月的名單：月初前一天有 K 線、第一根 K 線早於月初 90 天，前 30 天成交金額前 30 名。"""
    t = month_start(key)
    vols = []
    for sym, d in daily.items():
        i = d.pos.get(t - D)
        if i is None or not d.t or d.t[0] > t - AGE * D:
            continue
        a = bisect.bisect_left(d.t, t - 30 * D)
        vols.append((-sum(d.qv[a:i + 1]), sym))
    return [s for _, s in sorted(vols)[:TOP]]


def signal(d: Daily, T: int) -> tuple[int, float, float] | None:
    """T（UTC 0 點）的訊號：(方向, 強度, ATR)；沒有訊號回 None。"""
    k = d.pos.get(T - D)
    if k is None or k < 133:
        return None
    c = d.c
    w_ma = c[k] / (sum(c[k - 7 * j] for j in range(20)) / 20) - 1
    d50 = c[k] / (sum(c[k - 49:k + 1]) / 50) - 1
    gate = 1 if w_ma > 0 and d50 > 0 else -1 if w_ma < 0 and d50 < 0 else 0
    last20 = c[k - 19:k + 1]
    side = 1 if gate > 0 and c[k] >= max(last20) else -1 if gate < 0 and c[k] <= min(last20) else 0
    if not side or d.t[k] - d.t[k - ATR_N] != ATR_N * D:
        return None
    tr = [max(d.h[j] - d.l[j], abs(d.h[j] - c[j - 1]), abs(d.l[j] - c[j - 1])) for j in range(k - ATR_N + 1, k + 1)]
    r = [c[j] / c[j - 1] - 1 for j in range(k - 19, k + 1)]
    sd = st.stdev(r)
    z30 = (c[k] / c[k - 30] - 1) / (sd * math.sqrt(30)) if sd else 0.0
    return side, abs(z30), sum(tr) / ATR_N


def exit_path(b: K15, side: int, stop: float, T: int) -> tuple[float, int, float, bool] | None:
    """從 T 那根進場：(出場價, 出場時間, 途中最大反向幅度, 是否停損)；還沒結束回 None。同第三十一輪。"""
    e = b.o[0]
    end = T + HOLD * D
    worst = 0.0
    for j in range(len(b.t)):
        if b.t[j] >= end:
            return b.o[j], b.t[j], worst, False
        if side > 0:
            if j > 0 and b.o[j] <= stop:
                return b.o[j], b.t[j], max(worst, 1 - b.o[j] / e), True
            worst = max(worst, 1 - b.l[j] / e)
            if b.l[j] <= stop:
                return stop, b.t[j], worst, True
        else:
            if j > 0 and b.o[j] >= stop:
                return b.o[j], b.t[j], max(worst, b.o[j] / e - 1), True
            worst = max(worst, b.h[j] / e - 1)
            if b.h[j] >= stop:
                return stop, b.t[j], worst, True
    return None


def net_of(side: int, gross: float, fund: float) -> float:
    return side * gross - 2 * COST - side * fund


def simulate(now: int, uni: dict[str, list[str]], daily: dict[str, Daily], get15: Callable[[str, int], K15 | None],
             funding: Callable[[str], tuple[list[int], list[float]]], start: int = START_MS) -> tuple[list[dict], list[dict]]:
    """從起點逐日重算到 now：回傳（已完成的交易, 還在抱的單）。get15(幣, T) 回傳 T 起的 15 分 K（第一根要是 T）。"""
    done, holding = [], []           # holding：還沒出場的單（出場時間未知）
    busy: dict[str, int] = {}         # 幣 → 出場時間（已知）；還在抱的用很大的數
    T = start
    while T + BAR <= now:
        busy = {s: x for s, x in busy.items() if x > T}
        names = uni.get(month_key(T), [])
        cands = []
        for sym in names:
            if sym in busy or sym not in daily:
                continue
            sig = signal(daily[sym], T)
            if sig:
                cands.append((-sig[1], sym, sig))
        for _, sym, (side, strength, atr) in sorted(cands):
            if len(busy) >= SLOTS:
                break
            b = get15(sym, T)
            if b is None or not b.t or b.t[0] != T:
                continue
            k = bisect.bisect_right(b.t, now - BAR)                 # 只用 now 以前收完的 15 分 K
            b = K15(b.t[:k], b.o[:k], b.h[:k], b.l[:k], b.c[:k])
            e = b.o[0]
            path = exit_path(b, side, e - side * STOP_K * atr, T)
            if path is None and now >= T + (HOLD + 2) * D:          # 下架或資料中斷：用最後收盤出場（同第三十一輪）
                worst = max((1 - x / e if side > 0 else x / e - 1) for x in (b.l if side > 0 else b.h))
                path = (b.c[-1], b.t[-1] + BAR, worst, False)
            if path is None:
                busy[sym] = 2 ** 62
                holding.append({'t': T, 'sym': sym, 'side': side, 'entry': e, 'stop': e - side * STOP_K * atr})
                continue
            px, X, mae, stopped = path
            ft, fr = funding(sym)
            fund = sum(fr[bisect.bisect_right(ft, T):bisect.bisect_right(ft, X)])
            gross = px / e - 1
            alt = exit_path(b, -side, e + side * STOP_K * atr, T)
            alt_net = None
            if alt:
                afund = sum(fr[bisect.bisect_right(ft, T):bisect.bisect_right(ft, alt[1])])
                alt_net = net_of(-side, alt[0] / e - 1, afund)
            busy[sym] = X
            done.append({'t': T, 'sym': sym, 'side': side, 'entry': e, 'stop': e - side * STOP_K * atr, 'exit_t': X, 'exit_px': px,
                         'stopped': stopped, 'gross': gross, 'fund': fund, 'net': net_of(side, gross, fund), 'alt': alt_net,
                         'mae': mae, 'days': (X - T) / D})
        T += D
    return done, holding


def read_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]


def update(path: Path, done: list[dict]) -> tuple[list[dict], int]:
    """只補記還沒記過的完成交易（用進場時間＋幣當鍵），不改舊紀錄。"""
    logged = read_log(path)
    seen = {(r['t'], r['sym']) for r in logged}
    new = sorted((r for r in done if (r['t'], r['sym']) not in seen), key=lambda r: (r['exit_t'], r['sym']))
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as f:
            for r in new:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
    return logged + new, len(new)


def _t_daily(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    a = min(r['exit_t'] for r in rows) // D
    b = max(r['exit_t'] for r in rows) // D
    days = [0.0] * (b - a + 1)
    for r in rows:
        days[r['exit_t'] // D - a] += SIZE * r['net']
    if len(days) < 2 or st.stdev(days) == 0:
        return 0.0
    return st.fmean(days) / (st.stdev(days) / math.sqrt(len(days)))


def _dd(rows: list[dict]) -> float:
    eq, peak, worst = 1.0, 1.0, 0.0
    for r in sorted(rows, key=lambda r: (r['exit_t'], r['sym'])):
        eq *= 1 + SIZE * r['net']
        peak = max(peak, eq)
        worst = min(worst, eq / peak - 1)
    return worst


def _rand_p90(rows: list[dict]) -> float | None:
    pairs = [(r['net'], r['alt']) for r in rows if r.get('alt') is not None]
    if not pairs:
        return None
    rng = random.Random(SEED)
    means = sorted(sum(a if rng.random() < 0.5 else b for a, b in pairs) / len(pairs) for _ in range(RUNS))
    return means[int(0.9 * RUNS)]


def status(rows: list[dict]) -> dict:
    rows = sorted(rows, key=lambda r: (r['exit_t'], r['sym']))
    nets = [r['net'] for r in rows]
    n = len(nets)
    out = {'n': n, 'mean': st.fmean(nets) if nets else 0.0, 'dd': _dd(rows), 't': _t_daily(rows),
           'equity': math.prod(1 + SIZE * x for x in nets) if nets else 1.0, 'stopped': None, 'final_pass': None}
    if n >= CHECK_N:
        first = rows[:CHECK_N]
        out['stopped'] = st.fmean(r['net'] for r in first) <= 0 or _dd(first) < MAX_DD
    if n >= FINAL_N:
        last = rows[:FINAL_N]
        half = FINAL_N // 2
        mean = st.fmean(r['net'] for r in last)
        p90 = _rand_p90(last)
        out['final'] = {'每筆>0': mean > 0, 't≥2': _t_daily(last) >= FINAL_T,
                        '贏隨機P90': p90 is not None and mean > p90,
                        '前後半>0': st.fmean(r['net'] for r in last[:half]) > 0 and st.fmean(r['net'] for r in last[half:]) > 0,
                        '回撤': _dd(last) >= MAX_DD}
        out['final_pass'] = all(out['final'].values())
    return out


def weekly_returns(rows: list[dict]) -> dict[int, float]:
    """週一 0 點（UTC）為一週，依出場時間加總 10% × 淨報酬。"""
    monday0 = int(datetime(2026, 11, 9, tzinfo=timezone.utc).timestamp() * 1000)
    out: dict[int, float] = {}
    for r in rows:
        wk = monday0 + (r['exit_t'] - monday0) // (7 * D) * 7 * D
        out[wk] = out.get(wk, 0.0) + SIZE * r['net']
    return out


def blend_weekly(equity: list[tuple[int, float]]) -> dict[int, float]:
    """blend 前瞻帳戶權益（毫秒, 權益）→ 每週（週一 0 點 UTC 起）報酬。"""
    monday0 = int(datetime(2026, 11, 9, tzinfo=timezone.utc).timestamp() * 1000)
    last: dict[int, float] = {}
    for t, e in equity:
        last[monday0 + (t - monday0) // (7 * D) * 7 * D] = e
    weeks = sorted(last)
    return {w: last[w] / last[p] - 1 for p, w in zip(weeks, weeks[1:]) if last[p] > 0}


def correlation(a: dict[int, float], b: dict[int, float]) -> tuple[int, float | None]:
    ws = sorted(set(b))
    xs, ys = [a.get(w, 0.0) for w in ws], [b[w] for w in ws]
    if len(ws) < 8 or st.pstdev(xs) == 0 or st.pstdev(ys) == 0:
        return len(ws), None
    return len(ws), st.correlation(xs, ys)


def text(s: dict, added: int, holding: int, corr: tuple[int, float | None]) -> str:
    head = (f"trend 波段前瞻紀錄（DESIGN 6.28，只記錄不下單）：完成 {s['n']} 筆（本週新增 {added}），還在抱 {holding} 筆；"
            f"帳戶 {s['equity']:.3f} 倍、每筆平均 {s['mean'] * 100:+.2f}%、t {s['t']:.2f}、回撤 {s['dd'] * 100:.1f}%")
    lines = [head]
    if s['final_pass'] is not None:
        bad = [k for k, v in s['final'].items() if not v]
        lines.append(f"最後判定（前 {FINAL_N} 筆）：{'通過，可以討論測試網' if s['final_pass'] else '沒通過（' + '、'.join(bad) + '），停止'}")
    elif s['stopped'] is not None:
        lines.append(f"中途檢查（前 {CHECK_N} 筆）：{'停止條件成立，停止紀錄' if s['stopped'] else '沒觸發停止，繼續記錄'}；距最後判定還差 {FINAL_N - s['n']} 筆")
    else:
        lines.append(f"距中途檢查還差 {CHECK_N - s['n']} 筆（最後判定 {FINAL_N} 筆）")
    weeks, c = corr
    lines.append(f"和 blend 前瞻的每週相關係數：{'週數不足（' + str(weeks) + ' 週，要 8 週）' if c is None else f'{c:+.2f}（{weeks} 週）'}")
    return '\n'.join(lines)
