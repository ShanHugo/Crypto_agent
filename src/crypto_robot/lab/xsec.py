"""第二十一輪：多幣排序模型（DESIGN.md 6.12，2026-11-06 營運者同意）。**看過結果後不改**。

這裡只做主環境的部分（純 Python，不需要 numpy）：
- 每週選成交額前 50 大的幣，算 16 項特徵與答案，寫成 CSV，給 scripts/xsec_train.py（_vendor/ml_venv）每月重訓；
- 讀預測結果，模擬 10 多 10 空，照事先訂的標準判定。
研究統計用 float（不碰交易路徑）。
"""
from __future__ import annotations

import bisect
import csv
import json
import random
import statistics as st
from dataclasses import dataclass
from pathlib import Path

from crypto_robot.lab.hourly import ms
from crypto_robot.lab.metrics import t_stat

D = 86_400_000
W = 7 * D
TOP, FRAC, MIN_UNI, MIN_AGE, GAP = 50, 0.2, 20, 60, 7 * D
COST, STRESS = 0.001, 0.002             # 每邊：手續費 0.05%＋滑價 0.05%；成本加倍
FIRST_MONDAY = ms(2019, 9, 2)
END = ms(2026, 11, 1)
LAST_MONTH = '2026-10'
OFFSET = {'mon': 0, 'thu': 3 * D}
DEV = (ms(2022, 1, 3), ms(2024, 12, 30))
DEV_MID = ms(2023, 7, 1)
EXAM = (ms(2025, 1, 1), END)
EXAM_MID = ms(2025, 12, 1)
GROUPS = ['mom', 'ridge', 'gbm']
CONTROLS = ['ridge_shuf', 'gbm_shuf']
MIN_WEEKS, MIN_T, MAX_DD, RUNS, SEED = 130, 3.0, -0.30, 2000, 100
# 穩定幣、指數、黃金代幣；股票、商品、盤前合約由 exchangeInfo 的 contractType／underlyingType 排除（scripts/fetch_xsec.py）
EXCLUDE = {'USDCUSDT', 'DEFIUSDT', 'BTCDOMUSDT', 'FOOTBALLUSDT', 'BLUEBIRDUSDT', 'ALLUSDT', 'XAUTUSDT'}
FEATURES = ['ret_7d', 'ret_14d', 'ret_28d', 'ret_56d', 'ma_20', 'ma_50', 'vol_28d', 'beta_56d', 'max_28d',
            'dv_30d', 'dv_ratio', 'amihud_28d', 'age', 'fund_7d', 'fund_28d', 'taker_7d']


def rank(xs: list[float | None]) -> list[float]:
    """橫斷面排名，轉成 -0.5～+0.5；同值取平均，缺值 0（中位）。"""
    vals = sorted((x, i) for i, x in enumerate(xs) if x is not None)
    n, out = len(vals), [0.0] * len(xs)
    if n < 2:
        return out
    i = 0
    while i < n:
        j = i
        while j + 1 < n and vals[j + 1][0] == vals[i][0]:
            j += 1
        r = (i + j) / 2 / (n - 1) - 0.5
        for k in range(i, j + 1):
            out[vals[k][1]] = r
        i = j + 1
    return out


@dataclass(frozen=True)
class Hold:
    ratio: float            # 持有結束的價格 ÷ 進場價（下架：最後收盤）
    fund: float             # 每 1 單位名目部位要付的資金費率合計（按當時部位大小）
    path: tuple             # 每天收盤的 (ratio, fund)，算回撤用
    delisted: bool


