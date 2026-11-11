"""第二十八輪：多週期 gbm，日線 → 4 小時 → 1 小時 → 15 分（DESIGN.md 6.24、6.3，2026-11-11 營運者同意）。
第二十九輪（DESIGN 6.25）擴充：`mtf_gate` 掛單進場、只做最強訊號。**看過結果後不改**。

主環境的部分（純 Python，不需要 numpy）：
- 每根 15 分 K 收盤算 25 項特徵（15 分 10 項沿用第二十七輪定義、日線 8 項、4 小時 4 項、1 小時 3 項）、答案、
  抱 16 根的結果、方向閘門，寫成 CSV 給 scripts/mtf_train.py（_vendor/ml_venv）；
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
H, LOOK, BOX, VLOOK = 16, 96, 16, 96
COST, SLIP = 0.0005, 0.0005     # 吃單每邊；壓力測試每邊再多扣
MAKER = 0.0002                   # 掛單（2026-11-11 官網，USDT 永續一般用戶）
SLOTS, SIZE = 10, 0.1
DEV = (ms(2021, 1, 1), ms(2024, 1, 1))
DEV_MID = ms(2022, 7, 1)
EXAM = (ms(2024, 1, 1), ms(2026, 1, 1))
EXAM_MID = ms(2025, 1, 1)
MIN_DEV, MIN_EXAM, MIN_T, EXAM_T, MAX_DD = 1000, 500, 3.0, 2.0, -0.30
RUNS, SEED = 1000, 100
LIQ = {'10x': 0.09, '20x': 0.04}
GROUPS = ['gate', 'gate_maker', 'gate_maker_top']
CONTROL = 'gate_maker_shuf'
BASE = ['z1', 'z4', 'z16', 'z96', 'pos16', 'vratio', 'taker4', 'fund', 'btc_z4', 'hour']
HTF = ['d_z1', 'd_z3', 'd_z7', 'd_z30', 'd_ma20', 'd_ma50', 'd_vol20', 'd_fund3',
       'h4_z1', 'h4_z6', 'h4_ma20', 'h4_pos6', 'h1_z1', 'h1_z4', 'h1_ma24']
FEATURES = BASE + HTF
COLUMNS = ['t', 'sym', *FEATURES, 'target', 'gross', 'fundsum', 'mae_l', 'mae_s', 'gate',
           'mgross', 'fill_l', 'fill_s', 'mmae_l', 'mmae_s']   # 掛單：成交價＝訊號那根收盤


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


def ret_roll(c: list[float]) -> Rolling:
    return Rolling([0.0] + [c[i] / c[i - 1] - 1 for i in range(1, len(c))])


class Frame:
    """一個週期的 K 線（開盤時間、收、高、低），可以問「時間 T 時最後一根收完的是第幾根」。"""

    def __init__(self, t: list[int], c: list[float], h: list[float], lo: list[float], step: int):
        self.t, self.c, self.h, self.l, self.step = t, c, h, lo, step
        self.roll = ret_roll(c)
        self.pos = {x: i for i, x in enumerate(t)}

    @classmethod
    def agg(cls, b: Bars, n: int) -> 'Frame':
        """每 n 根 15 分 K 合併（UTC 對齊）；不滿 n 根的不要。"""
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

    def last(self, T: int, need: int) -> int | None:
        """T 以前最後一根收完的 K 的索引；它和往前 need 根要連續，不然 None。"""
        k = self.pos.get(T // self.step * self.step - self.step)
        if k is None or k < need or self.t[k] - self.t[k - need] != need * self.step:
            return None
        return k

    def z(self, k: int, n: int, vol_n: int) -> float | None:
        sd = self.roll.std(k - vol_n + 1, k + 1)
        return (self.c[k] / self.c[k - n] - 1) / (sd * math.sqrt(n)) if sd else None

    def ma(self, k: int, n: int) -> float:
        return self.c[k] / (sum(self.c[k - n + 1:k + 1]) / n) - 1


def daily_frame(coin) -> Frame:
    """xsec.Coin 的日 K（只有開、收；高低用不到）。"""
    return Frame(list(coin.t), list(coin.c), list(coin.c), list(coin.c), D)


def htf(T: int, d: Frame, h4: Frame, h1: Frame, ft: list[int], fr: list[float]) -> list[float | None]:
    out: list[float | None] = [None] * len(HTF)
    k = d.last(T, 50)
    if k is not None:
        out[0:7] = [d.z(k, 1, 20), d.z(k, 3, 20), d.z(k, 7, 20), d.z(k, 30, 20), d.ma(k, 20), d.ma(k, 50),
                    d.roll.std(k - 19, k + 1)]
    a, b = bisect.bisect_right(ft, T - 3 * D), bisect.bisect_right(ft, T)
    out[7] = sum(fr[a:b]) / (b - a) if b > a else None
    k = h4.last(T, 30)
    if k is not None:
        hi, lo = max(h4.h[k - 5:k + 1]), min(h4.l[k - 5:k + 1])
        out[8:12] = [h4.z(k, 1, 30), h4.z(k, 6, 30), h4.ma(k, 20), (h4.c[k] - lo) / (hi - lo) if hi > lo else 0.5]
    k = h1.last(T, 24)
    if k is not None:
        out[12:15] = [h1.z(k, 1, 24), h1.z(k, 4, 24), h1.ma(k, 24)]
    return out


def gate(d_ma20: float | None, h4_ma20: float | None) -> int:
    if d_ma20 is None or h4_ma20 is None:
        return 0
    return 1 if d_ma20 > 0 and h4_ma20 > 0 else -1 if d_ma20 < 0 and h4_ma20 < 0 else 0


def btc_z4(b: Bars) -> dict[int, float]:
    roll = ret_roll(b.c)
    out = {}
    for i in range(LOOK, len(b.t)):
        if b.t[i] - b.t[i - LOOK] == LOOK * BAR:
            sd = roll.std(i - LOOK + 1, i + 1)
            if sd:
                out[b.t[i] + BAR] = (b.c[i] / b.c[i - 4] - 1) / (sd * 2)
    return out


def rows_for(sym: str, b: Bars, day: Frame, ft: list[int], fr: list[float], btc: dict[int, float], in_uni) -> list[list]:
    """一個幣的每一列（名單月份、前 96 根與後 17 根連續）。15 分特徵定義同第二十七輪。"""
    roll, qroll = ret_roll(b.c), Rolling(b.qv)
    h4, h1 = Frame.agg(b, 16), Frame.agg(b, 4)
    out = []
    for i in range(LOOK, len(b.t) - H - 1):
        T = b.t[i] + BAR
        if not in_uni(month_key(T)) or b.t[i + H + 1] - b.t[i] != (H + 1) * BAR or b.t[i] - b.t[i - LOOK] != LOOK * BAR:
            continue
        sd = roll.std(i - LOOK + 1, i + 1)
        if not sd:
            continue
        c = b.c[i]
        z = [(c / b.c[i - n] - 1) / (sd * math.sqrt(n)) for n in (1, 4, 16, 96)]
        hi, lo = max(b.h[i - BOX + 1:i + 1]), min(b.l[i - BOX + 1:i + 1])
        avg = qroll.total(i - VLOOK, i) / VLOOK
        q4 = qroll.total(i - 3, i + 1)
        k = bisect.bisect_right(ft, T) - 1
        base = [*z, (c - lo) / (hi - lo) if hi > lo else 0.5, b.qv[i] / avg if avg > 0 else 0.0,
                sum(b.tb[i - 3:i + 1]) / q4 if q4 > 0 else 0.5, fr[k] if k >= 0 else 0.0, btc.get(T), (T // 3_600_000) % 24]
        hx = htf(T, day, h4, h1, ft, fr)
        e, x = b.o[i + 1], b.o[i + H + 1]
        gross = x / e - 1
        E, X = b.t[i + 1], b.t[i + H + 1]
        out.append([T, sym, *base, *hx, gross / (sd * 4), gross,
                    sum(fr[bisect.bisect_right(ft, E):bisect.bisect_right(ft, X)]),
                    1 - min(b.l[i + 1:i + H + 1]) / e, max(b.h[i + 1:i + H + 1]) / e - 1, gate(hx[4], hx[10]),
                    x / c - 1, int(b.l[i + 1] < c), int(b.h[i + 1] > c),
                    1 - min(b.l[i + 1:i + H + 1]) / c, max(b.h[i + 1:i + H + 1]) / c - 1])
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


# ---- 模擬與判定（同第二十七輪，只是抱 16 根） ----

@dataclass(frozen=True)
class Trade:
    t: int
    sym: str
    side: int
    gross: float
    fund: float
    mae: float
    cin: float = COST       # 進場手續費（掛單 0.02%）
    cout: float = COST

    def net(self, extra: float = 0.0) -> float:
        return self.side * self.gross - self.cin - self.cout - 2 * extra - self.side * self.fund

    def pre(self) -> float:
        return self.side * self.gross - self.side * self.fund


def simulate(events: list[tuple]) -> tuple[list[Trade], list[tuple]]:
    """events：(t, sym, side, strength, gross, fundsum, mae_l, mae_s, filled, cin[, 開盤進場報酬])。
    最多 10 筆、同幣不重複、強度大的優先；沒成交的掛單照樣佔挑選順位、不佔持倉。回傳（成交的單, 被挑中沒成交的訊號）。"""
    events = sorted(events, key=lambda e: (e[0], -e[3], e[1]))
    open_: dict[str, int] = {}
    out, missed, i = [], [], 0
    while i < len(events):
        t = events[i][0]
        j = i
        while j < len(events) and events[j][0] == t:
            j += 1
        open_ = {s: x for s, x in open_.items() if x > t}
        picked = 0
        for e in events[i:j]:
            if len(open_) + picked >= SLOTS or e[1] in open_:
                continue
            if not e[8]:
                picked += 1
                missed.append(e)
                continue
            open_[e[1]] = t + H * BAR
            out.append(Trade(t, e[1], e[2], e[4], e[5], e[6] if e[2] > 0 else e[7], e[9]))
        i = j
    return out, missed


def daily(trades: list[Trade], start: int, end: int, f) -> list[float]:
    days = [0.0] * ((end - start) // D)
    for tr in trades:
        k = (tr.t + H * BAR - start) // D
        if 0 <= k < len(days):
            days[k] += SIZE * f(tr)
    return days


def random_means(trades: list[Trade], runs: int = RUNS, seed: int = SEED) -> list[float]:
    rng = random.Random(seed)
    base = [tr.gross - tr.fund for tr in trades]
    cost = sum(tr.cin + tr.cout for tr in trades) / len(trades)
    n = len(base)
    return sorted(sum(b if rng.random() < 0.5 else -b for b in base) / n - cost for _ in range(runs))


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
        'p90': pct(rnd, 0.90), 'p95': pct(rnd, 0.95),
        'stress': sum(x.net(SLIP) for x in tr) / len(tr),
        'dd': max_drawdown(v), 'best': best, 'drop_best': sum(rest) / len(rest) if rest else 0.0,
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


def checks_for(g: str, stats: dict[str, dict]) -> dict[str, bool]:
    return dev_checks(stats.get(g, {}))


def leak(control: dict) -> bool:
    return bool(control.get('n')) and abs(control['t_pre']) >= MIN_T


def pick(stats: dict[str, dict]) -> str | None:
    if leak(stats.get(CONTROL, {})):
        return None
    ok = [g for g in GROUPS if all(checks_for(g, stats).values())]
    return max(ok, key=lambda g: stats[g]['t']) if ok else None


def read_preds(path: Path) -> dict[tuple[int, str], dict[str, tuple[int, float, int]]]:
    """(t, sym) → {模型: (方向, 預測值, 是否 2% 最強)}。"""
    out: dict = {}
    if not path.exists():
        return out
    with path.open(encoding='utf-8', newline='') as f:
        for row in csv.DictReader(f):
            out.setdefault((int(row['t']), row['sym']), {})[row['model']] = (int(row['side']), float(row['score']), int(row['top']))
    return out


def load_events(panel: Path, preds: dict, period: tuple[int, int]) -> dict[str, list[tuple]]:
    """各組在期間內、和方向閘門一致的訊號。"""
    ev: dict[str, list[tuple]] = {g: [] for g in [*GROUPS, CONTROL]}
    keys = ('t', 'sym', 'gross', 'fundsum', 'mae_l', 'mae_s', 'gate', 'mgross', 'fill_l', 'fill_s', 'mmae_l', 'mmae_s')
    with panel.open(encoding='utf-8', newline='') as f:
        r = csv.reader(f)
        head = next(r)
        ix = {k: head.index(k) for k in keys}
        for row in r:
            t = int(row[ix['t']])
            if not period[0] <= t < period[1]:
                continue
            p = preds.get((t, row[ix['sym']]))
            if not p:
                continue
            v = {k: row[ix[k]] for k in keys}
            fund = float(v['fundsum'])
            for model, groups in (('mtf', ('gate', 'gate_maker', 'gate_maker_top')), ('mtf_shuf', ('gate_maker_shuf',))):
                side, score, top = p.get(model, (0, 0.0, 0))
                if not side or side != int(v['gate']):
                    continue
                filled = int(v['fill_l'] if side > 0 else v['fill_s'])
                taker = (t, v['sym'], side, abs(score), float(v['gross']), fund, float(v['mae_l']), float(v['mae_s']), 1, COST)
                maker = (t, v['sym'], side, abs(score), float(v['mgross']), fund, float(v['mmae_l']), float(v['mmae_s']), filled, MAKER,
                         float(v['gross']))
                for g in groups:
                    if g == 'gate':
                        ev[g].append(taker)
                    elif g != 'gate_maker_top' or top:
                        ev[g].append(maker)
    return ev


def fill_stats(trades: list[Trade], missed: list[tuple], events: list[tuple], period: tuple[int, int]) -> dict:
    """只報告：掛單成交率；被挑中的掛單裡，成交與沒成交的「下一根開盤進場」報酬（扣成本前，看逆向選擇）。"""
    open_px = {(e[0], e[1]): e[10] for e in events if len(e) > 10}
    got = [x for x in trades if period[0] <= x.t < period[1]]
    miss = [e for e in missed if period[0] <= e[0] < period[1]]
    if not got and not miss:
        return {}
    a = [x.side * open_px.get((x.t, x.sym), 0.0) for x in got]
    b = [e[2] * e[10] for e in miss]
    return {'fill': len(got) / (len(got) + len(miss)), 'open_filled': sum(a) / len(a) if a else 0.0,
            'open_missed': sum(b) / len(b) if b else 0.0}


def run(panel: Path, preds_path: Path, period: tuple[int, int], mid: int, runs: int = RUNS) -> dict[str, dict]:
    ev = load_events(panel, read_preds(preds_path), period)
    out = {}
    for g, e in ev.items():
        trades, missed = simulate(e)
        out[g] = {**evaluate(trades, period, mid, runs), **(fill_stats(trades, missed, e, period) if g != 'gate' else {})}
    return out


def pc(x: float) -> str:
    return f'{x * 100:+.3f}%'


def report(stats: dict[str, dict], when: str, stage: str = 'dev') -> str:
    lines = [f'# 第二十九輪 mtf_gate 掛單＋只做最強（抱 4 小時）：{"研究期 2021～2023" if stage == "dev" else "考卷 2024～2025"}（{when}）', '',
             '每筆 1 倍、10% 資金、最多 10 筆；吃單 0.05%、掛單 0.02%＋實際資金費率。t 用每天帳戶損益算。', '',
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
    lines += ['', '## 掛單（只報告）', '']
    for g in [*GROUPS, CONTROL]:
        s = stats.get(g, {})
        if 'fill' in s:
            lines.append(f"- `{g}`：成交率 {s['fill'] * 100:.1f}%；若改成下一根開盤進場，成交的單每筆 {pc(s['open_filled'])}、"
                         f"沒成交的單 {pc(s['open_missed'])}（扣成本前）")
    lines += ['', '## 判定', '']
    for g in GROUPS:
        if g not in stats:
            continue
        c = checks_for(g, stats) if stage == 'dev' else exam_checks(stats[g])
        bad = [k for k, v in c.items() if not v]
        lines.append(f"- `{g}`：{'全部通過' if not bad else '沒過：' + '、'.join(bad)}")
    if stage == 'dev':
        ctl = stats.get(CONTROL, {})
        lines.append(f"- 對照 `{CONTROL}` 扣成本前 t {ctl.get('t_pre', 0):.2f}：{'|t| ≥ 3，當程式有問題，全部不算通過' if leak(ctl) else '沒有洩漏'}")
        p = pick(stats)
        lines.append(f"- 結論：{'進考卷：`' + p + '`' if p else '沒有組通過，不進考卷'}")
    return '\n'.join(lines) + '\n'
