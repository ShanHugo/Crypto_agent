"""第二十五輪：多空比研究（DESIGN.md 6.20，2026-11-07 營運者同意）。**看過結果後不改**。

4 組：crowd（跟全體帳戶多空比反著做）、whale（大戶部位比 ÷ 全體比越高越做多）、blend_crowd、blend_whale（加進 blend 的排名平均）。
資料來自 Binance 公開資料站（scripts/fetch_xsec_metrics.py）。資料站標 T 的列等於 API 標 T＋5 分鐘的值，
所以只用 create_time ≤ 決策前 15 分鐘的列。gbm、ridge 用已存的預測，不重訓；模擬與指標沿用 lab/xsec.py（不改）。
2025-01～2026-10 只當參考段，研究期判定寫下之後才能算。研究統計用 float（不碰交易路徑）。
"""
from __future__ import annotations

import csv
import math
import random
import statistics as st
from pathlib import Path

from crypto_robot.lab import xsec, xsec_blend
from crypto_robot.lab.hourly import ms
from crypto_robot.lab.xsec import D, MIN_UNI, SEED

DEV = (ms(2023, 1, 2), ms(2024, 12, 30))
DEV_MID = ms(2024, 1, 1)
MIN_WEEKS = 90
MIN_COVERAGE = 0.95
SNAP_GAP = 15 * 60_000          # 決策前 15 分鐘（資料站的標記比 API 早 5 分鐘）
SNAP_WINDOW = 6 * 3_600_000     # 更舊的列不算這次決策的快照
GROUPS = ['crowd', 'whale', 'blend_crowd', 'blend_whale']
CONTROL = 'pos_shuf'
FIELDS = {'global': 'count_long_short_ratio', 'top': 'sum_toptrader_long_short_ratio',
          'top_acc': 'count_toptrader_long_short_ratio', 'oi': 'sum_open_interest_value'}


def _num(v: object) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 0 and math.isfinite(x) else None


def snapshot(rows: list[tuple[int, dict]], t: int) -> tuple[int, dict[str, float | None]] | None:
    """rows：(create_time 毫秒, 原始欄位)，順序不拘。回傳決策 t 能用的最新一列；沒有就 None。"""
    usable = [r for r in rows if t - SNAP_WINDOW <= r[0] <= t - SNAP_GAP]
    if not usable:
        return None
    when, raw = max(usable, key=lambda r: r[0])
    return when, {k: _num(raw.get(col)) for k, col in FIELDS.items()}


