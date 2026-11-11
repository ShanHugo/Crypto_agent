"""第三十一輪：波段，抱 1～10 天，週線 → 日線 → 4 小時（DESIGN.md 6.27、6.3，2026-11-12 營運者同意）。**看過結果後不改**。

主環境的部分（純 Python，不需要 numpy）：
- 每幣每天 UTC 0 點一列：25 項特徵、答案、方向閘門、`trend` 訊號、多空兩種情境的出場結果（2 × ATR 停損或 10 天），
  寫成 CSV 給 scripts/swing_train.py（_vendor/ml_venv）；
- 讀訊號，模擬最多 10 筆同時持有，照事先訂的標準判定。
研究統計用 float（不碰交易路徑）。
"""
from __future__ import annotations

import bisect
import csv
import math
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from crypto_robot.lab.hourly import ms
from crypto_robot.lab.metrics import max_drawdown, t_stat

D = 86_400_000
BAR = 900_000
HOLD, ATR_N, STOP_K = 10, 14, 2.0
COST, SLIP = 0.0005, 0.0005
SLOTS, SIZE = 10, 0.1
DEV = (ms(2021, 1, 1), ms(2025, 1, 1))
DEV_MID = ms(2023, 1, 1)
EXAM = (ms(2026, 1, 1), ms(2026, 10, 22))
EXAM_MID = ms(2026, 6, 1)
MIN_DEV, MIN_EXAM, MIN_T, EXAM_T, MAX_DD = 300, 100, 3.0, 2.0, -0.30
RUNS, SEED = 1000, 100
LIQ = {'2x': 0.49, '3x': 1 / 3 - 0.01, '5x': 0.19, '10x': 0.09}
GROUPS = ['trend', 'swing_gbm', 'swing_gate']
CONTROL = 'swing_gate_shuf'
FEATURES = ['d_z1', 'd_z3', 'd_z7', 'd_z14', 'd_z30', 'd_z60', 'd_ma20', 'd_ma50', 'd_ma100', 'd_vol20', 'd_bb', 'd_qv', 'd_taker7',
            'fund3', 'fund14', 'btc_z7', 'btc_z30', 'w_ma20', 'r28', 'atr', 'd_sar_dir', 'd_sar_dist', 'h4_bb', 'h4_sar_dir', 'h4_ma20']
SIDES = ['gross', 'fund', 'mae', 'days', 'stop']
COLUMNS = ['t', 'sym', *FEATURES, 'target', 'gate', 'trend', 'trend_s',
           *[f'{k}_l' for k in SIDES], *[f'{k}_s' for k in SIDES]]


def month_key(t: int) -> str:
    return datetime.fromtimestamp(t / 1000, timezone.utc).strftime('%Y-%m')


@dataclass
class Bars:
    """一個幣的 15 分 K（開盤時間排序、不重複）。"""
    t: list[int]
    o: list[float]
    h: list[float]
    l: list[float]  # noqa: E741
    c: list[float]

    @classmethod
    def from_rows(cls, rows: list[list]) -> 'Bars':
        rows = sorted({int(r[0]): r for r in rows if float(r[1]) > 0 and float(r[4]) > 0}.values(), key=lambda r: int(r[0]))
        cols = list(zip(*rows)) if rows else [[]] * 5
        return cls([int(x) for x in cols[0]], *[[float(x) for x in col] for col in cols[1:5]])


