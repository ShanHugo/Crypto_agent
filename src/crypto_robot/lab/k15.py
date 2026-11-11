"""第二十七輪：15 分 K 合約短線（DESIGN.md 6.23、6.3，2026-11-11 營運者同意）。**看過結果後不改**。

主環境的部分（純 Python，不需要 numpy）：
- 每月名單（前 30 天成交金額前 30 名、上市滿 90 天）；
- 每根 15 分 K 收盤算 10 項特徵、答案、持有 4 根的結果、`brk`／`rev` 訊號，寫成 CSV 給 scripts/k15_train.py（_vendor/ml_venv）；
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
BAR = 900_000                    # 15 分鐘
TOP, AGE = 30, 90
H, LOOK, BOX, VLOOK = 4, 96, 16, 96
BRK_VOL, REV_Z = 1.5, 2.5
COST, SLIP = 0.0005, 0.0005       # 每邊手續費；壓力測試每邊再多扣
SLOTS, SIZE = 10, 0.1
FIRST_MONTH, LAST_MONTH = '2020-01', '2026-10'
DEV = (ms(2021, 1, 1), ms(2024, 1, 1))
DEV_MID = ms(2022, 7, 1)
EXAM = (ms(2024, 1, 1), ms(2026, 1, 1))
EXAM_MID = ms(2025, 1, 1)
MIN_DEV, MIN_EXAM, MIN_T, EXAM_T, MAX_DD = 1000, 500, 3.0, 2.0, -0.30
RUNS, SEED = 1000, 100
LIQ = {'10x': 0.09, '20x': 0.04}  # 1／槓桿 − 維持保證金 1%
GROUPS = ['brk', 'rev', 'gbm']
CONTROL = 'gbm_shuf'
FEATURES = ['z1', 'z4', 'z16', 'z96', 'pos16', 'vratio', 'taker4', 'fund', 'btc_z4', 'hour']
COLUMNS = ['t', 'sym', *FEATURES, 'target', 'gross', 'fundsum', 'mae_l', 'mae_s', 'brk', 'brk_s', 'rev', 'rev_s']


def month_key(t: int) -> str:
    return datetime.fromtimestamp(t / 1000, timezone.utc).strftime('%Y-%m')


def month_start(key: str) -> int:
    y, m = map(int, key.split('-'))
    return ms(y, m, 1)


def months(first: str = FIRST_MONTH, last: str = LAST_MONTH) -> list[str]:
    out, (y, m) = [], map(int, first.split('-'))
    while f'{y:04d}-{m:02d}' <= last:
        out.append(f'{y:04d}-{m:02d}')
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def universe(coins: dict, keys: list[str]) -> dict[str, list[str]]:
    """每月 1 日：前一天還有日 K、這一段上市滿 90 天的幣，按前 30 天成交金額取前 30 名。coins 是 xsec.Coin。"""
    out = {}
    for key in keys:
        t = month_start(key)
        vols = []
        for sym, c in coins.items():
            i = c.pos.get(t - D)
            if i is None or c.seg[i] > t - AGE * D:
                continue
            a, b = c.span(t, 30)
            vols.append((-sum(c.qv[a:b]), sym))
        out[key] = [s for _, s in sorted(vols)[:TOP]]
    return out


@dataclass
class Bars:
    """一個幣的 15 分 K（開盤時間排序、不重複）。"""
    t: list[int]
    o: list[float]
    h: list[float]
    l: list[float]  # noqa: E741
    c: list[float]
    qv: list[float]
    tb: list[float]

    @classmethod
    def from_rows(cls, rows: list[list]) -> 'Bars':
        rows = sorted({int(r[0]): r for r in rows if float(r[1]) > 0 and float(r[4]) > 0}.values(), key=lambda r: int(r[0]))
        cols = list(zip(*rows)) if rows else [[]] * 7
        return cls([int(x) for x in cols[0]], *[[float(x) for x in col] for col in cols[1:7]])


class Rolling:
    """前綴和：O(1) 算區間的和與標準差。"""

    def __init__(self, xs: list[float]):
        self.s, self.q = [0.0], [0.0]
        for x in xs:
            self.s.append(self.s[-1] + x)
            self.q.append(self.q[-1] + x * x)

    def total(self, a: int, b: int) -> float:
        return self.s[b] - self.s[a]

    def std(self, a: int, b: int) -> float:
        n = b - a
        mean = (self.s[b] - self.s[a]) / n
        var = ((self.q[b] - self.q[a]) - n * mean * mean) / (n - 1)
        return math.sqrt(var) if var > 0 else 0.0


def ret_roll(b: Bars) -> Rolling:
    """單根報酬 r[i] = c[i]/c[i-1]-1（第 0 根是 0）的前綴和。"""
    return Rolling([0.0] + [b.c[i] / b.c[i - 1] - 1 for i in range(1, len(b.c))])


def vol_at(b: Bars, roll: Rolling, i: int) -> float | None:
    """收盤 i 時，過去 96 根單根報酬的標準差；前 96 根不連續就 None。"""
    if i < LOOK or b.t[i] - b.t[i - LOOK] != LOOK * BAR:
        return None
    sd = roll.std(i - LOOK + 1, i + 1)
    return sd or None


def btc_z4(b: Bars) -> dict[int, float]:
    """BTC 每根收盤時間 → 過去 4 根標準化報酬。"""
    roll = ret_roll(b)
    out = {}
    for i in range(LOOK, len(b.t)):
        sd = vol_at(b, roll, i)
        if sd:
            out[b.t[i] + BAR] = (b.c[i] / b.c[i - 4] - 1) / (sd * 2)
    return out


def rows_for(sym: str, b: Bars, ft: list[int], fr: list[float], btc: dict[int, float], in_uni) -> list[list]:
    """一個幣的每一列（只留名單月份、前 96 根與後 5 根都連續的收盤）。in_uni(月份) → bool。"""
    roll = ret_roll(b)
    qroll = Rolling(b.qv)
    out = []
    for i in range(LOOK, len(b.t) - H - 1):
        T = b.t[i] + BAR
        if not in_uni(month_key(T)) or b.t[i + H + 1] - b.t[i] != (H + 1) * BAR:
            continue
        sd = vol_at(b, roll, i)
        if sd is None:
            continue
        c = b.c[i]
        z = [(c / b.c[i - n] - 1) / (sd * math.sqrt(n)) for n in (1, 4, 16, 96)]
        hi, lo = max(b.h[i - BOX + 1:i + 1]), min(b.l[i - BOX + 1:i + 1])
        pos = (c - lo) / (hi - lo) if hi > lo else 0.5
        avg = qroll.total(i - VLOOK, i) / VLOOK
        vr = b.qv[i] / avg if avg > 0 else 0.0
        q4 = qroll.total(i - 3, i + 1)
        taker = sum(b.tb[i - 3:i + 1]) / q4 if q4 > 0 else 0.5
        k = bisect.bisect_right(ft, T) - 1
        fund = fr[k] if k >= 0 else 0.0
        e, x = b.o[i + 1], b.o[i + H + 1]
        gross = x / e - 1
        E, X = b.t[i + 1], b.t[i + H + 1]
        fundsum = sum(fr[bisect.bisect_right(ft, E):bisect.bisect_right(ft, X)])
        mae_l = 1 - min(b.l[i + 1:i + H + 1]) / e
        mae_s = max(b.h[i + 1:i + H + 1]) / e - 1
        ph, pl = max(b.h[i - BOX:i]), min(b.l[i - BOX:i])
        brk = 1 if c > ph and vr >= BRK_VOL else -1 if c < pl and vr >= BRK_VOL else 0
        rev = 1 if z[1] <= -REV_Z else -1 if z[1] >= REV_Z else 0
        out.append([T, sym, *z, pos, vr, taker, fund, btc.get(T), (T // 3_600_000) % 24,
                    gross / (sd * 2), gross, fundsum, mae_l, mae_s, brk, vr if brk else 0, rev, abs(z[1]) if rev else 0])
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
    t: int          # 進場時間（＝訊號那根的收盤時間）
    sym: str
    side: int
    gross: float    # 做多的進出場報酬
    fund: float     # 抱單期間結算的資金費率合計（做多付正費率）
    mae: float      # 途中最大反向幅度（依方向）

    def net(self, cost: float = COST) -> float:
        return self.side * self.gross - 2 * cost - self.side * self.fund

    def pre(self) -> float:
        return self.side * self.gross - self.side * self.fund


def simulate(events: list[tuple]) -> list[Trade]:
    """events：(t, sym, side, strength, gross, fundsum, mae_l, mae_s)。最多 10 筆、同幣不重複、強度大的優先。"""
    events = sorted(events, key=lambda e: (e[0], -e[3], e[1]))
    open_: dict[str, int] = {}        # 幣 → 出場時間
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
            open_[e[1]] = t + H * BAR
            out.append(Trade(t, e[1], e[2], e[4], e[5], e[6] if e[2] > 0 else e[7]))
        i = j
    return out


def daily(trades: list[Trade], start: int, end: int, f) -> list[float]:
    """每天（依出場日）的帳戶損益：每筆 10% 資金 × f(trade)；沒有單的天是 0。"""
    days = [0.0] * ((end - start) // D)
    for tr in trades:
        k = (tr.t + H * BAR - start) // D
        if 0 <= k < len(days):
            days[k] += SIZE * f(tr)
    return days


def random_means(trades: list[Trade], runs: int = RUNS, seed: int = SEED) -> list[float]:
    """同一批單隨機方向的每筆平均淨報酬。"""
    rng = random.Random(seed)
    base = [tr.gross - tr.fund for tr in trades]
    n = len(base)
    out = []
    for _ in range(runs):
        out.append(sum(b if rng.random() < 0.5 else -b for b in base) / n - 2 * COST)
    return sorted(out)


def pct(xs: list[float], p: float) -> float:
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def evaluate(trades: list[Trade], period: tuple[int, int], mid: int, runs: int = RUNS) -> dict:
    tr = [x for x in trades if period[0] <= x.t < period[1]]
    if not tr:
        return {'n': 0}
    nets = [x.net() for x in tr]
    eq, v = 1.0, [1.0]
    for x in sorted(tr, key=lambda x: (x.t, x.sym)):
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
        'n': len(tr), 'mean': sum(nets) / len(tr), 'pre': sum(x.pre() for x in tr) / len(tr),
        't': t_stat(daily(tr, *period, Trade.net)), 't_pre': t_stat(daily(tr, *period, Trade.pre)),
        'first': sum(a) / len(a) if a else 0.0, 'second': sum(b) / len(b) if b else 0.0,
        'rnd_mean': sum(rnd) / len(rnd), 'p90': pct(rnd, 0.90), 'p95': pct(rnd, 0.95),
        'stress': sum(x.net(COST + SLIP) for x in tr) / len(tr),
        'dd': max_drawdown(v), 'final': eq, 'best': best,
        'drop_best': sum(rest) / len(rest) if rest else 0.0,
        'long': sum(x.side > 0 for x in tr) / len(tr),
        **{f'liq_{k}': sum(x.mae >= d for x in tr) / len(tr) for k, d in LIQ.items()},
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


def load_events(panel: Path, preds: dict[tuple[int, str], dict[str, tuple[int, float]]], period: tuple[int, int]) -> dict[str, list[tuple]]:
    """讀 panel，收集各組在期間內的訊號。preds：(t, sym) → {'gbm': (side, score), 'gbm_shuf': ...}。"""
    ev: dict[str, list[tuple]] = {g: [] for g in [*GROUPS, CONTROL]}
    with panel.open(encoding='utf-8', newline='') as f:
        r = csv.reader(f)
        head = next(r)
        ix = {k: head.index(k) for k in ('t', 'sym', 'gross', 'fundsum', 'mae_l', 'mae_s', 'brk', 'brk_s', 'rev', 'rev_s')}
        for row in r:
            t = int(row[ix['t']])
            if not period[0] <= t < period[1]:
                continue
            sym = row[ix['sym']]
            base = (float(row[ix['gross']]), float(row[ix['fundsum']]), float(row[ix['mae_l']]), float(row[ix['mae_s']]))
            for g in ('brk', 'rev'):
                side = int(row[ix[g]])
                if side:
                    ev[g].append((t, sym, side, float(row[ix[g + '_s']]), *base))
            for g, (side, score) in preds.get((t, sym), {}).items():
                if side:
                    ev[g].append((t, sym, side, abs(score), *base))
    return ev


def read_preds(path: Path) -> dict[tuple[int, str], dict[str, tuple[int, float]]]:
    out: dict = {}
    if not path.exists():
        return out
    with path.open(encoding='utf-8', newline='') as f:
        for row in csv.DictReader(f):
            out.setdefault((int(row['t']), row['sym']), {})[row['model']] = (int(row['side']), float(row['score']))
    return out


def run(panel: Path, preds_path: Path, period: tuple[int, int], mid: int, runs: int = RUNS) -> dict[str, dict]:
    ev = load_events(panel, read_preds(preds_path), period)
    return {g: evaluate(simulate(e), period, mid, runs) for g, e in ev.items()}


def pc(x: float) -> str:
    return f'{x * 100:+.3f}%'


def report(stats: dict[str, dict], when: str, stage: str = 'dev') -> str:
    checks = dev_checks if stage == 'dev' else exam_checks
    lines = [f'# 第二十七輪 15 分 K 合約短線：{"研究期 2021～2023" if stage == "dev" else "考卷 2024～2025"}（{when}）', '',
             '每筆 1 倍、10% 資金、最多 10 筆；成本每邊 0.05%＋實際資金費率。t 用每天帳戶損益算。', '',
             '| 組 | 筆數 | 每筆淨 | t | 扣成本前 | 扣前 t | 前半 | 後半 | 隨機 P95 | 多扣滑價 | 回撤 | 拿掉最賺幣 | 做多比例 | 碰 10 倍／20 倍爆倉 |',
             '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for g in [*GROUPS, CONTROL]:
        s = stats.get(g, {})
        if not s.get('n'):
            lines.append(f'| `{g}` | 0 | | | | | | | | | | | | |')
            continue
        lines.append(f"| `{g}` | {s['n']} | {pc(s['mean'])} | {s['t']:.2f} | {pc(s['pre'])} | {s['t_pre']:.2f} | {pc(s['first'])} | {pc(s['second'])} | "
                     f"{pc(s['p95'])} | {pc(s['stress'])} | {s['dd'] * 100:.1f}% | {pc(s['drop_best'])}（{s['best']}） | {s['long'] * 100:.0f}% | "
                     f"{s['liq_10x'] * 100:.2f}%／{s['liq_20x'] * 100:.2f}% |")
    lines += ['', '## 判定', '']
    for g in GROUPS:
        c = checks(stats.get(g, {}))
        bad = [k for k, v in c.items() if not v]
        lines.append(f"- `{g}`：{'全部通過' if not bad else '沒過：' + '、'.join(bad)}")
    ctl = stats.get(CONTROL, {})
    lines.append(f"- 對照 `{CONTROL}` 扣成本前 t {ctl.get('t_pre', 0):.2f}：{'|t| ≥ 3，當程式有問題，全部不算通過' if leak(ctl) else '沒有洩漏'}")
    if stage == 'dev':
        p = pick(stats)
        lines.append(f"- 結論：{'進考卷：`' + p + '`' if p else '沒有組通過，不進考卷'}")
    return '\n'.join(lines) + '\n'