class Coin:
    """一個幣的日 K 與資金費率。中斷超過 7 天算新幣（段落重新起算）。"""

    def __init__(self, sym: str, k: list, f: list):
        self.sym = sym
        k = [b for b in k if b[1] > 0 and b[2] > 0]
        self.t = [b[0] for b in k]
        self.o = [b[1] for b in k]
        self.c = [b[2] for b in k]
        self.qv = [b[3] for b in k]
        self.tb = [b[4] for b in k]
        self.pos = {t: i for i, t in enumerate(self.t)}
        self.ft = [x[0] for x in f]
        self.fr = [x[1] for x in f]
        self.seg, self.r = [], []
        for i, t in enumerate(self.t):
            new = i == 0 or t - self.t[i - 1] > GAP
            self.seg.append(t if new else self.seg[-1])
            self.r.append(None if new else self.c[i] / self.c[i - 1] - 1)

    def span(self, t: int, days: int) -> tuple[int, int]:
        """[t − days 天, t) 的 K 線索引範圍。"""
        return bisect.bisect_left(self.t, t - days * D), bisect.bisect_left(self.t, t)

    def eligible(self, t: int) -> bool:
        """週日那根存在（當時還在交易）、這一段上市滿 60 天。"""
        i = self.pos.get(t - D)
        return i is not None and self.seg[i] <= t - MIN_AGE * D

    def fund_sum(self, a: int, b: int) -> float:
        return sum(self.fr[bisect.bisect_left(self.ft, a):bisect.bisect_left(self.ft, b)])

    def close_at(self, day: int) -> float:
        return self.c[bisect.bisect_right(self.t, day) - 1]

    def features(self, t: int, market: dict[int, float]) -> list[float | None]:
        """決策時點 t 已知的 16 項（只用 t 以前已收盤的 K 線與已結算的資金費率）。"""
        last = t - D
        c = self.c[self.pos[last]]
        ret = lambda n: c / self.close_at(last - n * D) - 1      # noqa: E731
        a7, b = self.span(t, 7)
        a20, a28, a30 = self.span(t, 20)[0], self.span(t, 28)[0], self.span(t, 30)[0]
        a50, a56 = self.span(t, 50)[0], self.span(t, 56)[0]
        r28 = [x for x in self.r[a28:b] if x is not None]
        pairs = [(self.r[i], market[self.t[i]]) for i in range(a56, b) if self.r[i] is not None and self.t[i] in market]
        beta = None
        if len(pairs) >= 2:
            ma, mb = st.fmean(p[0] for p in pairs), st.fmean(p[1] for p in pairs)
            var = sum((p[1] - mb) ** 2 for p in pairs)
            beta = sum((p[0] - ma) * (p[1] - mb) for p in pairs) / var if var else None
        ill = [abs(self.r[i]) / self.qv[i] for i in range(a28, b) if self.r[i] is not None and self.qv[i] > 0]
        qv7, qv28 = sum(self.qv[a7:b]), sum(self.qv[a28:b])
        return [ret(7), ret(14), ret(28), ret(56),
                c / st.fmean(self.c[a20:b]), c / st.fmean(self.c[a50:b]),
                st.pstdev(r28) if len(r28) >= 2 else None, beta, max(r28) if r28 else None,
                st.median(self.qv[a30:b]), qv7 / qv28 if qv28 else None, st.fmean(ill) if ill else None,
                (t - self.seg[self.pos[last]]) / D,
                self.fund_sum(t - 7 * D, t), self.fund_sum(t - 28 * D, t),
                sum(self.tb[a7:b]) / qv7 if qv7 else None]

    def hold(self, t: int) -> Hold:
        """t 進場持有 7 天。沒有 t 那根就用上一根收盤當進場價；後面沒有 K 線（中斷超過 7 天）就是下架，用最後收盤出場。"""
        first = self.pos.get(t)
        prev = bisect.bisect_left(self.t, t) - 1
        c0 = self.o[first] if first is not None else self.c[prev]
        ratio, fund, last, path = 1.0, 0.0, prev, []
        for day in range(t, t + W, D):
            j = self.pos.get(day)
            if j is not None:
                fund += ratio * self.fund_sum(day, day + D)      # 當天的費率按前一天收盤的部位算
                ratio, last = self.c[j] / c0, j
            path.append((ratio, fund))
        nxt = self.t[last + 1] if last + 1 < len(self.t) else None
        delisted = self.t[last] < t + 6 * D and (nxt is None or nxt - self.t[last] > GAP)
        return Hold(ratio, fund, tuple(path), delisted)


