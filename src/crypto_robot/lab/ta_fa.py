"""第十七輪：技術面與基本面研究 Agent（DESIGN.md 6.8，2026-11-05 營運者同意）。**看過結果後不改**。

每週一台北 00:00（UTC 週日 16:00）判斷一次方向，持有 7 天；ETHUSDT 永續合約 1 倍、可多可空。
價格＝那個時點前一根 1h K 線的收盤。成本：換方向按「部位變化」收手續費（空手→多 0.05%、多→空 0.10%，續抱不收）＋持倉期間實際資金費率（多付空收）。
研究統計用 float（不碰交易路徑）。
"""
from __future__ import annotations

import bisect
import json
import math
import random
import statistics as st
from datetime import datetime, timezone
from pathlib import Path

from crypto_robot.lab.futures import Funding
from crypto_robot.lab.hourly import H, Series, ms

D = 24 * H
W = 7 * D
FEE = 0.0010                       # 來回
EXAM_FEE = 0.0020                  # 技術面考卷（現貨價格、資金費率當 0）手續費加倍當壓力測試
RANDOM_RUNS = 1000
LAMBDA = 0.94
DEV = (ms(2021, 1, 1), ms(2024, 1, 1))
DEV_MID = ms(2022, 7, 1)
EXAM_TA = (ms(2017, 8, 17), ms(2021, 1, 1))      # Binance 現貨，還沒當過考卷
EXAM_FA = (ms(2024, 1, 1), ms(2100, 1, 1))       # 合約；價格看過、這個規則沒看過 → 結果打折
TA_VARIANTS = ['tsmom4', 'tsmom12', 'trend_vote', 'ma200']
MIN_T = 2.0
FRED_LAG = W                       # 總經與穩定幣：一律用一週前已有的值（避免偷看還沒公布的數字）


def mondays(start: int, end: int) -> list[int]:
    """台北週一 00:00＝UTC 週日 16:00。"""
    t = start // D * D + 16 * H
    while datetime.fromtimestamp(t / 1000, timezone.utc).weekday() != 6 or t < start:
        t += D
    return list(range(t, end, W))


def price(eth: Series, t: int) -> float | None:
    i = eth.idx.get(t - H)
    return None if i is None else eth.c[i]


def closes(eth: Series, t: int, days: int) -> list[float] | None:
    """t 往前 days 個台北日界（含 t）的價格，舊到新；有缺就 None。"""
    out = [price(eth, t - k * D) for k in range(days - 1, -1, -1)]
    return None if any(x is None for x in out) else out


def ta_signal(name: str, eth: Series, t: int) -> int | None:
    c = closes(eth, t, 200 if name == 'ma200' else 85)
    if c is None:
        return None
    sign = lambda x: (x > 0) - (x < 0)   # noqa: E731
    if name == 'ma200':
        return sign(c[-1] - st.fmean(c))
    r = {k: c[-1] / c[-1 - k] - 1 for k in (7, 28, 84)}
    if name == 'tsmom4':
        return sign(r[28])
    if name == 'tsmom12':
        return sign(r[84])
    return sign(sum(sign(x) for x in r.values()))           # trend_vote：三票多數決


class Macro:
    """[[毫秒, 數值], ...] 的時間序列；asof 回傳 ts 當下（含）最後一筆。"""

    def __init__(self, rows: list):
        rows = sorted(rows)
        self.ts, self.v = [r[0] for r in rows], [r[1] for r in rows]

    def asof(self, ts: int) -> float | None:
        i = bisect.bisect_right(self.ts, ts) - 1
        return self.v[i] if i >= 0 else None


def load_macro(folder: Path) -> dict[str, Macro]:
    return {p.stem: Macro(json.loads(p.read_text(encoding='utf-8'))) for p in folder.glob('*.json')}


