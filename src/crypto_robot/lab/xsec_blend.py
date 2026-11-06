"""第二十四輪：多幣排序合體模型（DESIGN.md 6.17，2026-11-06 營運者同意）。**看過結果後不改**。

3 組：blend（gbm、ridge、動能排名平均）、gbm_vt（只用 gbm，依波動調部位）、blend_vt（兩個一起）。
部位比例 = min(1, 20% ÷ 預測年化波動)，每週定一次，只縮不放大。
gbm、ridge 用第二十一輪已存的預測，不重訓；選幣池與指標沿用 lab/xsec.py（不改）。
2025-01～2026-10 只當參考段，研究期判定寫下之後才能算。研究統計用 float（不碰交易路徑）。
"""
from __future__ import annotations

import random
import statistics as st

from crypto_robot.lab import xsec
from crypto_robot.lab.xsec import COST, D, FRAC, MIN_UNI, RUNS, SEED

LAMBDA, LOOKBACK, TARGET, YEAR = 0.94, 56, 0.20, 365
GROUPS = {'blend': ('blend', False), 'gbm_vt': ('gbm', True), 'blend_vt': ('blend', True)}
CONTROL = 'blend_shuf'


def blend_score(uni: dict, wd_preds: dict[str, dict], models: list[str], use_mom: bool = True) -> dict[str, float]:
    """每個模型各自在幣之間排名，再取等權重平均。只留所有模型都有分數的幣。"""
    names = sorted(s for s in uni if all(s in wd_preds.get(m, {}) for m in models))
    if not names:
        return {}
    cols = [xsec.rank([wd_preds[m][s] for s in names]) for m in models]
    if use_mom:
        cols.append(xsec.rank([uni[s][1] for s in names]))
    return {s: st.fmean(col[i] for col in cols) for i, s in enumerate(names)}


def blend_picks(universe: list[str], gbm: dict[str, float], ridge: dict[str, float], mom: dict[str, float]) -> tuple[list[str], list[str]]:
    """前瞻模擬（DESIGN 6.18）：這週的 blend 名單。三個分數都有的幣不足 20 個就報錯，不換倉。"""
    uni = {s: (0.0, mom[s]) for s in universe if s in mom}
    score = blend_score(uni, {'gbm': gbm, 'ridge': ridge}, ['gbm', 'ridge'])
    if len(score) < MIN_UNI:
        raise ValueError(f'三個分數都有的幣只有 {len(score)} 個，不換倉')
    return xsec.select(score)


def picks_for(kind: str, panel: dict, preds: dict, wd: str, ts: list[int]) -> list[tuple[int, list[str], list[str]]]:
    """kind：gbm 只用 gbm；blend 三個合體；blend_shuf 對照組（兩個打亂答案的模型，動能不靠訓練答案，不放）。"""
    out = []
    for t in ts:
        uni = panel.get(t)
        if not uni:
            continue
        one = lambda m: preds.get((m, wd), {}).get(t, {})      # noqa: E731, B023
        if kind == 'gbm':
            score = {s: x for s, x in one('gbm').items() if s in uni}
        elif kind == 'blend':
            score = blend_score(uni, {m: one(m) for m in ('gbm', 'ridge')}, ['gbm', 'ridge'])
        else:
            score = blend_score(uni, {m: one(m) for m in ('gbm_shuf', 'ridge_shuf')}, ['gbm_shuf', 'ridge_shuf'], use_mom=False)
        if len(score) >= MIN_UNI:
            out.append((t, *xsec.select(score)))
    return out