def load_coins(folder: Path) -> dict[str, Coin]:
    out = {}
    for p in sorted(folder.glob('*.json')):
        d = json.loads(p.read_text(encoding='utf-8'))
        if d['k'] and p.stem not in EXCLUDE:
            out[p.stem] = Coin(p.stem, d['k'], d['f'])
    return out


def market_returns(coins: dict[str, Coin]) -> dict[int, float]:
    """每天全部幣的等權重報酬（算 beta 用）。"""
    s: dict[int, list[float]] = {}
    for c in coins.values():
        for t, r in zip(c.t, c.r):
            if r is not None:
                s.setdefault(t, []).append(r)
    return {t: st.fmean(v) for t, v in s.items()}


def universe(coins: dict[str, Coin], t: int) -> list[Coin]:
    """t 當時在交易、上市滿 60 天的幣，過去 30 天成交額中位數前 50 名。"""
    scored = []
    for c in coins.values():
        if c.eligible(t):
            a, b = c.span(t, 30)
            scored.append((-st.median(c.qv[a:b]), c.sym, c))
    scored.sort(key=lambda x: x[:2])
    return [x[2] for x in scored[:TOP]]


def weeks(a: int, b: int, wd: str = 'mon') -> list[int]:
    """開始 ≥ a、7 天持有在 b 以前結束的週。"""
    t0 = FIRST_MONDAY + OFFSET[wd]
    t0 += max(0, (a - t0 + W - 1) // W) * W
    return list(range(t0, b - W + 1, W))


def label(c: Coin, t: int) -> float:
    h = c.hold(t)
    return h.ratio - 1 - h.fund


def write_panel(path: Path, coins: dict[str, Coin], wd: str) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    market = market_returns(coins)
    count, sizes = 0, {}
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['ts', 'sym', 'n', 'label'] + FEATURES)
        for t in weeks(FIRST_MONDAY, END, wd):
            uni = universe(coins, t)
            if len(uni) < MIN_UNI:
                continue
            ys = rank([label(c, t) for c in uni])
            cols = [rank(list(col)) for col in zip(*(c.features(t, market) for c in uni))]
            for i, c in enumerate(uni):
                w.writerow([t, c.sym, len(uni), round(ys[i], 6)] + [round(col[i], 6) for col in cols])
            count += 1
            sizes[t] = len(uni)
    return {'weeks': count, 'first': min(sizes), 'smallest_in_dev': min((n for t, n in sizes.items() if t >= DEV[0]), default=0)}


def read_panel(path: Path) -> dict[int, dict[str, tuple[float, float]]]:
    """週 → 幣 → (答案排名, 28 天報酬排名)。"""
    out: dict[int, dict[str, tuple[float, float]]] = {}
    with path.open(encoding='utf-8') as f:
        for r in csv.DictReader(f):
            out.setdefault(int(r['ts']), {})[r['sym']] = (float(r['label']), float(r['ret_28d']))
    return out


def read_preds(path: Path) -> dict[tuple[str, str], dict[int, dict[str, float]]]:
    """(模型, 星期) → 週 → 幣 → 預測。"""
    out: dict = {}
    if path.exists():
        with path.open(encoding='utf-8') as f:
            for line in f:
                r = json.loads(line)
                out.setdefault((r['model'], r['wd']), {}).setdefault(r['ts'], {})[r['sym']] = r['pred']
    return out


def select(score: dict[str, float]) -> tuple[list[str], list[str]]:
    """分數最高的 20% 做多、最低的 20% 做空；同分照代號。"""
    names = sorted(score, key=lambda s: (-score[s], s))
    k = max(1, round(len(names) * FRAC))
    return names[:k], names[-k:]


class Book:
    """模擬用的快取：同一個幣同一週的持有結果只算一次。"""

    def __init__(self, coins: dict[str, Coin]):
        self.coins, self.cache = coins, {}

    def hold(self, sym: str, t: int) -> Hold:
        key = (sym, t)
        if key not in self.cache:
            self.cache[key] = self.coins[sym].hold(t)
        return self.cache[key]


