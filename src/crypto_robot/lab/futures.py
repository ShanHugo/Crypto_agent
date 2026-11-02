"""第十四輪：Binance ETHUSDT 永續合約，資金費率反向＋假突破對照（DESIGN.md 6.3，2026-11-02 使用者同意草案）。**看過結果後不改**。

1 倍、可多可空、一次只抱一筆；在判斷時點的收盤價進出。成本：來回 taker 0.10%＋持倉期間實際的資金費率（多單付、空單收）。
研究統計用 float（不碰交易路徑）。
"""
from __future__ import annotations

import bisect
import json
import random
import statistics as st
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from crypto_robot.lab.hourly import DEV_END, DEV_MID, DEV_START, H, Series, ms as _ms, slots

MARKET = 'binancef_ethusdt'
FEE = 0.0010
STRESS = 0.001
RANDOM_RUNS = 1000
LOOKBACK = 90                      # 過去 30 天＝90 期資金費率（不含這一期）
MIN_N, MIN_T, HO_N = 100, 3.0, 50
HOLDOUT_SETS = {'2024+': (DEV_END, _ms(2100, 1, 1)), '2019-11~2020': (0, DEV_START)}   # 都沒用過，只考一次


@dataclass(frozen=True)
class Variant:
    kind: str                      # fund_ext：費率 > 第 90 百分位做空、< 第 10 做多；fund_streak：連 3 期 > 第 75 做空、< 第 25 做多；fake：突破 24h 高做空、跌破低做多
    hours: int

    @property
    def name(self) -> str:
        return f'{self.kind}-hold{self.hours}'


VARIANTS = [Variant('fund_ext', 8), Variant('fund_ext', 24), Variant('fund_streak', 8), Variant('fund_streak', 24),
            Variant('fake', 3), Variant('fake', 6)]