class Frame:
    """合併 K（開盤時間、收、高、低）。"""

    def __init__(self, t: list[int], c: list[float], h: list[float], lo: list[float], step: int):
        self.t, self.c, self.h, self.l, self.step = t, c, h, lo, step
        self.pos = {x: i for i, x in enumerate(t)}

    @classmethod
    def agg(cls, b: Bars, n: int) -> 'Frame':
        step = n * BAR
        t, c, h, lo = [], [], [], []
        i = 0
        while i < len(b.t):
            s = b.t[i] // step * step
            j = i
            while j < len(b.t) and b.t[j] < s + step:
                j += 1
            if j - i == n:
                t.append(s)
                c.append(b.c[j - 1])
                h.append(max(b.h[i:j]))
                lo.append(min(b.l[i:j]))
            i = j
        return cls(t, c, h, lo, step)

    def last(self, T: int) -> int | None:
        """T 以前最後一根收完的 K（必須剛好是 T 前一根）。"""
        return self.pos.get(T // self.step * self.step - self.step)


def sar(h: list[float], lo: list[float], c: list[float], step: float = 0.02, cap: float = 0.2) -> list[tuple[int, float] | None]:
    """Wilder 拋物線 SAR（同第三十輪）：每根 K 的 (方向, SAR)，第 0、1 根沒有。"""
    out: list[tuple[int, float] | None] = [None] * len(c)
    if len(c) < 3:
        return out
    up = c[1] > c[0]
    s, ep, af = (lo[0], h[1], step) if up else (h[0], lo[1], step)
    for i in range(2, len(c)):
        s = s + af * (ep - s)
        if up:
            s = min(s, lo[i - 1], lo[i - 2])
            if lo[i] < s:
                up, s, ep, af = False, ep, lo[i], step
            elif h[i] > ep:
                ep, af = h[i], min(af + step, cap)
        else:
            s = max(s, h[i - 1], h[i - 2])
            if h[i] > s:
                up, s, ep, af = True, ep, h[i], step
            elif lo[i] < ep:
                ep, af = lo[i], min(af + step, cap)
        out[i] = (1 if up else -1, s)
    return out


def boll_pct(c: list[float], k: int) -> float | None:
    if k < 19:
        return None
    w = c[k - 19:k + 1]
    mid = sum(w) / 20
    sd = math.sqrt(sum((x - mid) ** 2 for x in w) / 20)
    return (c[k] - (mid - 2 * sd)) / (4 * sd) if sd else None


def atr(f: Frame, k: int, n: int = ATR_N) -> float | None:
    """合併日 K 第 k 根為止的 ATR(n)：真實波幅簡單平均；這 n＋1 根要連續。"""
    if k < n or f.t[k] - f.t[k - n] != n * f.step:
        return None
    tr = [max(f.h[j] - f.l[j], abs(f.h[j] - f.c[j - 1]), abs(f.l[j] - f.c[j - 1])) for j in range(k - n + 1, k + 1)]
    return sum(tr) / n


def exit_path(b: Bars, i0: int, side: int, stop: float, T: int) -> tuple[float, int, float, bool]:
    """從第 i0 根（進場那根）開始：回傳 (出場價, 出場時間, 途中最大反向幅度, 是否停損)。"""
    e = b.o[i0]
    end = T + HOLD * D
    worst = 0.0
    j = i0
    while j < len(b.t) and b.t[j] < end:
        if side > 0:
            if b.o[j] <= stop and j > i0:
                return b.o[j], b.t[j], max(worst, 1 - b.o[j] / e), True
            worst = max(worst, 1 - b.l[j] / e)
            if b.l[j] <= stop:
                return stop, b.t[j], worst, True
        else:
            if b.o[j] >= stop and j > i0:
                return b.o[j], b.t[j], max(worst, b.o[j] / e - 1), True
            worst = max(worst, b.h[j] / e - 1)
            if b.h[j] >= stop:
                return stop, b.t[j], worst, True
        j += 1
    if j < len(b.t):
        return b.o[j], b.t[j], worst, False
    return b.c[-1], b.t[-1] + BAR, worst, False


def zret(c: list[float], k: int, n: int, sd: float) -> float | None:
    return (c[k] / c[k - n] - 1) / (sd * math.sqrt(n)) if k >= n and sd else None


def daily_sd(r: list[float | None], k: int) -> float | None:
    xs = [x for x in r[k - 19:k + 1] if x is not None] if k >= 19 else []
    if len(xs) < 20:
        return None
    m = sum(xs) / 20
    v = sum((x - m) ** 2 for x in xs) / 19
    return math.sqrt(v) if v > 0 else None


def rows_for(sym: str, coin, b: Bars, btc, in_uni) -> list[list]:
    """一個幣的每一天（名單月份、T 那根 15 分 K 存在、ATR 算得出來）。coin、btc 是 xsec.Coin（日 K）。"""
    day, h4 = Frame.agg(b, 96), Frame.agg(b, 16)
    dsar, hsar = sar(day.h, day.l, day.c), sar(h4.h, h4.l, h4.c)
    bpos = {t: i for i, t in enumerate(b.t)}
    out = []
    for T in sorted({t // D * D for t in b.t}):
        if not in_uni(month_key(T)) or T not in bpos:
            continue
        k = coin.pos.get(T - D)
        kd = day.last(T)
        if k is None or kd is None:
            continue
        a = atr(day, kd)
        if not a:
            continue
        c, r = coin.c, coin.r
        sd = daily_sd(r, k)
        ma = lambda n: c[k] / (sum(c[k - n + 1:k + 1]) / n) - 1 if k >= n - 1 else None   # noqa: E731
        wk = [c[k - 7 * j] for j in range(20)] if k >= 133 else None
        w_ma = c[k] / (sum(wk) / 20) - 1 if wk else None
        d50 = ma(50)
        gate = 0 if w_ma is None or d50 is None else 1 if w_ma > 0 and d50 > 0 else -1 if w_ma < 0 and d50 < 0 else 0
        last20 = c[k - 19:k + 1] if k >= 19 else None
        trend = (1 if gate > 0 and c[k] >= max(last20) else -1 if gate < 0 and c[k] <= min(last20) else 0) if last20 else 0
        q7, q30 = sum(coin.qv[max(0, k - 6):k + 1]) / 7, sum(coin.qv[max(0, k - 29):k + 1]) / 30
        tq = sum(coin.qv[max(0, k - 6):k + 1])
        f3 = coin.fr[bisect.bisect_right(coin.ft, T - 3 * D):bisect.bisect_right(coin.ft, T)]
        f14 = coin.fr[bisect.bisect_right(coin.ft, T - 14 * D):bisect.bisect_right(coin.ft, T)]
        kb = btc.pos.get(T - D)
        bsd = daily_sd(btc.r, kb) if kb is not None else None
        ds = dsar[kd]
        kh = h4.last(T)
        hs = hsar[kh] if kh is not None else None
        h4ma = h4.c[kh] / (sum(h4.c[kh - 19:kh + 1]) / 20) - 1 if kh is not None and kh >= 19 else None
        feats = [*(zret(c, k, n, sd) for n in (1, 3, 7, 14, 30, 60)), ma(20), d50, ma(100), sd, boll_pct(c, k),
                 q7 / q30 if q30 > 0 else None, sum(coin.tb[max(0, k - 6):k + 1]) / tq if tq > 0 else None,
                 sum(f3) / len(f3) if f3 else None, sum(f14) / len(f14) if f14 else None,
                 zret(btc.c, kb, 7, bsd) if kb is not None else None, zret(btc.c, kb, 30, bsd) if kb is not None else None,
                 w_ma, c[k] / c[k - 28] - 1 if k >= 28 else None, a / c[k],
                 ds[0] if ds else None, day.c[kd] / ds[1] - 1 if ds else None,
                 boll_pct(h4.c, kh) if kh is not None else None, hs[0] if hs else None, h4ma]
        i0 = bpos[T]
        e = b.o[i0]
        t5 = bisect.bisect_left(b.t, T + 5 * D)
        target = (b.o[t5] / e - 1) / (sd * math.sqrt(5)) if t5 < len(b.t) and b.t[t5] == T + 5 * D and sd else None
        sides = []
        for side in (1, -1):
            px, X, mae, stopped = exit_path(b, i0, side, e - side * STOP_K * a, T)
            fund = sum(coin.fr[bisect.bisect_right(coin.ft, T):bisect.bisect_right(coin.ft, X)])
            sides += [px / e - 1, fund, mae, (X - T) / D, int(stopped)]
        z30 = feats[4]
        out.append([T, sym, *feats, target, gate, trend, abs(z30) if trend and z30 is not None else 0.0, *sides])
    return out


def fmt(x) -> str:
    if x is None:
        return ''
    if isinstance(x, float):
        return f'{x:.6g}'
    return str(x)


def write_rows(w, rows: list[list]) -> None:
    for r in rows:
        w.writerow([fmt(x) for x in r])


# ---- 模擬與判定 ----

@dataclass(frozen=True)
class Trade:
    t: int
    sym: str
    side: int
    long: tuple     # 做多情境 (gross, fund, mae, days, stop)
    short: tuple    # 做空情境

    def leg(self, side: int | None = None) -> tuple:
        return self.long if (side or self.side) > 0 else self.short

    def net(self, extra: float = 0.0, side: int | None = None) -> float:
        s = side or self.side
        g, f = self.leg(s)[:2]
        return s * g - 2 * (COST + extra) - s * f

    def pre(self) -> float:
        g, f = self.leg()[:2]
        return self.side * g - self.side * f

    @property
    def exit(self) -> int:
        return self.t + int(self.leg()[3] * D)


def simulate(events: list[tuple]) -> list[Trade]:
    """events：(t, sym, side, strength, long, short)。最多 10 筆、同幣出場前不重複、強度大的優先。"""
    events = sorted(events, key=lambda e: (e[0], -e[3], e[1]))
    open_: dict[str, int] = {}
    out, i = [], 0
    while i < len(events):
        t = events[i][0]
        j = i
        while j < len(events) and events[j][0] == t:
            j += 1
        open_ = {s: x for s, x in open_.items() if x > t}
        for e in events[i:j]:
            if len(open_) >= SLOTS or e[1] in open_:
                continue
            tr = Trade(t, e[1], e[2], e[4], e[5])
            open_[e[1]] = tr.exit
            out.append(tr)
        i = j
    return out


def daily(trades: list[Trade], start: int, end: int, f) -> list[float]:
    """每天（依出場日）的帳戶損益；期間延長 10 天讓最後幾筆的出場算進去。"""
    days = [0.0] * ((end - start) // D + HOLD + 1)
    for tr in trades:
        k = (tr.exit - start) // D
        if 0 <= k < len(days):
            days[k] += SIZE * f(tr)
    return days


def random_means(trades: list[Trade], runs: int = RUNS, seed: int = SEED) -> list[float]:
    rng = random.Random(seed)
    n = len(trades)
    return sorted(sum(x.net(side=1 if rng.random() < 0.5 else -1) for x in trades) / n for _ in range(runs))


def pct(xs: list[float], p: float) -> float:
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def evaluate(trades: list[Trade], period: tuple[int, int], mid: int, runs: int = RUNS) -> dict:
    tr = [x for x in trades if period[0] <= x.t < period[1]]
    if not tr:
        return {'n': 0}
    n = len(tr)
    nets = [x.net() for x in tr]
    eq, v = 1.0, [1.0]
    for x in sorted(tr, key=lambda x: (x.exit, x.sym)):
        eq *= 1 + SIZE * x.net()
        v.append(eq)
    by_coin: dict[str, float] = {}
    for x in tr:
        by_coin[x.sym] = by_coin.get(x.sym, 0.0) + x.net()
    best = max(by_coin, key=by_coin.get)
    rest = [x.net() for x in tr if x.sym != best]
    rnd = random_means(tr, runs)
    a = [x.net() for x in tr if x.t < mid]
    b = [x.net() for x in tr if x.t >= mid]
    return {
        'n': n, 'mean': sum(nets) / n, 'pre': sum(x.pre() for x in tr) / n,
        't': t_stat(daily(tr, *period, Trade.net)), 't_pre': t_stat(daily(tr, *period, Trade.pre)),
        'first': sum(a) / len(a) if a else 0.0, 'second': sum(b) / len(b) if b else 0.0,
        'p90': pct(rnd, 0.90), 'p95': pct(rnd, 0.95),
        'stress': sum(x.net(SLIP) for x in tr) / n, 'dd': max_drawdown(v), 'final': eq,
        'best': best, 'drop_best': sum(rest) / len(rest) if rest else 0.0,
        'long': sum(x.side > 0 for x in tr) / n, 'days': sum(x.leg()[3] for x in tr) / n,
        'stopped': sum(x.leg()[4] for x in tr) / n, 'fund': sum(x.side * x.leg()[1] for x in tr) / n,
        **{f'liq_{k}': sum(x.leg()[2] >= d for x in tr) / n for k, d in LIQ.items()},
    }


def dev_checks(s: dict) -> dict[str, bool]:
    if not s.get('n'):
        return {'筆數': False}
    return {'筆數': s['n'] >= MIN_DEV, '平均>0': s['mean'] > 0, 't≥3': s['t'] >= MIN_T,
            '前後半>0': s['first'] > 0 and s['second'] > 0, '贏隨機P95': s['mean'] > s['p95'],
            '多扣滑價>0': s['stress'] > 0, '回撤': s['dd'] >= MAX_DD, '拿掉最賺幣>0': s['drop_best'] > 0}


def exam_checks(s: dict) -> dict[str, bool]:
    if not s.get('n'):
        return {'筆數': False}
    return {'筆數': s['n'] >= MIN_EXAM, '平均>0': s['mean'] > 0, 't≥2': s['t'] >= EXAM_T,
            '贏隨機P90': s['mean'] > s['p90'], '前後半>0': s['first'] > 0 and s['second'] > 0, '回撤': s['dd'] >= MAX_DD}


def leak(control: dict) -> bool:
    return bool(control.get('n')) and abs(control['t_pre']) >= MIN_T


def pick(stats: dict[str, dict]) -> str | None:
    if leak(stats.get(CONTROL, {})):
        return None
    ok = [g for g in GROUPS if all(dev_checks(stats.get(g, {})).values())]
    return max(ok, key=lambda g: stats[g]['t']) if ok else None


def read_preds(path: Path) -> dict[tuple[int, str], dict[str, tuple[int, float]]]:
    out: dict = {}
    if not path.exists():
        return out
    with path.open(encoding='utf-8', newline='') as f:
        for row in csv.DictReader(f):
            out.setdefault((int(row['t']), row['sym']), {})[row['model']] = (int(row['side']), float(row['score']))
    return out


def load_events(panel: Path, preds: dict, period: tuple[int, int]) -> dict[str, list[tuple]]:
    ev: dict[str, list[tuple]] = {g: [] for g in [*GROUPS, CONTROL]}
    with panel.open(encoding='utf-8', newline='') as f:
        r = csv.reader(f)
        head = next(r)
        ix = {k: i for i, k in enumerate(head)}
        for row in r:
            t = int(row[ix['t']])
            if not period[0] <= t < period[1]:
                continue
            sym = row[ix['sym']]
            lg = tuple(float(row[ix[f'{k}_l']]) for k in SIDES)
            sh = tuple(float(row[ix[f'{k}_s']]) for k in SIDES)
            gate = int(row[ix['gate']])
            side = int(row[ix['trend']])
            if side:
                ev['trend'].append((t, sym, side, float(row[ix['trend_s']]), lg, sh))
            p = preds.get((t, sym), {})
            side, score = p.get('gbm', (0, 0.0))
            if side:
                ev['swing_gbm'].append((t, sym, side, abs(score), lg, sh))
                if side == gate:
                    ev['swing_gate'].append((t, sym, side, abs(score), lg, sh))
            side, score = p.get('gbm_shuf', (0, 0.0))
            if side and side == gate:
                ev[CONTROL].append((t, sym, side, abs(score), lg, sh))
    return ev


def run(panel: Path, preds_path: Path, period: tuple[int, int], mid: int, runs: int = RUNS) -> dict[str, dict]:
    ev = load_events(panel, read_preds(preds_path), period)
    return {g: evaluate(simulate(e), period, mid, runs) for g, e in ev.items()}


def pc(x: float) -> str:
    return f'{x * 100:+.2f}%'


def report(stats: dict[str, dict], when: str, stage: str = 'dev') -> str:
    checks = dev_checks if stage == 'dev' else exam_checks
    lines = [f'# 第三十一輪 波段（抱 1～10 天）：{"研究期 2021～2024" if stage == "dev" else "考卷 2026-01～10"}（{when}）', '',
             '每筆 1 倍、10% 資金、最多 10 筆；2 × ATR 停損或 10 天出場；吃單每邊 0.05%＋實際資金費率。t 用每天帳戶損益算。', '',
             '| 組 | 筆數 | 每筆淨 | t | 扣成本前 | 扣前 t | 前半 | 後半 | 隨機 P95 | 多扣滑價 | 回撤 | 帳戶倍數 | 拿掉最賺幣 | 做多 | 平均天數 | 停損 | 每筆資金費 |',
             '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for g in [*GROUPS, CONTROL]:
        s = stats.get(g, {})
        if not s.get('n'):
            lines.append(f'| `{g}` | 0 |' + ' |' * 15)
            continue
        lines.append(f"| `{g}` | {s['n']} | {pc(s['mean'])} | {s['t']:.2f} | {pc(s['pre'])} | {s['t_pre']:.2f} | {pc(s['first'])} | {pc(s['second'])} | "
                     f"{pc(s['p95'])} | {pc(s['stress'])} | {s['dd'] * 100:.1f}% | {s['final']:.2f} | {pc(s['drop_best'])}（{s['best']}） | "
                     f"{s['long'] * 100:.0f}% | {s['days']:.1f} | {s['stopped'] * 100:.0f}% | {pc(s['fund'])} |")
    lines += ['', '途中最大反向碰到爆倉距離的比例（只報告）：', '']
    for g in [*GROUPS, CONTROL]:
        s = stats.get(g, {})
        if s.get('n'):
            lines.append(f"- `{g}`：" + '、'.join(f"{k} {s['liq_' + k] * 100:.1f}%" for k in LIQ))
    lines += ['', '## 判定', '']
    for g in GROUPS:
        if g not in stats:
            continue
        bad = [k for k, v in checks(stats[g]).items() if not v]
        lines.append(f"- `{g}`：{'全部通過' if not bad else '沒過：' + '、'.join(bad)}")
    if stage == 'dev':
        ctl = stats.get(CONTROL, {})
        lines.append(f"- 對照 `{CONTROL}` 扣成本前 t {ctl.get('t_pre', 0):.2f}：{'|t| ≥ 3，當程式有問題，全部不算通過' if leak(ctl) else '沒有洩漏'}")
        p = pick(stats)
        lines.append(f"- 結論：{'進考卷：`' + p + '`' if p else '沒有組通過，不進考卷'}")
    return '\n'.join(lines) + '\n'
