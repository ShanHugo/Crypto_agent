"""第二十三輪：多幣排序不放空高波動幣（DESIGN.md 6.15，2026-11-06 營運者同意）。**看過結果後不改**。

只改放空名單：每週先拿掉波動最高的 20%，剩下的幣裡分數最低的 20% 放空；做多不變。
直接用第二十一輪已存的 gbm 預測，不重訓；選幣池、換手、成本、模擬都沿用 lab/xsec.py（不改）。
2025-01～2026-10 只當參考段，研究期判定寫下之後才能算。研究統計用 float（不碰交易路徑）。
"""
from __future__ import annotations

import csv
import random
import statistics as st
from datetime import datetime, timezone
from pathlib import Path

from crypto_robot.lab import xsec
from crypto_robot.lab.xsec import FRAC, MIN_UNI, RUNS, SEED

GROUP, CONTROL = 'gbm', 'gbm_shuf'
BAR = FRAC                      # 波動最高的 20% 不准放空，只測這個比例
PUMP = 1.5


def read_vols(path: Path) -> dict[int, dict[str, float]]:
    """週 → 幣 → 過去 28 天波動的排名（當週決策前已知）。"""
    out: dict[int, dict[str, float]] = {}
    with path.open(encoding='utf-8') as f:
        for r in csv.DictReader(f):
            out.setdefault(int(r['ts']), {})[r['sym']] = float(r['vol_28d'])
    return out


def barred(vols: dict[str, float]) -> set[str]:
    """波動最高的 20%；同值照代號。"""
    names = sorted(vols, key=lambda s: (-vols[s], s))
    return set(names[:max(1, round(len(names) * BAR))])


def select(score: dict[str, float], vols: dict[str, float]) -> tuple[list[str], list[str]]:
    """分數最高 20% 做多（不限制）；拿掉高波動後分數最低的 20% 放空。"""
    k = max(1, round(len(score) * FRAC))
    names = sorted(score, key=lambda s: (-score[s], s))
    longs = names[:k]
    ban = barred({s: vols[s] for s in score})
    allowed = [s for s in names if s not in ban and s not in longs]
    return longs, allowed[-k:]


def picks_for(group: str, panel: dict, preds: dict, vols: dict, wd: str, ts: list[int]) -> list[tuple[int, list[str], list[str]]]:
    out = []
    for t in ts:
        uni = panel.get(t)
        if not uni:
            continue
        score = {s: x for s, x in preds.get((group, wd), {}).get(t, {}).items() if s in uni}
        if len(score) >= MIN_UNI:
            out.append((t, *select(score, vols[t])))
    return out


def random_means(book: xsec.Book, panel: dict, vols: dict, ts: list[int], runs: int = RUNS, seed: int = SEED, cost: float = xsec.COST) -> list[float]:
    """隨機挑多空，放空同樣不准碰高波動的幣，同成本規則。"""
    rng = random.Random(seed)
    table = []
    for t in ts:
        if panel.get(t):
            names = sorted(panel[t])
            ban = barred({s: vols[t][s] for s in names})
            table.append((t, names, [s for s in names if s not in ban]))
    out = []
    for _ in range(runs):
        prev, total, equity = {}, 0.0, 1.0
        for t, names, allowed in table:
            if equity <= 0:
                continue
            k = max(1, round(len(names) * FRAC))
            longs = rng.sample(names, k)
            shorts = rng.sample([s for s in allowed if s not in longs], k)
            s = xsec.step(book, t, longs, shorts, prev, cost)
            total += s['ret']
            equity *= 1 + s['ret']
            prev = s['weights']
        out.append(total / len(table))
    return sorted(out)


def short_pump_rate(book: xsec.Book, picks: list) -> float:
    """空單裡，持有 7 天內任一天收盤 ≥ 進場價 +50% 的比例（只報告）。"""
    flags = [max(p[0] for p in book.hold(s, t).path) >= PUMP for t, _, shorts in picks for s in shorts]
    return st.fmean(flags) if flags else 0.0


def worst_short(rows: list[dict], shorts_by_week: dict[int, list[str]]) -> tuple[float, str, int]:
    """單週單一空單賠最多的一筆（只報告）。"""
    best = (0.0, '', 0)
    for r in rows:
        for s in shorts_by_week.get(r['t'], []):
            if r['by'].get(s, 0.0) < best[0]:
                best = (r['by'][s], s, r['t'])
    return best


def run_dev(coins: dict[str, xsec.Coin], panels: dict[str, dict], vols: dict[str, dict], preds: dict) -> dict:
    book = xsec.Book(coins)
    ts, thu = xsec.weeks(*xsec.DEV, 'mon'), xsec.weeks(*xsec.DEV, 'thu')
    rand = random_means(book, panels['mon'], vols['mon'], ts)
    p95 = xsec.percentile(rand, 0.95)
    picks = picks_for(GROUP, panels['mon'], preds, vols['mon'], 'mon', ts)
    m = xsec.metrics(xsec.simulate(book, picks), xsec.DEV_MID)
    stress = xsec.metrics(xsec.simulate(book, picks, xsec.STRESS), xsec.DEV_MID)['mean']
    tm = xsec.metrics(xsec.simulate(book, picks_for(GROUP, panels['thu'], preds, vols['thu'], 'thu', thu)), xsec.DEV_MID)['mean']
    checks = xsec.dev_checks(m, p95, stress, tm)
    control = xsec.metrics(xsec.simulate(book, picks_for(CONTROL, panels['mon'], preds, vols['mon'], 'mon', ts)), xsec.DEV_MID)
    return {**m, 'rand_mean': st.fmean(rand), 'p95': p95, 'stress': stress, 'thu': tm, 'checks': checks, 'passed': all(checks.values()),
            'control': control, 'leak': xsec.leaked(control), 'pump': short_pump_rate(book, picks)}


