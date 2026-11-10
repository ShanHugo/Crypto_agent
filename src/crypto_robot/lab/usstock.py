"""第二十六輪：美股合約多空排序（DESIGN 6.22，2026-11-10 營運者同意；規則在 6.3 第二十六輪）。**看過結果後不改**。

股票池是幣安美股合約對應的 153 檔股票（data/usstock/universe.json，2026-11-10 凍結），用 Yahoo 日 K（還原權值）回測：
- 週五（或那週最後一個交易日）收盤後算特徵，下週第一個交易日開盤價進場，抱到再下週第一個交易日開盤；
- 前 20% 做多、後 20% 做空，等權重，多空金額相同（合計 1 倍）；
- 成本：每邊 0.05%（照實際換手）＋資金費率不分多空每 8 小時 0.01%（一週 21 次，按 1 倍總曝險）。
研究統計用 float（不碰交易路徑）。模型訓練在 scripts/usstock_train.py（_vendor/ml_venv）。
"""
from __future__ import annotations

import bisect
import csv
import json
import math
import random
import statistics as st
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from crypto_robot.lab.metrics import t_stat
from crypto_robot.lab.xsec import percentile, rank

FRAC, MIN_UNI, AGE_DAYS = 0.2, 30, 365
COST, STRESS = 0.0005, 0.0015          # 每邊；壓力測試每邊再多 0.1%
FUND_WEEK = 0.0001 * 21                # 每 8 小時 0.01%，一週 21 次
FIRST_TRAIN = date(2005, 1, 3)         # 模型第一次訓練用 2005～2011
DEV = (date(2012, 1, 2), date(2019, 12, 30))
DEV_MID = date(2016, 1, 4)
EXAM = (date(2020, 1, 6), date(2025, 12, 29))
EXAM_MID = date(2023, 1, 2)
GROUPS = ['mom', 'rev', 'gbm', 'blend']
CONTROL = 'blend_shuf'
MODELS = {'gbm': ['gbm'], 'blend': ['gbm', 'ridge'], 'blend_shuf': ['gbm_shuf', 'ridge_shuf']}
MIN_T, MAX_DD, MAX_BETA, RUNS, SEED = 3.0, -0.30, 0.3, 1000, 100
FEATURES = ['ret_1w', 'ret_4w', 'ret_12w', 'ret_26w', 'mom_12_1', 'vol_12w', 'dv_chg', 'dist_high']


@dataclass(frozen=True)
class Week:
    monday: date
    decision: date      # 最後一個看得到的收盤
    entry: date         # 進場（開盤）
    exit: date          # 出場（開盤）


class Stock:
    """一檔股票的日 K。開盤價用還原收盤的比例調整，和還原收盤同一把尺。"""

    def __init__(self, sym: str, rows: list[list]):
        self.sym = sym
        self.days = [date.fromisoformat(r[0]) for r in rows]
        self.close = [r[5] for r in rows]                               # 還原收盤
        self.open = [r[1] * r[5] / r[4] for r in rows]                  # 還原開盤
        self.dv = [r[4] * r[6] for r in rows]                           # 成交金額（未還原）
        self.at = {d: i for i, d in enumerate(self.days)}

    def idx(self, d: date) -> int | None:
        return self.at.get(d)


def load(folder: Path, universe: Path) -> tuple[dict[str, Stock], Stock]:
    uni = json.loads(universe.read_text(encoding='utf-8'))
    out = {}
    for s in uni['stocks']:
        p = folder / f"{s['ticker']}.json"
        if p.exists():
            out[s['ticker']] = Stock(s['ticker'], json.loads(p.read_text(encoding='utf-8')))
    spy = Stock('SPY', json.loads((folder / 'SPY.json').read_text(encoding='utf-8')))
    return out, spy


