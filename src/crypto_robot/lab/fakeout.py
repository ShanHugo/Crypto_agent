"""第十三輪：跌破 24 小時低點後做多，怎麼出場（DESIGN.md 6.3，2026-11-02 使用者同意草案）。**看過結果後不改**。

判斷時點同第十二輪（台北 00、03…21 點）；收盤價進場、一次只抱一筆。MAX 與 Binance 各算一次，兩邊都要通過。
研究統計用 float（不碰交易路徑）。
"""
from __future__ import annotations

import json
import random
import statistics as st
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from crypto_robot.lab.direction3h import DATASETS, DEV_END, DEV_MID, DEV_START, FEE, H, STRESS, Series, slots, _ms

HOLDOUT_SETS = {                                # 考卷：三份都沒用過，只考一次
    'binance_2024+': ('binance', DEV_END, None),
    'max_2018-2020': ('max', 0, DEV_START),
    'binance_2017-2020': ('binance', 0, DEV_START),
}
RANDOM_RUNS = 1000
MIN_N, MIN_T = 100, 3.0
HO_N = 50


@dataclass(frozen=True)
class Variant:
    entry: str                      # break：跌破；confirm：上一個時點跌破、這次收回；deep：跌破超過 1%
    exit: str                       # hold：抱 hours 小時；tpsl：停利 tp／停損 sl，最多 24 小時
    hours: int = 24
    tp: float = 0.0
    sl: float = 0.0

    @property
    def name(self) -> str:
        return f'{self.entry}-hold{self.hours}' if self.exit == 'hold' else f'{self.entry}-tp{self.tp:.0%}-sl{self.sl:.0%}'


VARIANTS = ([Variant('break', 'hold', h) for h in (3, 6, 12, 24)]
            + [Variant('break', 'tpsl', tp=tp, sl=sl) for tp in (0.01, 0.02) for sl in (0.01, 0.02)]
            + [v for e in ('confirm', 'deep') for v in (Variant(e, 'hold', 6), Variant(e, 'tpsl', tp=0.02, sl=0.01))])


def _lo24(eth: Series, i: int) -> float:
    return min(eth.l[i - 24:i])


def entry_ok(eth: Series, i: int, kind: str) -> bool:
    """i 是判斷時點前最後一根已收盤的 K 線。"""
    c = eth.c
    if kind == 'break':
        return c[i] < _lo24(eth, i)
    if kind == 'deep':
        return c[i] < _lo24(eth, i) * 0.99
    j = i - 3
    return c[j] < _lo24(eth, j) and c[i] > _lo24(eth, j)


def exit_ret(eth: Series, i: int, v: Variant) -> tuple[float, int]:
    """在 i 收盤買進，回傳（未扣成本報酬, 抱了幾小時）。同一根同時碰到停利停損，算停損（保守）。"""
    p = eth.c[i]
    if v.exit == 'hold':
        return eth.c[i + v.hours] / p - 1, v.hours
    for k in range(1, 25):
        if eth.l[i + k] <= p * (1 - v.sl):
            return -v.sl, k
        if eth.h[i + k] >= p * (1 + v.tp):
            return v.tp, k
    return eth.c[i + 24] / p - 1, 24


def simulate(eth: Series, v: Variant, start: int, end: int) -> tuple[list[tuple[int, float]], list[float]]:
    """回傳（交易 [(時點, 扣成本報酬)], 每個時點都進場的扣成本報酬＝隨機基準母體）。"""
    trades, pool, free = [], [], 0
    for s in slots(start, end):
        i0 = eth.span(s - 28 * H, s + 23 * H)
        if i0 is None:
            continue
        i = i0 + 27
        r, held = exit_ret(eth, i, v)
        pool.append(r - FEE)
        if s >= free and entry_ok(eth, i, v.entry):
            trades.append((s, r - FEE))
            free = s + held * H
    return trades, pool