def run_reference(coins: dict[str, xsec.Coin], panel: dict, vols: dict, preds: dict) -> dict:
    book = xsec.Book(coins)
    ts = xsec.weeks(*xsec.EXAM, 'mon')
    picks = picks_for(GROUP, panel, preds, vols, 'mon', ts)
    sim = xsec.simulate(book, picks)
    m = xsec.metrics(sim, xsec.EXAM_MID)
    worst = min(sim['rows'], key=lambda r: r['ret'])
    shorts = {t: s for t, _, s in picks}
    day = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime('%Y-%m-%d')      # noqa: E731
    myx = {day(r['t']): {'short': 'MYXUSDT' in shorts[r['t']], 'ret': r['ret']} for r in sim['rows'] if day(r['t']) in ('2025-09-01', '2025-09-08')}
    w = worst_short(sim['rows'], shorts)
    return {**m, 'worst': worst['ret'], 'worst_t': day(worst['t']), 'pump': short_pump_rate(book, picks), 'myx': myx,
            'worst_short': {'loss': w[0], 'sym': w[1], 'at': day(w[2]) if w[2] else ''},
            'rand_mean': st.fmean(random_means(book, panel, vols, ts)), 'low': min(sim['marks'])}


def _pct(x: float, digits: int = 2) -> str:
    return f'{x * 100:+.{digits}f}%'


def dev_report(m: dict, when: str) -> str:
    c = m['control']
    miss = [k for k, ok in m['checks'].items() if not ok]
    return '\n'.join([
        f'# 第二十三輪：不放空高波動幣（{when}）', '',
        '研究期 2022-01-03～2024-12-30，`gbm`（第二十一輪已存的預測），已扣成本。', '',
        '| | 這一輪 | 第二十一輪 |', '|---|---|---|',
        f"| 每週平均 | {_pct(m['mean'])} | +0.34% |",
        f"| t | {m['t']:+.2f} | +1.26 |",
        f"| 前半／後半 | {_pct(m['h1'])}／{_pct(m['h2'])} | +0.36%／+0.31% |",
        f"| 總報酬 | {_pct(m['total'], 1)} | +55.1% |",
        f"| 最大回撤 | {_pct(m['dd'], 1)} | -30.8% |",
        f"| 多方／空方每週 | {_pct(m['long'])}／{_pct(m['short'])} | -0.03%／+0.46% |",
        f"| 資金費率每週 | {_pct(m['fund'])} | +0.04% |",
        f"| 週換手 | {m['turnover'] * 100:.0f}% | 92% |",
        f"| 空單暴漲率 | {m['pump'] * 100:.2f}% | 1.79% |",
        f"| 成本加倍 | {_pct(m['stress'])} | +0.24% |",
        f"| 週四起算 | {_pct(m['thu'])} | +0.46% |",
        f"| 最賺的幣、拿掉後每週 | {m['top']}、{_pct(m['drop_top'])} | GALAUSDT、+0.26% |", '',
        f"隨機挑幣（同樣不空高波動）：每週平均 {_pct(m['rand_mean'])}，第 95 百分位 {_pct(m['p95'])}。", '',
        f"對照組 `gbm_shuf`：每週 {_pct(c['mean'])}，t {c['t']:+.2f}。洩漏：{'有，整輪作廢' if m['leak'] else '沒有'}。", '',
        f"判定：{'**九項全過**' if m['passed'] else '沒過：' + '、'.join(miss)}。", ''])


def reference_report(m: dict, dev: dict, when: str) -> str:
    w = m['worst_short']
    myx = '；'.join(f"{d}：{'有放空' if v['short'] else '沒放空'}，當週 {_pct(v['ret'], 1)}" for d, v in sorted(m['myx'].items()))
    return '\n'.join([
        f'# 第二十三輪參考段（{when}）', '',
        f"2025-01-06～2026-10-26，不判定通過，而且這條規則是看過這段才想到的，不是樣本外。研究期判定已先寫下：{'通過' if dev['passed'] else '沒過'}。", '',
        '| | 這一輪 | 第二十一輪考卷 |', '|---|---|---|',
        f"| 每週平均 | {_pct(m['mean'])} | +0.58% |",
        f"| t | {m['t']:+.2f} | +1.08 |",
        f"| 總報酬 | {_pct(m['total'], 1)} | +50.3% |",
        f"| 最大回撤 | {_pct(m['dd'], 1)} | -49.8% |",
        f"| 權益最低（起始＝1） | {m['low']:.3f} | 0.733 |",
        f"| 最差一週 | {_pct(m['worst'], 1)}（{m['worst_t']}） | -29.6%（2025-09-08） |",
        f"| 單筆空單最賠 | {_pct(w['loss'], 1)}（{w['sym']}，{w['at']}） | -22.0%（MYXUSDT，2025-09-08） |",
        f"| 空單暴漲率 | {m['pump'] * 100:.2f}% | 5.32% |",
        f"| 多方／空方每週 | {_pct(m['long'])}／{_pct(m['short'])} | -0.11%／+0.77% |",
        f"| 資金費率每週 | {_pct(m['fund'])} | -0.51% |",
        f"| 前半／後半 | {_pct(m['h1'])}／{_pct(m['h2'])} | +0.32%／+0.84% |", '',
        f"MYX 那兩週：{myx}。", '',
        f"同期隨機挑幣（同樣不空高波動）每週平均 {_pct(m['rand_mean'])}。", ''])