def weeks(cal: list[date], a: date, b: date) -> list[Week]:
    """週一 ≥ a 且週一＋7 天 ≤ b 的每一週；進出場是週一當天或之後第一個交易日，決策日是進場前一個交易日。"""
    out, m = [], a - timedelta(days=a.weekday())
    if m < a:
        m += timedelta(days=7)
    while m + timedelta(days=7) <= b:
        i, j = bisect.bisect_left(cal, m), bisect.bisect_left(cal, m + timedelta(days=7))
        if 0 < i < len(cal) and j < len(cal):
            out.append(Week(m, cal[i - 1], cal[i], cal[j]))
        m += timedelta(days=7)
    return out


def _ret(xs: list[float], i: int, n: int) -> float:
    return xs[i] / xs[i - n] - 1


def features(s: Stock, spy: Stock, d: date) -> dict[str, float] | None:
    """決策日 d 收盤看得到的特徵；需要 252 個交易日歷史。報酬類都減掉 SPY 同一段日期的報酬。"""
    i = s.idx(d)
    if i is None or i < 252:
        return None
    c = s.close

    def rel(n: int, skip: int = 0) -> float | None:
        a, b = s.days[i - n], s.days[i - skip]
        ia, ib = spy.idx(a), spy.idx(b)
        if ia is None or ib is None:
            return None
        return (c[i - skip] / c[i - n] - 1) - (spy.close[ib] / spy.close[ia] - 1)

    vals = {'ret_1w': rel(5), 'ret_4w': rel(20), 'ret_12w': rel(60), 'ret_26w': rel(130), 'mom_12_1': rel(252, 20)}
    if any(v is None for v in vals.values()):
        return None
    logs = [math.log(c[k] / c[k - 1]) for k in range(i - 59, i + 1)]
    recent, before = st.fmean(s.dv[i - 19:i + 1]), st.fmean(s.dv[i - 39:i - 19])
    vals['vol_12w'] = st.pstdev(logs)
    vals['dv_chg'] = math.log(recent / before) if recent > 0 and before > 0 else 0.0
    vals['dist_high'] = c[i] / max(c[i - 251:i + 1]) - 1
    return vals


def eligible(s: Stock, w: Week) -> bool:
    """那週可以交易：上市滿 1 年，決策日、進場日、出場日都有 K 線。"""
    return (s.days[0] <= w.decision - timedelta(days=AGE_DAYS) and s.idx(w.decision) is not None
            and s.idx(w.entry) is not None and s.idx(w.exit) is not None)


def hold(s: Stock, w: Week) -> tuple[float, list[float]]:
    """(出場開盤 ÷ 進場開盤, 持有期間每天收盤 ÷ 進場開盤)。"""
    i, j = s.idx(w.entry), s.idx(w.exit)
    o = s.open[i]
    return s.open[j] / o, [s.close[k] / o for k in range(i, j)]


def write_panel(path: Path, stocks: dict[str, Stock], spy: Stock, a: date = FIRST_TRAIN, b: date = EXAM[1]) -> dict:
    """每週每檔一列：週一、代號、下週報酬、答案排名、mom、rev、8 項特徵排名（每週內 -0.5～+0.5）。"""
    rows, n_weeks = [], 0
    for w in weeks(spy.days, a, b):
        cand = {}
        for sym, s in stocks.items():
            if eligible(s, w):
                f = features(s, spy, w.decision)
                if f is not None:
                    cand[sym] = (f, hold(s, w)[0] - 1)
        if not cand:
            continue
        n_weeks += 1
        names = sorted(cand)
        label = rank([cand[x][1] for x in names])
        cols = [rank([cand[x][0][k] for x in names]) for k in FEATURES]
        for n, sym in enumerate(names):
            f, r = cand[sym]
            rows.append([w.monday.isoformat(), sym, r, label[n], f['mom_12_1'], -f['ret_1w'], *(col[n] for col in cols)])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as fh:
        wr = csv.writer(fh)
        wr.writerow(['monday', 'sym', 'ret', 'label', 'mom', 'rev', *FEATURES])
        wr.writerows(rows)
    return {'weeks': n_weeks, 'rows': len(rows)}