def basket_vol(coins: dict[str, xsec.Coin], t: int, longs: list[str], shorts: list[str]) -> float:
    """這些幣用目前權重，回算過去 56 天每天的組合報酬，EWMA λ 0.94，年化。只用 t 以前已收盤的 K 線；沒有資料的日子算 0。"""
    k = len(longs)
    wt = {s: 1 / (2 * k) for s in longs} | {s: -1 / (2 * k) for s in shorts}
    total, norm = 0.0, 0.0
    for age in range(LOOKBACK):
        day = t - (age + 1) * D
        r = 0.0
        for s, w in wt.items():
            c = coins[s]
            i = c.pos.get(day)
            if i is not None and c.r[i] is not None:
                r += w * c.r[i]
        weight = LAMBDA ** age
        total += weight * r * r
        norm += weight
    return (YEAR * total / norm) ** 0.5


def scale_of(vol: float) -> float:
    return 1.0 if vol <= 0 else min(1.0, TARGET / vol)


def scales_for(coins: dict[str, xsec.Coin], picks: list) -> dict[int, float]:
    return {t: scale_of(basket_vol(coins, t, longs, shorts)) for t, longs, shorts in picks}


def step(book: xsec.Book, t: int, longs: list[str], shorts: list[str], prev: dict[str, float], cost: float, scale: float = 1.0) -> dict:
    """同 xsec.step，只多一個部位比例：每個幣 scale/(2k)，合計 scale 倍。"""
    k = len(longs)
    tgt = {s: scale / (2 * k) for s in longs} | {s: -scale / (2 * k) for s in shorts}
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
            extra += abs(w * h.ratio) * 2 * cost
        else:
            after[s] = w * h.ratio
    r = max(long_pnl + short_pnl - fee - extra, -1.0)
    return {'ret': r, 'fee': fee, 'extra': extra, 'turnover': turnover, 'long': long_pnl, 'short': short_pnl, 'fund': fund_pnl,
            'by': by, 'tgt': tgt, 'weights': {s: x / (1 + r) for s, x in after.items()} if r > -1 else {}}


def simulate(book: xsec.Book, picks: list, cost: float = COST, scales: dict[int, float] | None = None) -> dict:
    """同 xsec.simulate，每週用 scales[t] 的部位比例；沒給就是 1 倍。"""
    prev: dict[str, float] = {}
    equity, marks, rows = 1.0, [1.0], []
    for t, longs, shorts in picks:
        if equity <= 0:
            rows.append({'t': t, 'ret': 0.0, 'turnover': 0.0, 'long': 0.0, 'short': 0.0, 'fund': 0.0, 'by': {}})
            continue
        s = step(book, t, longs, shorts, prev, cost, scales.get(t, 1.0) if scales else 1.0)
        for d in range(7):
            pnl = sum(w * (book.hold(c, t).path[d][0] - 1 - book.hold(c, t).path[d][1]) for c, w in s['tgt'].items())
            marks.append(max(equity * (1 - s['fee'] + pnl), 0.0))
        equity *= 1 + s['ret']
        marks[-1] = equity
        prev = s['weights']
        rows.append({'t': t, **{k: s[k] for k in ('ret', 'turnover', 'long', 'short', 'fund', 'by')}})
    return {'rows': rows, 'marks': marks, 'equity': equity}


def random_means(book: xsec.Book, panel: dict, ts: list[int], scales: dict[int, float] | None = None, runs: int = RUNS, seed: int = SEED, cost: float = COST) -> list[float]:
    """同 xsec.random_means；要調部位的組，隨機組每週用同一個比例。"""
    rng = random.Random(seed)
    table = [(t, sorted(panel[t])) for t in ts if panel.get(t)]
    out = []
    for _ in range(runs):
        prev, total, equity = {}, 0.0, 1.0
        for t, names in table:
            if equity <= 0:
                continue
            k = max(1, round(len(names) * FRAC))
            pick = rng.sample(names, 2 * k)
            s = step(book, t, pick[:k], pick[k:], prev, cost, scales.get(t, 1.0) if scales else 1.0)
            total += s['ret']
            equity *= 1 + s['ret']
            prev = s['weights']
        out.append(total / len(table))
    return sorted(out)


