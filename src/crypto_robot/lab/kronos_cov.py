"""第十八輪：Kronos 加外部資訊再訓練（DESIGN.md 6.9，2026-11-06 營運者同意）。**看過結果後不改**。

這裡只做主 venv 的部分（純 Python、不需要 torch）：
- 每根 1h K 線算 7 項外部資訊（只用那根收盤時已知的資料），用訓練期標準化後寫成 CSV，給 scripts/kronos_cov.py（Kronos venv）訓練與預測；
- 讀預測結果（每天 00:00 的 30 個樣本報酬），照第十六輪的規則評分。
研究統計用 float（不碰交易路徑）。
"""
from __future__ import annotations

import bisect
import csv
import json
import statistics as st
from pathlib import Path

from crypto_robot.lab import futures
from crypto_robot.lab.futures import FEE, Funding
from crypto_robot.lab.hourly import H, Series, ms
from crypto_robot.lab.ta_fa import Macro

D = 24 * H
W = 7 * D
COVS = ['fund_last', 'fund_7d', 'premium', 'stable_4w', 'netliq_4w', 'dxy_4w', 'real_4w']
CLIP = 5.0
TRAIN = (ms(2020, 1, 1), ms(2024, 1, 1))
VAL = (ms(2024, 1, 1), ms(2024, 7, 1))
DEV = (ms(2024, 7, 1), ms(2025, 9, 1))
DEV_MID = ms(2025, 2, 1)
EXAM = (ms(2025, 9, 1), ms(2026, 11, 1))
MODELS = ['K0', 'K1', 'K2']
MIN_N, MIN_T = 100, 2.0
LAG = W                            # 總經與穩定幣：一律用一週前已有的值


def raw_covs(t: int, fund: Funding, prem: Macro, m: dict[str, Macro]) -> list[float] | None:
    """開盤時間 t 的那根 1h K 線收盤時（k = t + 1h）已知的 7 項；缺任何一項就 None。"""
    k = t + H
    i = bisect.bisect_right(fund.ts, k)
    a = bisect.bisect_right(fund.ts, k - W)
    p = prem.asof(t)                                   # 溢價指數 1h K 開盤 t、收盤 k
    if i == 0 or i - a < 15 or p is None or prem.ts[bisect.bisect_right(prem.ts, t) - 1] != t:
        return None

    def asof(name: str, ts: int) -> float | None:
        return m[name].asof(ts)

    def net(ts: int) -> float | None:
        x = [asof(n, ts) for n in ('WALCL', 'WTREGEN', 'RRPONTSYD')]
        return None if None in x else x[0] - x[1] - x[2] * 1000
    now, old = k - LAG, k - LAG - 4 * W
    v = {n: (asof(n, now), asof(n, old)) for n in ('stablecoins', 'DTWEXBGS', 'DFII10')}
    n1, n0 = net(now), net(old)
    if any(None in x for x in v.values()) or n1 is None or n0 is None or n0 == 0:
        return None
    return [fund.rate[i - 1], st.fmean(fund.rate[a:i]), p,
            v['stablecoins'][0] / v['stablecoins'][1] - 1, (n1 - n0) / abs(n0),
            v['DTWEXBGS'][0] / v['DTWEXBGS'][1] - 1, v['DFII10'][0] - v['DFII10'][1]]


def standardize(rows: dict[int, list[float] | None], start: int, end: int) -> tuple[dict, list[tuple[float, float]]]:
    """用 [start, end) 的平均與標準差標準化，超過 ±5 截掉；回傳（新 rows, 每項的 (平均, 標準差)）。"""
    train = [r for t, r in rows.items() if r is not None and start <= t < end]
    stats = [(st.fmean(c), st.pstdev(c) or 1.0) for c in zip(*train)]
    out = {t: None if r is None else [max(-CLIP, min(CLIP, (x - mu) / sd)) for x, (mu, sd) in zip(r, stats)]
           for t, r in rows.items()}
    return out, stats