def read_panel(path: Path) -> dict[date, dict[str, dict[str, float]]]:
    out: dict[date, dict[str, dict[str, float]]] = {}
    with path.open(encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            out.setdefault(date.fromisoformat(r['monday']), {})[r['sym']] = {'mom': float(r['mom']), 'rev': float(r['rev'])}
    return out


def read_preds(path: Path) -> dict[str, dict[date, dict[str, float]]]:
    out: dict[str, dict[date, dict[str, float]]] = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            r = json.loads(line)
            out.setdefault(r['model'], {}).setdefault(date.fromisoformat(r['monday']), {})[r['sym']] = r['pred']
    return out


def select(score: dict[str, float]) -> tuple[list[str], list[str]]:
    names = sorted(score, key=lambda s: (-score[s], s))
    k = max(1, round(len(names) * FRAC))
    return names[:k], names[-k:]


def score_for(group: str, uni: dict[str, dict[str, float]], preds: dict, monday: date) -> dict[str, float]:
    if group in ('mom', 'rev'):
        return {s: v[group] for s, v in uni.items()}
    models = MODELS[group]
    names = sorted(s for s in uni if all(s in preds.get(m, {}).get(monday, {}) for m in models))
    if not names:
        return {}
    cols = [rank([preds[m][monday][s] for s in names]) for m in models]
    return {s: st.fmean(col[i] for col in cols) for i, s in enumerate(names)}


def picks_for(group: str, panel: dict, preds: dict, ws: list[Week]) -> list[tuple[Week, list[str], list[str]]]:
    out = []
    for w in ws:
        uni = panel.get(w.monday, {})
        score = score_for(group, uni, preds, w.monday)
        if len(score) >= MIN_UNI:
            out.append((w, *select(score)))
    return out


def step(stocks: dict[str, Stock], w: Week, longs: list[str], shorts: list[str], prev: dict[str, float], cost: float) -> dict:
    """一週：只交易和上週不同的部分。每檔 1/(2k)，合計 1 倍；資金費率按 1 倍總曝險。"""
    k = len(longs)
    tgt = {s: 1 / (2 * k) for s in longs} | {s: -1 / (2 * k) for s in shorts}
    turnover = sum(abs(tgt.get(s, 0.0) - prev.get(s, 0.0)) for s in tgt.keys() | prev.keys())
    fee = turnover * cost
    long_pnl = short_pnl = 0.0
    after, paths = {}, {}
    for s, wgt in tgt.items():
        ratio, path = hold(stocks[s], w)
        paths[s] = path
        pnl = wgt * (ratio - 1)
        if wgt > 0:
            long_pnl += pnl
        else:
            short_pnl += pnl
        after[s] = wgt * ratio
    r = max(long_pnl + short_pnl - fee - FUND_WEEK, -1.0)
    return {'ret': r, 'fee': fee, 'turnover': turnover, 'long': long_pnl, 'short': short_pnl, 'tgt': tgt, 'paths': paths,
            'weights': {s: x / (1 + r) for s, x in after.items()} if r > -1 else {}}


def simulate(stocks: dict[str, Stock], picks: list, cost: float = COST) -> dict:
    prev: dict[str, float] = {}
    equity, marks, rows = 1.0, [1.0], []
    for w, longs, shorts in picks:
        if equity <= 0:
            rows.append({'monday': w.monday, 'ret': 0.0, 'turnover': 0.0, 'long': 0.0, 'short': 0.0})
            continue
        s = step(stocks, w, longs, shorts, prev, cost)
        n = min(len(p) for p in s['paths'].values())
        for d in range(n):                                  # 每天收盤記權益（算回撤）
            pnl = sum(wgt * (s['paths'][x][d] - 1) for x, wgt in s['tgt'].items())
            marks.append(max(equity * (1 - s['fee'] + pnl), 0.0))
        equity *= 1 + s['ret']
        marks.append(equity)
        prev = s['weights']
        rows.append({'monday': w.monday, **{k: s[k] for k in ('ret', 'turnover', 'long', 'short')}})
    return {'rows': rows, 'marks': marks, 'equity': equity}


def drawdown(marks: list[float]) -> float:
    peak, worst = 0.0, 0.0
    for v in marks:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1)
    return worst