def read_metrics(path: Path) -> dict[int, dict[str, dict[str, float | None]]]:
    """決策時點 → 幣 → 欄位。沒有檔案或沒有列的幣不會出現。"""
    out: dict = {}
    with path.open(encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if r['status'] == 'ok':
                out.setdefault(int(r['t']), {})[r['sym']] = {k: _num(r[k]) for k in FIELDS}
    return out


def crowd_score(names: dict | list, snap: dict) -> dict[str, float]:
    """全體多空比越高分數越低。"""
    out = {}
    for s in names:
        g = snap.get(s, {}).get('global')
        if g:
            out[s] = -math.log(g)
    return out


def whale_score(names: dict | list, snap: dict) -> dict[str, float]:
    """log（大戶部位比 ÷ 全體比）。兩個都要有值。"""
    out = {}
    for s in names:
        v = snap.get(s, {})
        if v.get('top') and v.get('global'):
            out[s] = math.log(v['top'] / v['global'])
    return out


FACTOR = {'crowd': crowd_score, 'whale': whale_score}


def score_for(kind: str, uni: dict, snap: dict, gbm: dict, ridge: dict) -> dict[str, float]:
    """單獨用就是因子分數；blend_* 是 gbm、ridge、動能、因子四個排名的平均，只留四個都有的幣。"""
    if kind in FACTOR:
        return FACTOR[kind](uni, snap)
    f = kind.removeprefix('blend_')
    return xsec_blend.blend_score(uni, {'gbm': gbm, 'ridge': ridge, f: FACTOR[f](uni, snap)}, ['gbm', 'ridge', f])


def picks_for(kind: str, panel: dict, snaps: dict, preds: dict, wd: str, ts: list[int]) -> list[tuple[int, list[str], list[str]]]:
    out = []
    for t in ts:
        uni = panel.get(t)
        if not uni:
            continue
        one = lambda m: preds.get((m, wd), {}).get(t, {})      # noqa: E731, B023
        score = score_for(kind, uni, snaps.get(t, {}), one('gbm'), one('ridge'))
        if len(score) >= MIN_UNI:
            out.append((t, *xsec.select(score)))
    return out


def shuffled_picks(panel: dict, snaps: dict, ts: list[int], seed: int = SEED) -> list[tuple[int, list[str], list[str]]]:
    """對照組：whale 的分數在每週內隨機打亂。"""
    rng = random.Random(seed)
    out = []
    for t in ts:
        score = whale_score(panel.get(t, {}), snaps.get(t, {}))
        if len(score) >= MIN_UNI:
            names = sorted(score)
            vals = [score[s] for s in names]
            rng.shuffle(vals)
            out.append((t, *xsec.select(dict(zip(names, vals)))))
    return out


def coverage(panel: dict, snaps: dict, ts: list[int]) -> dict:
    """選幣池裡 whale 兩個欄位都有值的比例；進研究前要 ≥ 95%。"""
    have = total = 0
    worst = (1.0, None)
    for t in ts:
        uni = panel.get(t, {})
        if not uni:
            continue
        n = len(whale_score(uni, snaps.get(t, {})))
        have, total = have + n, total + len(uni)
        worst = min(worst, (n / len(uni), t), key=lambda x: x[0])
    return {'have': have, 'total': total, 'rate': have / total if total else 0.0, 'worst_rate': worst[0], 'worst_t': worst[1]}


def dev_checks(m: dict, p95: float, stress_mean: float, thu_mean: float) -> dict[str, bool]:
    """第二十一輪的九項；只有週數因為研究期 104 週改成 90。"""
    c = xsec.dev_checks(m, p95, stress_mean, thu_mean)
    c.pop('有持倉 ≥ 130 週')
    return {'有持倉 ≥ 90 週': m['weeks'] >= MIN_WEEKS, **c}


def beats_blend(m: dict, base: dict) -> bool:
    """第 10 項（只給 blend_*）：t 高於 blend，回撤也不比它深。"""
    return m['t'] > base['t'] and m['dd'] >= base['dd']


def oi_change(panel: dict, snaps: dict, ts: list[int]) -> dict[int, dict[str, float]]:
    """未平倉金額 7 天變化，只拿來報告等級相關。"""
    out = {}
    for t in ts:
        now, old = snaps.get(t, {}), snaps.get(t - 7 * D, {})
        out[t] = {s: now[s]['oi'] / old[s]['oi'] - 1 for s in panel.get(t, {})
                  if now.get(s, {}).get('oi') and old.get(s, {}).get('oi')}
    return out


def _measure(book: xsec.Book, picks: list, mid: int) -> dict:
    sim = xsec.simulate(book, picks)
    return {**xsec.metrics(sim, mid), 'worst': min((r['ret'] for r in sim['rows']), default=0.0)}


def ics(panel: dict, snaps: dict, ts: list[int]) -> dict[str, float]:
    """和下週報酬排名的相關（只報告）。"""
    tab = {name: {t: FACTOR[name](panel.get(t, {}), snaps.get(t, {})) for t in ts} for name in FACTOR}
    tab['oi_chg_7d'] = oi_change(panel, snaps, ts)
    return {k: xsec.spearman_ic(panel, v, k, ts) for k, v in tab.items()}


def run_dev(coins: dict[str, xsec.Coin], panels: dict[str, dict], snaps: dict, preds: dict) -> dict:
    book = xsec.Book(coins)
    ts, thu = xsec.weeks(*DEV, 'mon'), xsec.weeks(*DEV, 'thu')
    cover = coverage(panels['mon'], snaps, ts)
    if cover['rate'] < MIN_COVERAGE:
        raise ValueError(f"涵蓋率 {cover['rate']:.1%} 低於 95%，不改標準硬跑（DESIGN 6.20）")
    base = _measure(book, xsec_blend.picks_for('blend', panels['mon'], preds, 'mon', ts), DEV_MID)
    rand = xsec.random_means(book, panels['mon'], ts)
    p95 = xsec.percentile(rand, 0.95)
    groups = {}
    for g in GROUPS:
        picks = picks_for(g, panels['mon'], snaps, preds, 'mon', ts)
        m = _measure(book, picks, DEV_MID)
        stress = xsec.metrics(xsec.simulate(book, picks, xsec.STRESS), DEV_MID)['mean']
        tm = xsec.metrics(xsec.simulate(book, picks_for(g, panels['thu'], snaps, preds, 'thu', thu)), DEV_MID)['mean']
        checks = dev_checks(m, p95, stress, tm)
        if g.startswith('blend_'):
            checks['t 較高、回撤不比 blend 深'] = beats_blend(m, base)
        groups[g] = {**m, 'stress': stress, 'thu': tm, 'checks': checks, 'passed': all(checks.values())}
    control = xsec.metrics(xsec.simulate(book, shuffled_picks(panels['mon'], snaps, ts)), DEV_MID)
    leak = xsec.leaked(control)
    return {'weeks': len(ts), 'coverage': cover, 'groups': groups, 'base': base, 'p95': p95, 'rand_mean': st.fmean(rand),
            'control': control, 'leak': leak, 'ic': ics(panels['mon'], snaps, ts),
            'passed': [] if leak else [g for g, v in groups.items() if v['passed']]}


def run_reference(coins: dict[str, xsec.Coin], panel: dict, snaps: dict, preds: dict) -> dict:
    book = xsec.Book(coins)
    ts = xsec.weeks(*xsec.EXAM, 'mon')
    base = _measure(book, xsec_blend.picks_for('blend', panel, preds, 'mon', ts), xsec.EXAM_MID)
    groups = {g: _measure(book, picks_for(g, panel, snaps, preds, 'mon', ts), xsec.EXAM_MID) for g in GROUPS}
    return {'weeks': len(ts), 'coverage': coverage(panel, snaps, ts), 'groups': groups, 'base': base, 'ic': ics(panel, snaps, ts)}


def _pct(x: float, digits: int = 2) -> str:
    return f'{x * 100:+.{digits}f}%'


def _row(name: str, m: dict) -> str:
    return (f"| `{name}` | {m['weeks']} | {_pct(m['mean'])} | {m['t']:+.2f} | {_pct(m['h1'])}／{_pct(m['h2'])} | {_pct(m['total'], 1)} | {_pct(m['dd'], 1)} | "
            f"{_pct(m['worst'], 1)} | {_pct(m['long'])}／{_pct(m['short'])} | {m['turnover'] * 100:.0f}% |")


HEAD = ['| 組 | 週數 | 每週平均 | t | 前半／後半 | 總報酬 | 最大回撤 | 最差一週 | 多方／空方每週 | 週換手 |', '|---|---|---|---|---|---|---|---|---|---|']


def _ic(r: dict) -> list[str]:
    return ['', '和下週報酬排名的相關（只報告，不是標準）：' + '、'.join(f'`{k}` {v:+.4f}' for k, v in r['ic'].items()), '']


def _cover(r: dict) -> str:
    c = r['coverage']
    return f"涵蓋率 {c['rate']:.1%}（{c['have']}/{c['total']}），最低的一週 {c['worst_rate']:.0%}。"


def dev_report(r: dict, when: str) -> str:
    lines = [f'# 第二十五輪：多空比研究（{when}）', '', f"研究期 2023-01-02～2024-12-30（{r['weeks']} 週），已扣成本。`blend` 是同期基準。{_cover(r)}", '', *HEAD, _row('blend', r['base'])]
    lines += [_row(g, m) for g, m in r['groups'].items()]
    lines += ['', f"隨機挑幣每週平均 {_pct(r['rand_mean'])}，第 95 百分位 {_pct(r['p95'])}。", '', '| 組 | 成本加倍 | 週四起算 | 最賺的幣、拿掉後每週 |', '|---|---|---|---|']
    lines += [f"| `{g}` | {_pct(m['stress'])} | {_pct(m['thu'])} | {m['top']}、{_pct(m['drop_top'])} |" for g, m in r['groups'].items()]
    lines += _ic(r)
    lines += ['## 判定', '']
    for g, m in r['groups'].items():
        miss = [k for k, ok in m['checks'].items() if not ok]
        lines.append(f"- `{g}`：{'**全部通過**' if m['passed'] else '沒過：' + '、'.join(miss)}")
    c = r['control']
    lines += ['', f"對照組 `{CONTROL}`：每週 {_pct(c['mean'])}，t {c['t']:+.2f}。程式問題：{'有，整輪作廢' if r['leak'] else '沒有'}。", '']
    return '\n'.join(lines)


def reference_report(r: dict, dev: dict, when: str) -> str:
    lines = [f'# 第二十五輪參考段（{when}）', '', f"2025-01-06～2026-10-26（{r['weeks']} 週），不判定通過，不是乾淨考卷。研究期通過的組：{'、'.join(dev['passed']) or '沒有'}。{_cover(r)}", '', *HEAD, _row('blend', r['base'])]
    lines += [_row(g, m) for g, m in r['groups'].items()]
    lines += _ic(r)
    return '\n'.join(lines)