def step(book: Book, t: int, longs: list[str], shorts: list[str], prev: dict[str, float], cost: float) -> dict:
    """一週：只交易和上週不同的部分，回傳報酬、週末權重與明細。每個幣 1/(2k)，合計 1 倍。"""
    k = len(longs)
    tgt = {s: 1 / (2 * k) for s in longs} | {s: -1 / (2 * k) for s in shorts}
    turnover = sum(abs(tgt.get(s, 0.0) - prev.get(s, 0.0)) for s in tgt.keys() | prev.keys())
    fee = turnover * cost
    long_pnl, short_pnl, fund_pnl, extra, by, after = 0.0, 0.0, 0.0, 0.0, {}, {}
    for s, w in tgt.items():
        h = book.hold(s, t)
        pnl = w * (h.ratio - 1 - h.fund)
        by[s] = pnl
        fund_pnl -= w * h.fund
        if w > 0:
            long_pnl += pnl
        else:
            short_pnl += pnl
        if h.delisted:
            extra += abs(w * h.ratio) * 2 * cost             # 下架：出場成本加倍，之後這個幣不再持有
        else:
            after[s] = w * h.ratio
    r = max(long_pnl + short_pnl - fee - extra, -1.0)
    return {'ret': r, 'fee': fee, 'extra': extra, 'turnover': turnover, 'long': long_pnl, 'short': short_pnl, 'fund': fund_pnl,
            'by': by, 'tgt': tgt, 'weights': {s: x / (1 + r) for s, x in after.items()} if r > -1 else {}}


def simulate(book: Book, picks: list[tuple[int, list[str], list[str]]], cost: float = COST) -> dict:
    prev: dict[str, float] = {}
    equity, marks, rows = 1.0, [1.0], []
    for t, longs, shorts in picks:
        if equity <= 0:
            rows.append({'t': t, 'ret': 0.0, 'turnover': 0.0, 'long': 0.0, 'short': 0.0, 'fund': 0.0, 'by': {}})
            continue
        s = step(book, t, longs, shorts, prev, cost)
        for d in range(7):                                  # 每天收盤記權益
            pnl = sum(w * (book.hold(c, t).path[d][0] - 1 - book.hold(c, t).path[d][1]) for c, w in s['tgt'].items())
            marks.append(max(equity * (1 - s['fee'] + pnl), 0.0))
        equity *= 1 + s['ret']
        marks[-1] = equity
        prev = s['weights']
        rows.append({'t': t, **{k: s[k] for k in ('ret', 'turnover', 'long', 'short', 'fund', 'by')}})
    return {'rows': rows, 'marks': marks, 'equity': equity}


def drawdown(marks: list[float]) -> float:
    peak, worst = 0.0, 0.0
    for v in marks:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1)
    return worst


def metrics(sim: dict, mid: int) -> dict:
    rows = sim['rows']
    rets = [r['ret'] for r in rows]
    first, second = [r['ret'] for r in rows if r['t'] < mid], [r['ret'] for r in rows if r['t'] >= mid]
    by: dict[str, float] = {}
    for r in rows:
        for s, x in r['by'].items():
            by[s] = by.get(s, 0.0) + x
    top = max(by, key=by.get) if by else ''
    n = len(rets) or 1
    mean = sum(rets) / n
    return {'weeks': sum(1 for r in rows if r['by']), 'mean': mean, 't': t_stat(rets),
            'h1': st.fmean(first) if first else 0.0, 'h2': st.fmean(second) if second else 0.0,
            'total': sim['equity'] - 1, 'dd': drawdown(sim['marks']), 'turnover': sum(r['turnover'] for r in rows) / n,
            'long': sum(r['long'] for r in rows) / n, 'short': sum(r['short'] for r in rows) / n,
            'fund': sum(r['fund'] for r in rows) / n, 'top': top, 'drop_top': mean - by.get(top, 0.0) / n}


def picks_for(group: str, panel: dict, preds: dict, wd: str, ts: list[int]) -> list[tuple[int, list[str], list[str]]]:
    out = []
    for t in ts:
        uni = panel.get(t)
        if not uni:
            continue
        score = {s: v[1] for s, v in uni.items()} if group == 'mom' else preds.get((group, wd), {}).get(t, {})
        score = {s: x for s, x in score.items() if s in uni}
        if len(score) >= MIN_UNI:
            out.append((t, *select(score)))
    return out