def write_csv(path: Path, eth: Series, fund: Funding, m: dict[str, Macro]) -> dict:
    """data/kronos_cov/eth_1h.csv：ts, open, high, low, close, volume, 7 項（缺的留空）。"""
    ts = sorted(eth.idx)
    rows = {t: raw_covs(t, fund, m['premium_ethusdt_1h'], m) for t in ts}
    z, stats = standardize(rows, *TRAIN)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['ts', 'open', 'high', 'low', 'close', 'volume'] + COVS)
        for t in ts:
            i = eth.idx[t]
            w.writerow([t, eth.o[i], eth.h[i], eth.l[i], eth.c[i], eth.v[i]] + (['' ] * len(COVS) if z[t] is None else z[t]))
    info = {'stats': dict(zip(COVS, stats)), 'rows': len(ts), 'missing': sum(z[t] is None for t in ts)}
    path.with_suffix('.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
    return info


def days(start: int, end: int) -> list[int]:
    """台北 00:00＝UTC 16:00。"""
    t = start // D * D + 16 * H
    return [x for x in range(t, end, D) if x >= start]


def read_preds(path: Path) -> dict[str, dict[int, list[float]]]:
    out: dict[str, dict[int, list[float]]] = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            r = json.loads(line)
            out.setdefault(r['model'], {})[r['ts']] = r['rets']
    return out


def direction(rets: list[float]) -> int:
    m = st.fmean(rets)
    return 1 if m > FEE else -1 if m < -FEE else 0


def simulate(preds: dict[int, list[float]], eth: Series, fund: Funding, start: int, end: int):
    """回傳（交易 [(時點, 方向, 淨報酬)], 母體 {方向: [每天都做的淨報酬]}）；持有 24 小時，成本同第十四輪。"""
    trades, pool = [], {1: [], -1: []}
    for t in days(start, end):
        if t not in preds or eth.span(t - H, t + 23 * H) is None:
            continue
        for d in (1, -1):
            pool[d].append(futures.trade_net(eth, fund, t, 24, d))
        if (d := direction(preds[t])):
            trades.append((t, d, futures.trade_net(eth, fund, t, 24, d)))
    return trades, pool


def dev_passed(m: dict, k1: dict | None = None) -> bool:
    ok = ('net' in m and m['n'] >= MIN_N and m['net'] > 0 and m['t'] >= MIN_T and m['net'] > m['rand'] and min(m['half']) > 0)
    return ok and (k1 is None or m['net'] > k1.get('net', float('-inf')))


def exam_passed(m: dict) -> bool:
    return 'net' in m and m['net'] > 0 and m['net'] > m['rand'] and min(m['half']) > 0


def run_dev(preds: dict, eth: Series, fund: Funding) -> dict:
    res = {k: futures.evaluate(*simulate(preds.get(k, {}), eth, fund, *DEV), DEV_MID) for k in MODELS}
    passed = {'K0': dev_passed(res['K0']), 'K1': dev_passed(res['K1']), 'K2': dev_passed(res['K2'], res['K1'])}
    return {'metrics': res, 'passed': passed, 'days': len(days(*DEV)),
            'have': {k: sum(DEV[0] <= t < DEV[1] for t in preds.get(k, {})) for k in MODELS}}


def run_exam(preds: dict, eth: Series, fund: Funding) -> dict:
    trades, pool = simulate(preds.get('K2', {}), eth, fund, *EXAM)
    mid = trades[len(trades) // 2][0] if trades else 0
    m = futures.evaluate(trades, pool, mid, pct=0.90)
    return {**m, 'passed': exam_passed(m)}


def report(res: dict, when: str, exam: dict | None = None) -> str:
    lines = ['# 第十八輪：Kronos 加外部資訊再訓練（研究期 2024-07-01～2025-08-31，Binance ETHUSDT 合約）', '',
             f'產生時間 {when}。每天台北 00:00 預測 24 小時、30 個樣本平均 > {FEE:.2%} 做多、< -{FEE:.2%} 做空；持有 24 小時；成本：來回 {FEE:.2%}＋實際資金費率。',
             f'K0 原版、K1 再訓練不加資訊、K2 再訓練＋7 項外部資訊。研究期 {res["days"]} 天，已預測：' +
             '、'.join(f'{k} {res["have"][k]} 天' for k in MODELS), '',
             f'通過：交易 ≥ {MIN_N}、每筆淨報酬 > 0、t（扣同方向每天都做的平均）≥ {MIN_T:g}、贏隨機第 95 百分位、前後半都 > 0；K2 另外要每筆高於 K1。', '',
             '| 模型 | 交易 | 其中做空 | 每筆淨報酬 | t | 前半／後半 | 隨機95 | 過 |', '|---|---|---|---|---|---|---|---|']
    for k in MODELS:
        m = res['metrics'][k]
        if 'net' not in m:
            lines.append(f'| {k} | {m["n"]} | {m["n_short"]} | – | – | – | – | ✗ |')
            continue
        lines.append(f'| {k} | {m["n"]} | {m["n_short"]} | {m["net"]:+.3%} | {m["t"]:+.2f} | '
                     f'{m["half"][0]:+.3%}／{m["half"][1]:+.3%} | {m["rand"]:+.3%} | {"✓" if res["passed"][k] else "✗"} |')
    lines += ['', '## 結論', '', f'K2 研究期{"通過，進考卷" if res["passed"]["K2"] else "沒通過，不進考卷"}。']
    if exam:
        lines += ['', '## 考卷（2025-09-01～2026-10-31，K2，只考一次）', '', '```', json.dumps(exam, ensure_ascii=False, indent=2), '```']
    return '\n'.join(lines) + '\n'