def evaluate(trades: list[tuple[int, float]], pool: list[float], mid: int, pct: float = 0.95, seed: int = 0) -> dict:
    n = len(trades)
    out = {'n': n}
    if n < 2 or not pool:
        return out
    nets = [x for _, x in trades]
    base = st.fmean(pool)
    ex = [x - base for x in nets]
    sd = st.stdev(ex)
    out['net'] = st.fmean(nets)
    out['base'] = base
    out['t'] = 0.0 if sd == 0 else st.fmean(ex) / (sd / n ** 0.5)
    halves = [[x for s, x in trades if (s < mid) == first] for first in (True, False)]
    out['half'] = [st.fmean(h) if h else 0.0 for h in halves]
    rng = random.Random(seed)
    means = sorted(st.fmean(rng.sample(pool, min(n, len(pool)))) for _ in range(RANDOM_RUNS))
    out['rand'] = means[int(RANDOM_RUNS * pct)]
    return out


def passed(m: dict) -> bool:
    return (m['n'] >= MIN_N and m['net'] > 0 and m['t'] >= MIN_T and min(m['half']) > 0
            and m['net'] > m['rand'] and m['net'] - STRESS > 0)


def ho_passed(m: dict) -> bool:
    return m['n'] >= HO_N and m['net'] > 0 and m['net'] > m['rand']


def run_dev(eth: dict[str, Series]) -> dict:
    res: dict = {ds: {} for ds in eth}
    for v in VARIANTS:
        for ds, e in eth.items():
            res[ds][v.name] = evaluate(*simulate(e, v, DEV_START, DEV_END), DEV_MID)
    res['passed'] = {v.name: all(passed(res[ds][v.name]) for ds in eth) for v in VARIANTS}
    return res


def pick(res: dict) -> str | None:
    ok = [n for n, p in res['passed'].items() if p]
    return max(ok, key=lambda n: min(res[ds][n]['t'] for ds in DATASETS if ds in res), default=None)


def run_holdout(eth: dict[str, Series], name: str, path: Path, commit: str) -> dict:
    if path.exists():
        raise RuntimeError('考卷已經考過')
    v = next(x for x in VARIANTS if x.name == name)
    record = {'variant': name, 'commit': commit, 'at': datetime.now(timezone.utc).isoformat(), 'sets': {}}
    for label, (ds, start, end) in HOLDOUT_SETS.items():
        trades, pool = simulate(eth[ds], v, start, end if end else _ms(2100, 1, 1))
        mid = trades[len(trades) // 2][0] if trades else 0
        m = evaluate(trades, pool, mid, pct=0.90)
        record['sets'][label] = {**m, 'passed': ho_passed(m)}
    record['passed'] = all(x['passed'] for x in record['sets'].values())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return record


def dev_report(res: dict, when: str) -> str:
    lines = ['# 第十三輪：跌破 24h 低點後做多（研究期 2021-01-01～2023-12-31）', '',
             f'產生時間 {when}。成本：來回 {FEE:.2%}，壓力再加 {STRESS:.1%}。「隨機」是每個時點都用同樣出場方式的平均。', '',
             f'通過（兩邊都要）：交易 ≥ {MIN_N}、扣成本平均 > 0、減隨機平均後 t ≥ {MIN_T:g}、前後半都 > 0、贏隨機第 95 百分位、加滑價仍 > 0。', '']
    for ds in DATASETS:
        lines += [f'## {ds}', '', '| 組合 | 交易 | 每筆淨報酬 | 隨機平均 | t | 前半／後半 | 隨機95 | 過 |', '|---|---|---|---|---|---|---|---|']
        for v in VARIANTS:
            m = res[ds][v.name]
            if m['n'] < 2:
                lines.append(f'| {v.name} | {m["n"]} | – | – | – | – | – | ✗ |')
                continue
            lines.append(f'| {v.name} | {m["n"]} | {m["net"]:+.3%} | {m["base"]:+.3%} | {m["t"]:+.2f} | '
                         f'{m["half"][0]:+.3%}／{m["half"][1]:+.3%} | {m["rand"]:+.3%} | {"✓" if passed(m) else "✗"} |')
        lines.append('')
    ok = [n for n, p in res['passed'].items() if p]
    lines += ['## 結論', '', f'兩邊都通過：{"、".join(ok) if ok else "沒有"}。' + (f'考卷要考：{pick(res)}。' if ok else '不進考卷。')]
    return '\n'.join(lines) + '\n'