def vt_checks(m: dict, fixed_dd: float, plain_t: float) -> dict[str, bool]:
    """第 10、11 項：避免把部位變小當成風險管理有效（第二十輪的教訓）。"""
    return {'回撤比同平均比例的固定版本淺': m['dd'] > fixed_dd, 't 高於不調部位': m['t'] > plain_t}


def beats_gbm(m: dict, base: dict, fixed_dd: float | None) -> bool:
    """比較表的「贏 gbm」（單一期間）：t 較高、回撤較淺；要調部位的組，回撤也要比固定版本淺。"""
    return m['t'] > base['t'] and m['dd'] > base['dd'] and (fixed_dd is None or m['dd'] > fixed_dd)


def _measure(book: xsec.Book, kind: str, vt: bool, panel: dict, preds: dict, ts: list[int], mid: int) -> dict:
    """一組在一段期間的指標；要調部位的組另外算固定比例與不調部位的版本。"""
    picks = picks_for(kind, panel, preds, 'mon', ts)
    scales = scales_for(book.coins, picks) if vt else None
    sim = simulate(book, picks, scales=scales)
    m = xsec.metrics(sim, mid)
    worst = min(sim['rows'], key=lambda r: r['ret'])
    out = {**m, 'worst': worst['ret'], 'avg_scale': st.fmean(scales.values()) if vt else 1.0, 'picks': picks, 'scales': scales, 'fixed_dd': None, 'plain_t': None}
    if vt:
        avg = out['avg_scale']
        out['fixed_dd'] = xsec.metrics(simulate(book, picks, scales={t: avg for t, _, _ in picks}), mid)['dd']
        out['plain_t'] = xsec.metrics(simulate(book, picks), mid)['t']
    return out


def _plain(m: dict) -> dict:
    return {k: v for k, v in m.items() if k not in ('picks', 'scales')}


def run_dev(coins: dict[str, xsec.Coin], panels: dict[str, dict], preds: dict) -> dict:
    book = xsec.Book(coins)
    ts, thu = xsec.weeks(*xsec.DEV, 'mon'), xsec.weeks(*xsec.DEV, 'thu')
    base = _measure(book, 'gbm', False, panels['mon'], preds, ts, xsec.DEV_MID)
    groups = {}
    for name, (kind, vt) in GROUPS.items():
        m = _measure(book, kind, vt, panels['mon'], preds, ts, xsec.DEV_MID)
        rand = random_means(book, panels['mon'], ts, m['scales'])
        p95 = xsec.percentile(rand, 0.95)
        stress = xsec.metrics(simulate(book, m['picks'], xsec.STRESS, m['scales']), xsec.DEV_MID)['mean']
        tp = picks_for(kind, panels['thu'], preds, 'thu', thu)
        tm = xsec.metrics(simulate(book, tp, scales=scales_for(coins, tp) if vt else None), xsec.DEV_MID)['mean']
        checks = xsec.dev_checks(m, p95, stress, tm)
        if vt:
            checks |= vt_checks(m, m['fixed_dd'], m['plain_t'])
        groups[name] = {**_plain(m), 'p95': p95, 'rand_mean': st.fmean(rand), 'stress': stress, 'thu': tm,
                        'checks': checks, 'passed': all(checks.values()), 'beats_gbm': beats_gbm(m, base, m['fixed_dd'])}
    control = xsec.metrics(simulate(book, picks_for(CONTROL, panels['mon'], preds, 'mon', ts)), xsec.DEV_MID)
    leak = xsec.leaked(control)
    passed = {g: v for g, v in groups.items() if v['passed']}
    return {'groups': groups, 'base': _plain(base), 'control': control, 'leak': leak,
            'pick': None if leak or not passed else max(passed, key=lambda g: passed[g]['t'])}