def beta(xs: list[float], ys: list[float]) -> float:
    """xs 對 ys（SPY）的迴歸斜率。"""
    if len(xs) < 2:
        return 0.0
    mx, my = st.fmean(xs), st.fmean(ys)
    vy = sum((y - my) ** 2 for y in ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vy if vy else 0.0


def spy_week(spy: Stock, w: Week) -> float:
    return hold(spy, w)[0] - 1


def metrics(sim: dict, spy: Stock, ws: dict[date, Week], mid: date) -> dict:
    rows = sim['rows']
    rets = [r['ret'] for r in rows]
    first, second = [r['ret'] for r in rows if r['monday'] < mid], [r['ret'] for r in rows if r['monday'] >= mid]
    n = len(rets) or 1
    return {'weeks': len(rets), 'mean': sum(rets) / n, 't': t_stat(rets),
            'h1': st.fmean(first) if first else 0.0, 'h2': st.fmean(second) if second else 0.0,
            'total': sim['equity'] - 1, 'dd': drawdown(sim['marks']), 'turnover': sum(r['turnover'] for r in rows) / n,
            'long': sum(r['long'] for r in rows) / n, 'short': sum(r['short'] for r in rows) / n,
            'beta': beta(rets, [spy_week(spy, ws[r['monday']]) for r in rows]), 'worst': min(rets) if rets else 0.0}


def random_means(stocks: dict[str, Stock], panel: dict, ws: list[Week], runs: int = RUNS, seed: int = SEED) -> list[float]:
    """每週從同一批股票隨機挑多空（同檔數、同成本），回傳每次的每週平均報酬。"""
    rng = random.Random(seed)
    weeks_uni = [(w, sorted(panel[w.monday])) for w in ws if len(panel.get(w.monday, {})) >= MIN_UNI]
    out = []
    for _ in range(runs):
        prev, total, equity = {}, 0.0, 1.0
        for w, names in weeks_uni:
            if equity <= 0:
                continue
            k = max(1, round(len(names) * FRAC))
            pick = rng.sample(names, 2 * k)
            s = step(stocks, w, pick[:k], pick[k:], prev, COST)
            total += s['ret']
            equity *= 1 + s['ret']
            prev = s['weights']
        out.append(total / len(weeks_uni))
    return sorted(out)


def dev_checks(m: dict, p95: float, stress_mean: float) -> dict[str, bool]:
    return {'每週平均 > 0': m['mean'] > 0, 't ≥ 3': m['t'] >= MIN_T, '前後半都 > 0': m['h1'] > 0 and m['h2'] > 0,
            '贏隨機第 95 百分位': m['mean'] > p95, '回撤不深於 -30%': m['dd'] >= MAX_DD,
            'beta 在 ±0.3 內': abs(m['beta']) <= MAX_BETA, '每邊多扣 0.1% 仍 > 0': stress_mean > 0}


def exam_checks(m: dict, p90: float) -> dict[str, bool]:
    return {'每週平均 > 0': m['mean'] > 0, '贏隨機第 90 百分位': m['mean'] > p90,
            '前後半都 > 0': m['h1'] > 0 and m['h2'] > 0, '回撤不深於 -30%': m['dd'] >= MAX_DD}


def run_dev(stocks: dict[str, Stock], spy: Stock, panel: dict, preds: dict) -> dict:
    ws = weeks(spy.days, *DEV)
    by = {w.monday: w for w in ws}
    rand = random_means(stocks, panel, ws)
    p95 = percentile(rand, 0.95)
    out = {'weeks': len(ws), 'rand_mean': st.fmean(rand), 'p95': p95, 'groups': {}, 'control': None}
    for g in GROUPS:
        picks = picks_for(g, panel, preds, ws)
        if not picks:
            continue
        m = metrics(simulate(stocks, picks), spy, by, DEV_MID)
        stress = metrics(simulate(stocks, picks, STRESS), spy, by, DEV_MID)['mean']
        checks = dev_checks(m, p95, stress)
        out['groups'][g] = {**m, 'stress': stress, 'checks': checks, 'passed': all(checks.values())}
    picks = picks_for(CONTROL, panel, preds, ws)
    if picks:
        out['control'] = metrics(simulate(stocks, picks), spy, by, DEV_MID)
    out['leak'] = out['control'] is not None and abs(out['control']['t']) >= MIN_T
    passed = {g: v for g, v in out['groups'].items() if v['passed']}
    out['pick'] = None if out['leak'] or not passed else max(passed, key=lambda g: passed[g]['t'])
    return out


def run_exam(stocks: dict[str, Stock], spy: Stock, panel: dict, preds: dict, group: str) -> dict:
    ws = weeks(spy.days, *EXAM)
    by = {w.monday: w for w in ws}
    p90 = percentile(random_means(stocks, panel, ws), 0.90)
    m = metrics(simulate(stocks, picks_for(group, panel, preds, ws)), spy, by, EXAM_MID)
    checks = exam_checks(m, p90)
    return {'group': group, **m, 'p90': p90, 'checks': checks, 'passed': all(checks.values())}


def _pct(x: float, digits: int = 2) -> str:
    return f'{x * 100:+.{digits}f}%'


def _row(name: str, m: dict) -> str:
    return (f"| `{name}` | {_pct(m['mean'])} | {m['t']:.2f} | {_pct(m['h1'])}／{_pct(m['h2'])} | {_pct(m['total'], 1)} | "
            f"{_pct(m['dd'], 1)} | {m['beta']:+.2f} | {_pct(m['long'])}／{_pct(m['short'])} | {m['turnover']:.2f} | {m['weeks']} |")


HEAD = ['| 組 | 每週平均 | t | 前半／後半 | 總報酬 | 最大回撤 | 對 SPY beta | 多方／空方每週 | 週換手 | 週數 |',
        '|---|---|---|---|---|---|---|---|---|---|']


def report(res: dict, when: str, exam: dict | None = None) -> str:
    lines = [f'# 第二十六輪 美股合約多空排序：研究期（{when}）', '',
             f'研究期 {DEV[0]}～{DEV[1]}（{res["weeks"]} 週，前後半在 {DEV_MID} 切開）。成本每邊 {COST:.2%}＋資金費率每週 {FUND_WEEK:.2%}。',
             f'隨機挑多空 {RUNS} 次：平均每週 {_pct(res["rand_mean"])}，第 95 百分位 {_pct(res["p95"])}。', '', *HEAD]
    lines += [_row(g, m) for g, m in res['groups'].items()]
    if res['control']:
        lines.append(_row(CONTROL + '（對照）', res['control']))
    lines += ['', '| 組 | ' + ' | '.join(next(iter(res['groups'].values()))['checks']) + ' | 每邊多扣 0.1% 後 | 結果 |',
              '|---|' + '---|' * (len(next(iter(res['groups'].values()))['checks']) + 2)]
    for g, m in res['groups'].items():
        lines.append(f'| `{g}` | ' + ' | '.join('✅' if v else '❌' for v in m['checks'].values())
                     + f" | {_pct(m['stress'])} | {'通過' if m['passed'] else '沒過'} |")
    lines += ['', f"對照 `{CONTROL}` t {res['control']['t']:.2f}：{'**|t| ≥ 3，程式可能有問題，全部不算通過**' if res['leak'] else '沒有學到東西，程式沒有看到問題'}。"
              if res['control'] else '對照沒有預測，沒算。',
              f"結論：{'`' + res['pick'] + '` 進考卷（只考一次）' if res['pick'] else '沒有組通過，不進考卷'}。"]
    if exam:
        lines += ['', f"## 考卷 {EXAM[0]}～{EXAM[1]}：`{exam['group']}`", '', *HEAD, _row(exam['group'], exam), '',
                  f"隨機第 90 百分位 {_pct(exam['p90'])}。" + '、'.join(f"{k} {'✅' if v else '❌'}" for k, v in exam['checks'].items()),
                  f"結果：**{'通過' if exam['passed'] else '沒過'}**。"]
    return '\n'.join(lines) + '\n'