def _q(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[int(p * (len(s) - 1))]


def crowd_avg(fund: Funding, prem: Macro, t: int) -> tuple[float, float] | None:
    """(t−7 天, t] 的平均資金費率、已收盤的溢價指數日 K 平均（日 K 在開盤後 1 天收盤）。"""
    a = bisect.bisect_right(fund.ts, t - W)
    b = bisect.bisect_right(fund.ts, t)
    i, j = bisect.bisect_left(prem.ts, t - W - D), bisect.bisect_right(prem.ts, t - D)
    if b - a < 15 or j - i < 5:
        return None
    return st.fmean(fund.rate[a:b]), st.fmean(prem.v[i:j])


def fa_parts(m: dict[str, Macro], fund: Funding, t: int) -> list[int] | None:
    """F1～F5（DESIGN 6.8 的表）；任何一項缺資料就 None。"""
    now = crowd_avg(fund, m['premium_ethusdt'], t)
    hist = [crowd_avg(fund, m['premium_ethusdt'], t - k * W) for k in range(1, 53)]
    if now is None or any(h is None for h in hist):
        return None
    hi = [_q([h[x] for h in hist], 0.8) for x in (0, 1)]
    lo = [_q([h[x] for h in hist], 0.2) for x in (0, 1)]
    f1 = -1 if now[0] > hi[0] and now[1] > hi[1] else 1 if now[0] < lo[0] and now[1] < lo[1] else 0

    def chg(name: str) -> float | None:
        a, b = m[name].asof(t - FRED_LAG), m[name].asof(t - FRED_LAG - 4 * W)
        return None if a is None or b is None else a - b

    def net(ts: int) -> float | None:
        x = [m[k].asof(ts) for k in ('WALCL', 'WTREGEN', 'RRPONTSYD')]
        return None if None in x else x[0] - x[1] - x[2] * 1000      # 百萬美元；逆回購單位是十億
    n1, n0 = net(t - FRED_LAG), net(t - FRED_LAG - 4 * W)
    c = [chg('stablecoins'), None if n1 is None or n0 is None else n1 - n0, chg('DTWEXBGS'), chg('DFII10')]
    if None in c:
        return None
    return [f1, 1 if c[0] > 0 else -1, 1 if c[1] > 0 else -1, -1 if c[2] > 0 else 1, -1 if c[3] > 0 else 1]


def fa_signal(m: dict[str, Macro], fund: Funding, t: int) -> int | None:
    parts = fa_parts(m, fund, t)
    if parts is None:
        return None
    s = st.fmean(parts)
    return 1 if s > 0.2 + 1e-9 else -1 if s < -0.2 - 1e-9 else 0


def week_ret(eth: Series, fund: Funding, t: int) -> tuple[float, float] | None:
    """(價格漲跌, (t, t+7 天] 結算的資金費率合計)。"""
    a, b = price(eth, t), price(eth, t + W)
    return None if a is None or b is None else (b / a - 1, fund.between(t, t + W))


def weekly(dirs: list[tuple[int, int]], eth: Series, fund: Funding, fee: float) -> list[tuple[int, int, float, float]]:
    """[(時點, 方向)] → [(時點, 方向, 淨報酬, 未扣手續費的報酬)]；手續費按部位變化收，缺一週就重新從空手算。"""
    out, prev, last = [], 0, None
    for t, d in dirs:
        r = week_ret(eth, fund, t)
        if r is None:
            prev, last = 0, None
            continue
        if last is not None and t - last != W:
            prev = 0
        gross = d * r[0] - d * r[1]
        out.append((t, d, gross - abs(d - prev) * fee / 2, gross))
        prev, last = d, t
    return out


def max_dd(nets: list[float]) -> float:
    eq = peak = 1.0
    dd = 0.0
    for x in nets:
        eq *= 1 + x
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    return dd


def evaluate(rows, eth: Series, fund: Funding, mid: int, pct: float, seed: int = 0) -> dict:
    """每週淨報酬的平均、t、前後兩半、最大回撤（和一直做多合約比）、隨機（同樣多空組成打亂順序）的百分位。
    隨機那一組不另外算手續費，改扣策略的平均手續費（打亂順序會換手更多，不讓策略因此佔便宜）。"""
    n = len(rows)
    out = {'n': n, 'n_long': sum(d > 0 for _, d, _, _ in rows), 'n_short': sum(d < 0 for _, d, _, _ in rows)}
    if n < 10:
        return out
    nets = [x for _, _, x, _ in rows]
    sd = st.stdev(nets)
    out['net'] = st.fmean(nets)
    out['t'] = 0.0 if sd == 0 else out['net'] / (sd / n ** 0.5)
    halves = [[x for t, _, x, _ in rows if (t < mid) == first] for first in (True, False)]
    out['half'] = [st.fmean(h) if h else 0.0 for h in halves]
    out['dd'] = max_dd(nets)
    hold = [g for _, _, _, g in weekly([(t, 1) for t, _, _, _ in rows], eth, fund, 0.0)]
    out['hold_net'], out['hold_dd'] = st.fmean(hold), max_dd(hold)
    cost = st.fmean(g - x for _, _, x, g in rows)
    rets = [(r[0], r[1]) for r in (week_ret(eth, fund, t) for t, _, _, _ in rows)]
    rng, dirs, means = random.Random(seed), [d for _, d, _, _ in rows], []
    for _ in range(RANDOM_RUNS):
        p = dirs[:]
        rng.shuffle(p)
        means.append(st.fmean(d * (a - f) for d, (a, f) in zip(p, rets)) - cost)
    out['rand'] = sorted(means)[int(RANDOM_RUNS * pct)]
    return out


def dev_passed(m: dict) -> bool:
    return ('net' in m and m['net'] > 0 and m['t'] >= MIN_T and m['net'] > m['rand'] and min(m['half']) > 0
            and m['dd'] > m['hold_dd'])


def exam_passed(m: dict) -> bool:
    return 'net' in m and m['net'] > 0 and m['net'] > m['rand'] and min(m['half']) > 0


# ---------- 波動預測 ----------

def vol_rows(eth: Series, start: int, end: int) -> list[tuple[int, float, float, float]]:
    """每週一：(時點, EWMA 預測 7 天變異數, 過去 30 天沿用, 實際 7 天變異數)。日報酬用台北日界。"""
    days = list(range(start // D * D + 16 * H - 400 * D, end + 8 * D, D))
    r = {}
    for a, b in zip(days, days[1:]):
        pa, pb = price(eth, a), price(eth, b)
        if pa is not None and pb is not None:
            r[b] = pb / pa - 1
    var, seen, out = None, [], []
    ewma = {}
    for t in days:
        if t not in r:
            var, seen = None, []
            continue
        seen.append(r[t] ** 2)
        if var is None:
            if len(seen) >= 30:
                var = st.fmean(seen[-30:])
            continue
        var = LAMBDA * var + (1 - LAMBDA) * r[t] ** 2
        ewma[t] = var
    for t in mondays(start, end):
        past = [r.get(t - k * D) for k in range(30)]
        fut = [r.get(t + k * D) for k in range(1, 8)]
        if t not in ewma or None in past or None in fut:
            continue
        rv = sum(x * x for x in fut)
        if rv > 0:
            out.append((t, 7 * ewma[t], 7 * st.fmean(x * x for x in past), rv))
    return out


def qlike(f: float, rv: float) -> float:
    return rv / f - math.log(rv / f) - 1


def vol_eval(rows, mid: int) -> dict:
    e = [(t, qlike(f, rv), qlike(b, rv)) for t, f, b, rv in rows]
    out = {'n': len(e)}
    if not e:
        return out
    out['ewma'], out['base'] = st.fmean(x for _, x, _ in e), st.fmean(y for _, _, y in e)
    out['half'] = [[st.fmean(v) for v in zip(*[(x, y) for t, x, y in e if (t < mid) == first])] for first in (True, False)]
    return out


def vol_passed(m: dict) -> bool:
    return m['n'] >= 50 and m['ewma'] < m['base'] and all(h[0] < h[1] for h in m['half'])


# ---------- 研究期與考卷 ----------

def ta_dirs(name: str, eth: Series, start: int, end: int):
    return [(t, d) for t in mondays(start, end) if (d := ta_signal(name, eth, t)) is not None]


def fa_dirs(m, fund: Funding, start: int, end: int):
    return [(t, d) for t in mondays(start, end) if (d := fa_signal(m, fund, t)) is not None]


def run_dev(eth: Series, fund: Funding, m: dict) -> dict:
    res = {n: evaluate(weekly(ta_dirs(n, eth, *DEV), eth, fund, FEE), eth, fund, DEV_MID, 0.95) for n in TA_VARIANTS}
    res['fundamental'] = evaluate(weekly(fa_dirs(m, fund, *DEV), eth, fund, FEE), eth, fund, DEV_MID, 0.95)
    vol = vol_eval(vol_rows(eth, *DEV), DEV_MID)
    passed = {n: dev_passed(x) for n, x in res.items()} | {'vol_ewma': vol_passed(vol)}
    return {'metrics': res, 'vol': vol, 'passed': passed}


def pick_ta(res: dict) -> str | None:
    ok = [n for n in TA_VARIANTS if res['passed'][n]]
    return max(ok, key=lambda n: res['metrics'][n]['t'], default=None)


def run_exam(res: dict, eth: Series, spot: Series, fund: Funding, m: dict, path: Path, commit: str) -> dict:
    """研究期通過的才考，各考一次；結果寫進 path，已經考過就拒絕。"""
    if path.exists():
        raise RuntimeError('考卷已經考過')
    rec = {'commit': commit, 'at': datetime.now(timezone.utc).isoformat(), 'exams': {}}
    nofund = Funding([])
    if (name := pick_ta(res)):
        rows = weekly(ta_dirs(name, spot, *EXAM_TA), spot, nofund, EXAM_FEE)
        x = evaluate(rows, spot, nofund, rows[len(rows) // 2][0], 0.90)
        rec['exams'][name] = {**x, 'passed': exam_passed(x)}
    if res['passed']['vol_ewma']:
        rows = vol_rows(spot, *EXAM_TA)
        x = vol_eval(rows, rows[len(rows) // 2][0])
        rec['exams']['vol_ewma'] = {**x, 'passed': vol_passed(x)}
    if res['passed']['fundamental']:
        rows = weekly(fa_dirs(m, fund, *EXAM_FA), eth, fund, FEE)
        x = evaluate(rows, eth, fund, rows[len(rows) // 2][0], 0.90)
        rec['exams']['fundamental'] = {**x, 'passed': exam_passed(x)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding='utf-8')
    return rec


def report(res: dict, when: str, exam: dict | None = None) -> str:
    lines = ['# 第十七輪：技術面與基本面研究 Agent（研究期 2021-01-01～2023-12-31，Binance ETHUSDT 合約）', '',
             f'產生時間 {when}。每週一台北 00:00 判斷、持有 7 天，1 倍可多可空；成本：部位變化 × 來回 {FEE:.2%} 的一半＋實際資金費率。', '',
             f'方向通過：每週平均淨報酬 > 0、t ≥ {MIN_T:g}、贏隨機（同多空組成打亂）第 95 百分位、前後半都 > 0、最大回撤淺於一直做多。', '',
             '| 組合 | 週數 | 多／空 | 每週淨報酬 | t | 前半／後半 | 隨機95 | 最大回撤（一直做多） | 過 |', '|---|---|---|---|---|---|---|---|---|']
    for n, x in res['metrics'].items():
        if 'net' not in x:
            lines.append(f'| {n} | {x["n"]} | – | – | – | – | – | – | ✗ |')
            continue
        lines.append(f'| {n} | {x["n"]} | {x["n_long"]}／{x["n_short"]} | {x["net"]:+.3%} | {x["t"]:+.2f} | '
                     f'{x["half"][0]:+.3%}／{x["half"][1]:+.3%} | {x["rand"]:+.3%} | {x["dd"]:.1%}（{x["hold_dd"]:.1%}） | '
                     f'{"✓" if res["passed"][n] else "✗"} |')
    v = res['vol']
    lines += ['', f'波動預測（QLIKE，越低越好）：EWMA {v["ewma"]:.4f}、沿用 30 天 {v["base"]:.4f}；'
              f'前半 {v["half"][0][0]:.4f}／{v["half"][0][1]:.4f}、後半 {v["half"][1][0]:.4f}／{v["half"][1][1]:.4f}；'
              f'{v["n"]} 週 → {"✓" if res["passed"]["vol_ewma"] else "✗"}']
    ok = [n for n, p in res['passed'].items() if p]
    lines += ['', '## 結論', '', f'研究期通過：{"、".join(ok) if ok else "沒有"}。']
    if exam:
        lines += ['', '## 考卷', '', '```', json.dumps(exam, ensure_ascii=False, indent=2), '```']
    return '\n'.join(lines) + '\n'