def random_means(book: Book, panel: dict, ts: list[int], runs: int = RUNS, seed: int = SEED, cost: float = COST) -> list[float]:
    """每週從同一個選幣池隨機挑多空，同成本規則，回傳每次的每週平均報酬。"""
    rng = random.Random(seed)
    weeks_uni = [(t, sorted(panel[t])) for t in ts if panel.get(t)]
    out = []
    for _ in range(runs):
        prev, total, equity = {}, 0.0, 1.0
        for t, names in weeks_uni:
            if equity <= 0:
                continue
            k = max(1, round(len(names) * FRAC))
            pick = rng.sample(names, 2 * k)
            s = step(book, t, pick[:k], pick[k:], prev, cost)
            total += s['ret']
            equity *= 1 + s['ret']
            prev = s['weights']
        out.append(total / len(weeks_uni))
    return sorted(out)


def percentile(sorted_xs: list[float], q: float) -> float:
    return sorted_xs[min(len(sorted_xs) - 1, int(q * len(sorted_xs)))]


def spearman_ic(panel: dict, preds_one: dict, group: str, ts: list[int]) -> float:
    """每週「預測排名」與「答案排名」的相關係數平均（只報告，不是標準）。"""
    out = []
    for t in ts:
        uni = panel.get(t, {})
        score = {s: v[1] for s, v in uni.items()} if group == 'mom' else preds_one.get(t, {})
        names = [s for s in uni if s in score]
        if len(names) >= MIN_UNI:
            a, b = rank([score[s] for s in names]), rank([uni[s][0] for s in names])
            sa, sb = sum(x * x for x in a), sum(x * x for x in b)
            if sa and sb:
                out.append(sum(x * y for x, y in zip(a, b)) / (sa * sb) ** 0.5)
    return st.fmean(out) if out else 0.0


def dev_checks(m: dict, p95: float, stress_mean: float, thu_mean: float) -> dict[str, bool]:
    return {'有持倉 ≥ 130 週': m['weeks'] >= MIN_WEEKS, '每週平均 > 0': m['mean'] > 0, 't ≥ 3': m['t'] >= MIN_T,
            '前後半都 > 0': m['h1'] > 0 and m['h2'] > 0, '贏隨機第 95 百分位': m['mean'] > p95, '成本加倍仍 > 0': stress_mean > 0,
            '回撤不深於 -30%': m['dd'] >= MAX_DD, '拿掉最賺的幣仍 > 0': m['drop_top'] > 0, '週四起算仍 > 0': thu_mean > 0}


def exam_checks(m: dict, p90: float) -> dict[str, bool]:
    return {'每週平均 > 0': m['mean'] > 0, '贏隨機第 90 百分位': m['mean'] > p90, '前後半都 > 0': m['h1'] > 0 and m['h2'] > 0,
            '回撤不深於 -30%': m['dd'] >= MAX_DD}


def leaked(m: dict) -> bool:
    return abs(m['t']) >= MIN_T


def run_dev(coins: dict[str, Coin], panels: dict[str, dict], preds: dict) -> dict:
    book = Book(coins)
    ts, thu = weeks(*DEV, 'mon'), weeks(*DEV, 'thu')
    rand = random_means(book, panels['mon'], ts)
    p95 = percentile(rand, 0.95)
    out = {'rand_mean': st.fmean(rand), 'p95': p95, 'groups': {}, 'controls': {}}
    for g in GROUPS:
        picks = picks_for(g, panels['mon'], preds, 'mon', ts)
        if not picks:
            continue
        m = metrics(simulate(book, picks), DEV_MID)
        stress = metrics(simulate(book, picks, STRESS), DEV_MID)['mean']
        tm = metrics(simulate(book, picks_for(g, panels['thu'], preds, 'thu', thu)), DEV_MID)['mean']
        checks = dev_checks(m, p95, stress, tm)
        out['groups'][g] = {**m, 'stress': stress, 'thu': tm, 'checks': checks, 'passed': all(checks.values()),
                            'ic': spearman_ic(panels['mon'], preds.get((g, 'mon'), {}), g, ts)}
    for g in CONTROLS:
        picks = picks_for(g, panels['mon'], preds, 'mon', ts)
        if picks:
            out['controls'][g] = metrics(simulate(book, picks), DEV_MID)
    out['leak'] = any(leaked(m) for m in out['controls'].values())
    passed = {g: v for g, v in out['groups'].items() if v['passed']}
    out['pick'] = None if out['leak'] or not passed else max(passed, key=lambda g: passed[g]['t'])
    return out