def run_reference(coins: dict[str, xsec.Coin], panel: dict, preds: dict) -> dict:
    book = xsec.Book(coins)
    ts = xsec.weeks(*xsec.EXAM, 'mon')
    base = _measure(book, 'gbm', False, panel, preds, ts, xsec.EXAM_MID)
    groups = {}
    for name, (kind, vt) in GROUPS.items():
        m = _measure(book, kind, vt, panel, preds, ts, xsec.EXAM_MID)
        groups[name] = {**_plain(m), 'beats_gbm': beats_gbm(m, base, m['fixed_dd'])}
    return {'groups': groups, 'base': _plain(base)}


def _pct(x: float, digits: int = 2) -> str:
    return f'{x * 100:+.{digits}f}%'


def _row(name: str, m: dict) -> str:
    fixed = _pct(m['fixed_dd'], 1) if m.get('fixed_dd') is not None else '–'
    return (f"| `{name}` | {_pct(m['mean'])} | {m['t']:+.2f} | {_pct(m['h1'])}／{_pct(m['h2'])} | {_pct(m['total'], 1)} | {_pct(m['dd'], 1)} | {fixed} | "
            f"{_pct(m['worst'], 1)} | {_pct(m['long'])}／{_pct(m['short'])} | {m['avg_scale']:.2f} | {m['turnover'] * 100:.0f}% |")


HEAD = ['| 組 | 每週平均 | t | 前半／後半 | 總報酬 | 最大回撤 | 同比例固定回撤 | 最差一週 | 多方／空方每週 | 平均比例 | 週換手 |', '|---|---|---|---|---|---|---|---|---|---|---|']


def dev_report(r: dict, when: str) -> str:
    lines = [f'# 第二十四輪：多幣排序合體模型（{when}）', '', '研究期 2022-01-03～2024-12-30，已扣成本。`gbm` 是第二十一輪（比較用）。', '', *HEAD, _row('gbm', r['base'])]
    lines += [_row(g, m) for g, m in r['groups'].items()]
    lines += ['', '| 組 | 隨機第 95 百分位 | 成本加倍 | 週四起算 | 最賺的幣、拿掉後每週 |', '|---|---|---|---|---|']
    lines += [f"| `{g}` | {_pct(m['p95'])} | {_pct(m['stress'])} | {_pct(m['thu'])} | {m['top']}、{_pct(m['drop_top'])} |" for g, m in r['groups'].items()]
    lines += ['', '## 判定', '']
    for g, m in r['groups'].items():
        miss = [k for k, ok in m['checks'].items() if not ok]
        lines.append(f"- `{g}`：{'**全部通過**' if m['passed'] else '沒過：' + '、'.join(miss)}；這段贏 `gbm`：{'是' if m['beats_gbm'] else '否'}")
    c = r['control']
    lines += ['', f"對照組 `{CONTROL}`：每週 {_pct(c['mean'])}，t {c['t']:+.2f}。洩漏：{'有，整輪作廢' if r['leak'] else '沒有'}。", '']
    return '\n'.join(lines)


def reference_report(r: dict, dev: dict, when: str) -> str:
    lines = [f'# 第二十四輪參考段（{when}）', '', f"2025-01-06～2026-10-26，不判定通過，不是乾淨考卷。研究期通過的組：{dev['pick'] or '沒有'}。", '', *HEAD, _row('gbm', r['base'])]
    lines += [_row(g, m) for g, m in r['groups'].items()]
    lines += ['', '## 比較表：兩段都要 t 較高、回撤較淺才算贏 `gbm`（只報告，不能取代研究期標準）', '']
    for g, m in r['groups'].items():
        d = dev['groups'][g]['beats_gbm']
        lines.append(f"- `{g}`：研究期 {'贏' if d else '沒贏'}、參考段 {'贏' if m['beats_gbm'] else '沒贏'} → {'**兩段都贏**' if d and m['beats_gbm'] else '不算贏'}")
    lines.append('')
    return '\n'.join(lines)
