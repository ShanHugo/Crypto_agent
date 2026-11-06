"""第十九輪：自己訓練小模型（DESIGN.md 6.10，2026-11-06 營運者同意）。**看過結果後不改**。

這裡只做主環境的部分（純 Python，不需要 numpy）：
- 每天台北 00:00 算 16 項特徵（只用那時已收盤的資料）寫成 CSV，給 scripts/own_model_train.py（_vendor/ml_venv）逐月重訓；
- 讀預測結果，照第十八輪的交易規則、第十四輪的通過標準評分。
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
FEATURES = ['ret_1d', 'ret_3d', 'ret_7d', 'ret_28d', 'ewma_vol', 'range_1d', 'vol_ratio', 'btc_1d', 'btc_7d',
            'fund_last', 'fund_7d', 'premium', 'stable_4w', 'netliq_4w', 'dxy_4w', 'real_4w']
TRAIN_START = ms(2020, 1, 1)
DEV = (ms(2022, 1, 1), ms(2025, 9, 1))
DEV_MID = ms(2023, 11, 1)
EXAM = (ms(2025, 9, 1), ms(2026, 11, 1))
MODELS = ['ridge', 'gbm']
CONTROLS = ['ridge_shuf', 'gbm_shuf']
MIN_N, MIN_T, STRESS = 100, 3.0, 0.001
LAG = W                            # 總經與穩定幣：一律用一週前已有的值
EWMA_LAM, EWMA_WARMUP, EWMA_DAYS = 0.94, 30, 120


def days(start: int, end: int) -> list[int]:
    """台北 00:00＝UTC 16:00。"""
    t = start // D * D + 16 * H
    return [x for x in range(t, end, D) if x >= start]


def ewma_vol(rets: list[float]) -> float:
    """台北日報酬（舊到新）的 EWMA 年化波動；同 futures_agent.ewma_vol，float 版。"""
    var = sum(r * r for r in rets[:EWMA_WARMUP]) / EWMA_WARMUP
    for r in rets[EWMA_WARMUP:]:
        var = EWMA_LAM * var + (1 - EWMA_LAM) * r * r
    return (var * 365) ** 0.5


def features(t: int, eth: Series, btc: Series, fund: Funding, prem: Macro, m: dict[str, Macro]) -> list[float] | None:
    """決策時點 t（那一刻已收盤的最後一根 K 線開盤是 t − 1h）已知的 16 項；缺任何一項就 None。"""
    i = eth.span(t - H - 28 * D, t - H)
    if i is None:
        return None
    last = i + 28 * 24                                 # t − 1h 那根
    c = eth.c[last]
    ret = lambda n: c / eth.c[last - n * 24] - 1       # noqa: E731
    closes = [eth.c[j] for j in range(last - 24 * EWMA_DAYS, last + 1, 24) if j >= 0 and eth.idx.get(t - H - (last - j) * H) == j]
    if len(closes) <= EWMA_WARMUP + 1:
        return None
    rets = [b / a - 1 for a, b in zip(closes, closes[1:])]
    vol7, vol28 = sum(eth.v[last - 7 * 24 + 1:last + 1]) / 7, sum(eth.v[last - 28 * 24 + 1:last + 1]) / 28
    if vol28 == 0:
        return None

    b = [btc.idx.get(t - H - n * D) for n in (0, 1, 7)]
    if None in b:
        return None
    a = bisect.bisect_right(fund.ts, t - W)
    z = bisect.bisect_right(fund.ts, t)
    p = bisect.bisect_right(prem.ts, t - H) - 1        # 溢價指數 1h K 開盤 t − 1h、收盤 t
    if z == 0 or z - a < 15 or p < 0 or prem.ts[p] != t - H:
        return None

    def net(ts: int) -> float | None:
        x = [m[n].asof(ts) for n in ('WALCL', 'WTREGEN', 'RRPONTSYD')]
        return None if None in x else x[0] - x[1] - x[2] * 1000
    now, old = t - LAG, t - LAG - 4 * W
    v = {n: (m[n].asof(now), m[n].asof(old)) for n in ('stablecoins', 'DTWEXBGS', 'DFII10')}
    n1, n0 = net(now), net(old)
    if any(None in x for x in v.values()) or n1 is None or n0 is None or n0 == 0:
        return None
    return [ret(1), ret(3), ret(7), ret(28), ewma_vol(rets),
            (max(eth.h[last - 23:last + 1]) - min(eth.l[last - 23:last + 1])) / c, vol7 / vol28,
            btc.c[b[0]] / btc.c[b[1]] - 1, btc.c[b[0]] / btc.c[b[2]] - 1,
            fund.rate[z - 1], st.fmean(fund.rate[a:z]), prem.v[p],
            v['stablecoins'][0] / v['stablecoins'][1] - 1, (n1 - n0) / abs(n0),
            v['DTWEXBGS'][0] / v['DTWEXBGS'][1] - 1, v['DFII10'][0] - v['DFII10'][1]]


def label(t: int, eth: Series) -> float | None:
    """t 進場、持有 24 小時的漲跌（訓練用的答案，不含成本）。"""
    i = eth.span(t - H, t + 23 * H)
    return None if i is None else eth.c[i + 24] / eth.c[i] - 1


def write_csv(path: Path, eth: Series, btc: Series, fund: Funding, m: dict[str, Macro]) -> dict:
    """data/own_model/features.csv：ts, ret, 16 項（缺的留空）；特徵是原始值，標準化在逐月重訓時用訓練資料算。"""
    prem, ts = m['premium_ethusdt_1h'], days(TRAIN_START, EXAM[1])
    path.parent.mkdir(parents=True, exist_ok=True)
    missing = 0
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['ts', 'ret'] + FEATURES)
        for t in ts:
            x, y = features(t, eth, btc, fund, prem, m), label(t, eth)
            missing += x is None
            w.writerow([t, '' if y is None else y] + ([''] * len(FEATURES) if x is None else x))
    return {'rows': len(ts), 'missing': missing, 'features': len(FEATURES)}


def read_preds(path: Path) -> dict[str, dict[int, float]]:
    out: dict[str, dict[int, float]] = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            r = json.loads(line)
            out.setdefault(r['model'], {})[r['ts']] = r['pred']
    return out


def direction(pred: float) -> int:
    return 1 if pred > FEE else -1 if pred < -FEE else 0


def simulate(preds: dict[int, float], eth: Series, fund: Funding, start: int, end: int):
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


def dev_passed(m: dict) -> bool:
    return ('net' in m and m['n'] >= MIN_N and m['net'] > 0 and m['t'] >= MIN_T and m['net'] > m['rand']
            and min(m['half']) > 0 and m['net'] - STRESS > 0)


def exam_passed(m: dict) -> bool:
    return 'net' in m and m['net'] > 0 and m['net'] > m['rand'] and min(m['half']) > 0


def leaked(m: dict) -> bool:
    """打亂答案的對照組不該有訊號；t 絕對值 ≥ 3 代表程式有洩漏。"""
    return abs(m.get('t', 0.0)) >= MIN_T


def run_dev(preds: dict, eth: Series, fund: Funding) -> dict:
    res = {k: futures.evaluate(*simulate(preds.get(k, {}), eth, fund, *DEV), DEV_MID) for k in MODELS + CONTROLS}
    leak = [k for k in CONTROLS if leaked(res[k])]
    passed = {k: not leak and dev_passed(res[k]) for k in MODELS}
    pick = max((k for k in MODELS if passed[k]), key=lambda k: res[k]['t'], default=None)
    return {'metrics': res, 'passed': passed, 'leak': leak, 'pick': pick, 'days': len(days(*DEV)),
            'have': {k: sum(DEV[0] <= t < DEV[1] for t in preds.get(k, {})) for k in MODELS + CONTROLS}}


def run_exam(preds: dict, eth: Series, fund: Funding, name: str) -> dict:
    trades, pool = simulate(preds.get(name, {}), eth, fund, *EXAM)
    mid = trades[len(trades) // 2][0] if trades else 0
    m = futures.evaluate(trades, pool, mid, pct=0.90)
    return {**m, 'model': name, 'passed': exam_passed(m)}


def report(res: dict, when: str, exam: dict | None = None) -> str:
    lines = ['# 第十九輪：自己訓練小模型（研究期 2022-01-01～2025-08-31，Binance ETHUSDT 合約）', '',
             f'產生時間 {when}。每月用過去的資料重訓；每天台北 00:00 預測 24 小時，> {FEE:.2%} 做多、< -{FEE:.2%} 做空；持有 24 小時；成本：來回 {FEE:.2%}＋實際資金費率。',
             f'研究期 {res["days"]} 天，已預測：' + '、'.join(f'{k} {res["have"][k]} 天' for k in MODELS + CONTROLS), '',
             f'通過：交易 ≥ {MIN_N}、每筆淨報酬 > 0、t（扣同方向每天都做的平均）≥ {MIN_T:g}、贏隨機第 95 百分位、前後半都 > 0、每筆成本再加 {STRESS:.2%} 仍 > 0。', '',
             '| 模型 | 交易 | 其中做空 | 每筆淨報酬 | t | 前半／後半 | 隨機95 | 過 |', '|---|---|---|---|---|---|---|---|']
    for k in MODELS + CONTROLS:
        m = res['metrics'][k]
        ok = '對照' if k in CONTROLS else '✓' if res['passed'][k] else '✗'
        if 'net' not in m:
            lines.append(f'| {k} | {m["n"]} | {m["n_short"]} | – | – | – | – | {ok} |')
            continue
        lines.append(f'| {k} | {m["n"]} | {m["n_short"]} | {m["net"]:+.3%} | {m["t"]:+.2f} | '
                     f'{m["half"][0]:+.3%}／{m["half"][1]:+.3%} | {m["rand"]:+.3%} | {ok} |')
    if res['leak']:
        end = f'對照組 {"、".join(res["leak"])} 的 t 絕對值 ≥ {MIN_T:g}：程式可能有洩漏，**整輪作廢**，查錯後另訂日期再跑。'
    elif res['pick']:
        end = f'{res["pick"]} 研究期通過，進考卷。'
    else:
        end = '沒有模型通過，不進考卷。'
    lines += ['', '## 結論', '', end]
    if exam:
        lines += ['', '## 考卷（2025-09-01～2026-10-31，只考一次）', '', '```', json.dumps(exam, ensure_ascii=False, indent=2), '```']
    return '\n'.join(lines) + '\n'