def run_exam(coins: dict[str, Coin], panel: dict, preds: dict, group: str) -> dict:
    book = Book(coins)
    ts = weeks(*EXAM, 'mon')
    rand = random_means(book, panel, ts)
    p90 = percentile(rand, 0.90)
    m = metrics(simulate(book, picks_for(group, panel, preds, 'mon', ts)), EXAM_MID)
    checks = exam_checks(m, p90)
    return {'group': group, **m, 'p90': p90, 'checks': checks, 'passed': all(checks.values())}


def _pct(x: float, digits: int = 2) -> str:
    return f'{x * 100:+.{digits}f}%'


def report(res: dict, when: str, exam: dict | None = None) -> str:
    """研究期與（有的話）考卷的文字報告。"""
    lines = [f'# 第二十一輪：多幣排序（{when}）', '',
             f'研究期 2022-01-03～2024-12-30，每週前 50 大、10 多 10 空、合計 1 倍。隨機挑選每週平均 {_pct(res["rand_mean"])}，第 95 百分位 {_pct(res["p95"])}。', '',
             '| 組 | 週數 | 每週平均 | t | 前半 | 後半 | 總報酬 | 最大回撤 | 週換手 | 成本加倍 | 週四起算 | 排名相關 |', '|---|---|---|---|---|---|---|---|---|---|---|---|']
    for g, m in res['groups'].items():
        lines.append(f"| `{g}` | {m['weeks']} | {_pct(m['mean'])} | {m['t']:+.2f} | {_pct(m['h1'])} | {_pct(m['h2'])} | {_pct(m['total'], 1)} | {_pct(m['dd'], 1)} | "
                     f"{m['turnover'] * 100:.0f}% | {_pct(m['stress'])} | {_pct(m['thu'])} | {m['ic']:+.3f} |")
    lines += ['', '## 判定', '']
    for g, m in res['groups'].items():
        miss = [k for k, ok in m['checks'].items() if not ok]
        lines.append(f"- `{g}`：{'**全部通過**' if m['passed'] else '沒過：' + '、'.join(miss)}")
    lines += ['', '## 只報告、不是標準', '', '| 組 | 多方每週 | 空方每週 | 資金費率每週 | 最賺的幣 | 拿掉後每週 |', '|---|---|---|---|---|---|']
    for g, m in res['groups'].items():
        lines.append(f"| `{g}` | {_pct(m['long'])} | {_pct(m['short'])} | {_pct(m['fund'])} | {m['top']} | {_pct(m['drop_top'])} |")
    lines += ['', '## 對照組（每週內打亂答案再訓練，t 絕對值 ≥ 3 代表程式有洩漏）', '']
    for g, m in res['controls'].items():
        lines.append(f"- `{g}`：每週平均 {_pct(m['mean'])}，t {m['t']:+.2f}")
    lines += ['', f"洩漏：{'有，整輪作廢' if res['leak'] else '沒有'}。可進考卷：{res['pick'] or '沒有'}。"]
    if exam:
        lines += ['', f"## 考卷 2025-01～2026-10（`{exam['group']}`，只考一次）", '',
                  f"每週平均 {_pct(exam['mean'])}（隨機第 90 百分位 {_pct(exam['p90'])}），t {exam['t']:+.2f}，前半 {_pct(exam['h1'])}、後半 {_pct(exam['h2'])}，"
                  f"總報酬 {_pct(exam['total'], 1)}，最大回撤 {_pct(exam['dd'], 1)}。", '',
                  f"判定：{'**通過**' if exam['passed'] else '沒過：' + '、'.join(k for k, ok in exam['checks'].items() if not ok)}"]
    return '\n'.join(lines) + '\n'