class Funding:
    def __init__(self, rows: list[tuple[int, float]]):
        rows = sorted((ts // H * H, r) for ts, r in rows)
        self.ts = [t for t, _ in rows]
        self.rate = [r for _, r in rows]

    def between(self, a: int, b: int) -> float:
        """(a, b] 之間結算的費率合計。"""
        return sum(self.rate[bisect.bisect_right(self.ts, a):bisect.bisect_right(self.ts, b)])


def _q(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[int(p * (len(s) - 1))]


def signal(v: Variant, eth: Series, fund: Funding, s: int, k: int | None) -> int:
    """時點 s 的方向（+1 多、-1 空、0 不做）。k 是 s 那一期資金費率的位置（fake 不用）。"""
    if v.kind == 'fake':
        i = eth.idx[s - H]
        now = eth.c[i]
        return -1 if now > max(eth.h[i - 24:i]) else 1 if now < min(eth.l[i - 24:i]) else 0
    hist, r = fund.rate[k - LOOKBACK:k], fund.rate[k]
    if v.kind == 'fund_ext':
        return -1 if r > _q(hist, 0.9) else 1 if r < _q(hist, 0.1) else 0
    last3 = fund.rate[k - 2:k + 1]
    hi, lo = _q(fund.rate[k - 2 - LOOKBACK:k - 2], 0.75), _q(fund.rate[k - 2 - LOOKBACK:k - 2], 0.25)
    return -1 if all(x > hi for x in last3) else 1 if all(x < lo for x in last3) else 0


def points(v: Variant, fund: Funding, start: int, end: int) -> list[tuple[int, int | None]]:
    if v.kind == 'fake':
        return [(s, None) for s in slots(start, end)]
    return [(t, k) for k, t in enumerate(fund.ts) if start <= t < end and k >= LOOKBACK + 2]


def trade_net(eth: Series, fund: Funding, s: int, hours: int, d: int) -> float:
    i = eth.idx[s - H]
    gross = d * (eth.c[i + hours] / eth.c[i] - 1)
    return gross - d * fund.between(s, s + hours * H) - FEE


def simulate(eth: Series, fund: Funding, v: Variant, start: int, end: int):
    """回傳（交易 [(時點, 方向, 淨報酬)], 母體 {方向: [每個時點都做的淨報酬]}）。"""
    trades, pool, free = [], {1: [], -1: []}, 0
    for s, k in points(v, fund, start, end):
        if eth.span(s - 25 * H, s + (v.hours - 1) * H) is None:
            continue
        for d in (1, -1):
            pool[d].append(trade_net(eth, fund, s, v.hours, d))
        if s >= free and (d := signal(v, eth, fund, s, k)):
            trades.append((s, d, trade_net(eth, fund, s, v.hours, d)))
            free = s + v.hours * H
    return trades, pool


def evaluate(trades, pool, mid: int, pct: float = 0.95, seed: int = 0) -> dict:
    n = len(trades)
    out = {'n': n, 'n_short': sum(d < 0 for _, d, _ in trades)}
    if n < 2 or not pool[1]:
        return out
    nets = [x for *_, x in trades]
    base = {d: st.fmean(p) for d, p in pool.items()}
    ex = [x - base[d] for _, d, x in trades]
    sd = st.stdev(ex)
    out['net'] = st.fmean(nets)
    out['t'] = 0.0 if sd == 0 else st.fmean(ex) / (sd / n ** 0.5)
    halves = [[x for s, _, x in trades if (s < mid) == first] for first in (True, False)]
    out['half'] = [st.fmean(h) if h else 0.0 for h in halves]
    rng, dirs, m = random.Random(seed), [d for _, d, _ in trades], len(pool[1])
    means = []
    for _ in range(RANDOM_RUNS):
        idx = rng.sample(range(m), min(n, m))
        means.append(st.fmean(pool[d][j] for d, j in zip(dirs, idx)))
    out['rand'] = sorted(means)[int(RANDOM_RUNS * pct)]
    return out


def passed(m: dict) -> bool:
    return (m['n'] >= MIN_N and m['net'] > 0 and m['t'] >= MIN_T and min(m['half']) > 0
            and m['net'] > m['rand'] and m['net'] - STRESS > 0)


def ho_passed(m: dict) -> bool:
    return m['n'] >= HO_N and m['net'] > 0 and m['net'] > m['rand']


def run_dev(eth: Series, fund: Funding) -> dict:
    res = {v.name: evaluate(*simulate(eth, fund, v, DEV_START, DEV_END), DEV_MID) for v in VARIANTS}
    return {'metrics': res, 'passed': {n: passed(m) for n, m in res.items()}}


def pick(res: dict) -> str | None:
    ok = [n for n, p in res['passed'].items() if p]
    return max(ok, key=lambda n: res['metrics'][n]['t'], default=None)


def run_holdout(eth: Series, fund: Funding, name: str, path: Path, commit: str) -> dict:
    if path.exists():
        raise RuntimeError('考卷已經考過')
    v = next(x for x in VARIANTS if x.name == name)
    record = {'variant': name, 'commit': commit, 'at': datetime.now(timezone.utc).isoformat(), 'sets': {}}
    for label, (start, end) in HOLDOUT_SETS.items():
        trades, pool = simulate(eth, fund, v, start, end)
        mid = trades[len(trades) // 2][0] if trades else 0
        m = evaluate(trades, pool, mid, pct=0.90)
        record['sets'][label] = {**m, 'passed': ho_passed(m)}
    record['passed'] = all(x['passed'] for x in record['sets'].values())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return record


def dev_report(res: dict, when: str) -> str:
    lines = ['# 第十四輪：Binance ETHUSDT 永續合約（研究期 2021-01-01～2023-12-31）', '',
             f'產生時間 {when}。1 倍、可多可空。成本：來回 {FEE:.2%}＋持倉期間實際資金費率；壓力再加 {STRESS:.1%}。', '',
             f'通過：交易 ≥ {MIN_N}、扣成本平均 > 0、減同方向隨機平均後 t ≥ {MIN_T:g}、前後半都 > 0、贏隨機第 95 百分位、加滑價仍 > 0。', '',
             '| 組合 | 交易 | 其中做空 | 每筆淨報酬 | t | 前半／後半 | 隨機95 | 過 |', '|---|---|---|---|---|---|---|---|']
    for v in VARIANTS:
        m = res['metrics'][v.name]
        if m['n'] < 2:
            lines.append(f'| {v.name} | {m["n"]} | {m["n_short"]} | – | – | – | – | ✗ |')
            continue
        lines.append(f'| {v.name} | {m["n"]} | {m["n_short"]} | {m["net"]:+.3%} | {m["t"]:+.2f} | '
                     f'{m["half"][0]:+.3%}／{m["half"][1]:+.3%} | {m["rand"]:+.3%} | {"✓" if passed(m) else "✗"} |')
    ok = [n for n, p in res['passed'].items() if p]
    lines += ['', '## 結論', '', f'通過：{"、".join(ok) if ok else "沒有"}。' + (f'考卷要考：{pick(res)}。' if ok else '不進考卷。')]
    return '\n'.join(lines) + '\n'
