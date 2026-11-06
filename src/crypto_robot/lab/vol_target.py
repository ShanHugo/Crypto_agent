"""第二十輪：波動目標持有（DESIGN.md 6.11，2026-11-06 營運者同意）。**看過結果後不改**。

只做多，部位＝min(1 倍, 目標波動 ÷ EWMA 預測波動)，每天台北 00:00 決定、差超過 0.1 倍才調；可選停損。
逐小時模擬手續費、資金費率、停損。研究統計用 float（不碰交易路徑）。
"""
from __future__ import annotations

import json
import random
import statistics as st
from dataclasses import dataclass
from typing import Callable

from crypto_robot.lab.futures import FEE, Funding
from crypto_robot.lab.hourly import H, Series, ms

D = 24 * H
FEE_SIDE = FEE / 2
TARGET_VOL, MAX_EXPOSURE, BAND = 0.5, 1.0, 0.1
LAM, WARMUP = 0.94, 30
STOP_PCT, STOP_VOL_K = 0.05, 3.0
DEV = (ms(2021, 1, 1), ms(2024, 1, 1))
DEV_MID = ms(2022, 7, 1)
EXAM_A = (ms(2024, 1, 1), ms(2026, 11, 1))
EXAM_B = (ms(2018, 1, 1), ms(2019, 11, 27))
MAX_DD = -0.60
MIN_PROB, BLOCK, RUNS, SEED = 0.90, 20, 2000, 100


@dataclass(frozen=True)
class Rule:
    name: str
    stop: str | None = None        # None、'pct'、'vol'


RULES = [Rule('vt'), Rule('vt-stop5', 'pct'), Rule('vt-stopvol', 'vol')]


@dataclass
class Result:
    days: list[int]                # 每筆日報酬的結束時點（台北 00:00）
    rets: list[float]              # 台北日報酬
    total: float                   # 扣成本總報酬
    dd: float                      # 最大回撤（逐小時權益，負數）
    avg: float                     # 平均部位（逐小時）


def days(start: int, end: int) -> list[int]:
    """台北 00:00＝UTC 16:00。"""
    t = start // D * D + 16 * H
    return [x for x in range(t, end, D) if x >= start]


def forecasts(eth: Series) -> dict[int, float]:
    """每個台北 00:00 的 EWMA 預測年化波動（只用到當天為止的日報酬）；不滿 WARMUP 天的沒有。"""
    ts = sorted(eth.idx)
    out, rets, var, prev = {}, [], None, None
    for t in days(ts[0], ts[-1] + H + 1):
        i = eth.idx.get(t - H)
        if i is None:
            continue
        c = eth.c[i]
        if prev is not None:
            r = c / prev - 1
            if var is not None:
                var = LAM * var + (1 - LAM) * r * r
            else:
                rets.append(r)
                if len(rets) == WARMUP:
                    var = sum(x * x for x in rets) / WARMUP
            if var is not None:
                out[t] = (var * 365) ** 0.5
        prev = c
    return out


def target(vol: float | None) -> float:
    return 0.0 if not vol else min(MAX_EXPOSURE, TARGET_VOL / vol)


def stop_dist(rule: Rule, vol: float | None) -> float | None:
    """停損距離（比例）；只有有預測時才有 'vol' 版。"""
    if rule.stop == 'pct':
        return STOP_PCT
    if rule.stop == 'vol' and vol:
        return vol / 365 ** 0.5 * STOP_VOL_K
    return None


def run(eth: Series, fund: Funding, start: int, end: int, want: Callable[[int], float],
        stop: Callable[[int], float | None] = lambda t: None, fee_mult: float = 1.0) -> Result:
    """每天 t：先記權益 → 依 want(t) 調部位 → 逐小時（開盤 t … t+23h）檢查停損、算損益、收付資金費率。"""
    rate: dict[int, float] = {}
    for ts, r in zip(fund.ts, fund.rate):
        rate[ts] = rate.get(ts, 0.0) + r
    equity, qty, price = 1.0, 0.0, None
    peak, dd, marks, exposure = 1.0, 0.0, [], 0.0
    points: list[tuple[int, float]] = []
    schedule = days(start, end)
    for t in schedule:
        i = eth.idx.get(t - H)
        if i is None:
            continue
        points.append((t, equity + qty * (eth.c[i] - price) if qty else equity))
        if t == schedule[-1]:          # 最後一天只記權益，不再持有到期間以外
            equity = points[-1][1]
            break
        equity, price = points[-1][1], eth.c[i]
        cur = qty * price / equity
        goal = want(t)
        if abs(goal - cur) > BAND:
            fee = abs(goal - cur) * equity * FEE_SIDE * fee_mult
            equity -= fee
            qty = goal * equity / price
        dist = stop(t)
        level = price * (1 - dist) if dist is not None and qty > 0 else None
        for ts in range(t, t + D, H):
            j = eth.idx.get(ts)
            if j is None:
                continue
            o, l, c = eth.o[j], eth.l[j], eth.c[j]
            if level is not None and qty > 0 and l <= level:
                fill = min(level, o)
                equity += qty * (fill - price)
                equity -= qty * fill * FEE_SIDE * fee_mult
                qty, level = 0.0, None
            equity += qty * (c - price)
            price = c
            if qty and ts + H in rate:
                equity -= qty * c * rate[ts + H]    # 費率為正時多單付錢
            peak = max(peak, equity)
            dd = min(dd, equity / peak - 1)
            marks.append(1)
            exposure += qty * c / equity
    rets = [b[1] / a[1] - 1 for a, b in zip(points, points[1:])]
    return Result([p[0] for p in points[1:]], rets, equity - 1, dd, exposure / len(marks) if marks else 0.0)


def sharpe(rets: list[float]) -> float:
    if len(rets) < 2:
        return 0.0
    sd = st.stdev(rets)
    return 0.0 if sd == 0 else st.fmean(rets) / sd * 365 ** 0.5


def half_sharpes(r: Result, mid: int) -> list[float]:
    return [sharpe([x for t, x in zip(r.days, r.rets) if (t < mid) == first]) for first in (True, False)]


def prob_better(a: list[float], b: list[float], block: int = BLOCK, runs: int = RUNS, seed: int = SEED) -> float:
    """循環區塊 bootstrap（兩邊同一批日子）：a 的 Sharpe 高於 b 的機率。"""
    n = len(a)
    if n < block:
        return 0.0
    rng, wins = random.Random(seed), 0
    for _ in range(runs):
        idx: list[int] = []
        while len(idx) < n:
            s = rng.randrange(n)
            idx += [(s + k) % n for k in range(block)]
        idx = idx[:n]
        wins += sharpe([a[i] for i in idx]) > sharpe([b[i] for i in idx])
    return wins / runs


def evaluate(rule: Rule, eth: Series, fund: Funding, period: tuple[int, int], vols: dict[int, float],
             mid: int | None = None, boot: bool = False) -> dict:
    want = lambda t: target(vols.get(t))                    # noqa: E731
    stop = lambda t: stop_dist(rule, vols.get(t))           # noqa: E731
    r = run(eth, fund, *period, want, stop)
    hold = run(eth, fund, *period, lambda t: 1.0)
    same = run(eth, fund, *period, lambda t: r.avg)
    m = {'total': r.total, 'sharpe': sharpe(r.rets), 'dd': r.dd, 'avg': r.avg, 'hold_total': hold.total,
         'hold_sharpe': sharpe(hold.rets), 'hold_dd': hold.dd, 'same_dd': same.dd}
    if mid is not None:
        m['half'], m['hold_half'] = half_sharpes(r, mid), half_sharpes(hold, mid)
        m['sharpe_fee2'] = sharpe(run(eth, fund, *period, want, stop, fee_mult=2.0).rets)
        m['hold_sharpe_fee2'] = sharpe(run(eth, fund, *period, lambda t: 1.0, fee_mult=2.0).rets)
    if boot:
        m['prob'] = prob_better(r.rets, hold.rets)
    return m


def checks(m: dict) -> dict[str, bool]:
    """研究期 7 項（DESIGN 6.11）。"""
    return {'賺錢': m['total'] > 0, 'Sharpe': m['sharpe'] > m['hold_sharpe'],
            '前後半': all(a > b for a, b in zip(m['half'], m['hold_half'])),
            '比同部位淺': m['dd'] > m['same_dd'], f'回撤≤{MAX_DD:.0%}': m['dd'] >= MAX_DD,
            '費用加倍': m['sharpe_fee2'] > m['hold_sharpe_fee2'], f'機率≥{MIN_PROB:.0%}': m['prob'] >= MIN_PROB}


def dev_passed(m: dict) -> bool:
    return all(checks(m).values())


def exam_a_passed(m: dict) -> bool:
    return m['total'] > 0 and m['sharpe'] > m['hold_sharpe'] and m['dd'] > m['same_dd'] and m['dd'] >= MAX_DD


def exam_b_passed(m: dict) -> bool:
    return m['sharpe'] > m['hold_sharpe'] and m['dd'] > m['same_dd']


def run_dev(eth: Series, fund: Funding) -> dict:
    vols = forecasts(eth)
    res = {r.name: evaluate(r, eth, fund, DEV, vols, DEV_MID, boot=True) for r in RULES}
    good = [k for k, m in res.items() if dev_passed(m)]
    return {'metrics': res, 'passed': good, 'pick': max(good, key=lambda k: res[k]['sharpe'], default=None)}


def run_exam(name: str, perp: Series, fund: Funding, spot: Series) -> dict:
    rule = next(r for r in RULES if r.name == name)
    a = evaluate(rule, perp, fund, EXAM_A, forecasts(perp))
    b = evaluate(rule, spot, Funding([]), EXAM_B, forecasts(spot))
    return {'rule': name, 'a': a, 'b': b, 'a_passed': exam_a_passed(a), 'b_passed': exam_b_passed(b),
            'passed': exam_a_passed(a) and exam_b_passed(b)}


def report(res: dict, when: str, exam: dict | None = None) -> str:
    lines = ['# 第二十輪：波動目標持有（研究期 2021-01-01～2023-12-31，Binance ETHUSDT 合約）', '',
             f'產生時間 {when}。只做多；部位＝min({MAX_EXPOSURE:g} 倍, {TARGET_VOL:.0%} ÷ EWMA 預測波動）；每天台北 00:00 決定、差超過 {BAND:g} 倍才調；單邊手續費 {FEE_SIDE:.2%}＋實際資金費率。', '',
             '| 規則 | 總報酬 | Sharpe | 最大回撤 | 平均部位 | 同部位固定持有回撤 | 前半／後半 Sharpe | 費用加倍 Sharpe | 機率 |', '|---|---|---|---|---|---|---|---|---|']
    for k, m in res['metrics'].items():
        lines.append(f'| {k} | {m["total"]:+.1%} | {m["sharpe"]:.2f} | {m["dd"]:.1%} | {m["avg"]:.2f} 倍 | {m["same_dd"]:.1%} | '
                     f'{m["half"][0]:.2f}／{m["half"][1]:.2f} | {m["sharpe_fee2"]:.2f} | {m["prob"]:.0%} |')
    m = next(iter(res['metrics'].values()))
    lines.append(f'| 一直持有 1 倍 | {m["hold_total"]:+.1%} | {m["hold_sharpe"]:.2f} | {m["hold_dd"]:.1%} | 1.00 倍 | – | '
                 f'{m["hold_half"][0]:.2f}／{m["hold_half"][1]:.2f} | {m["hold_sharpe_fee2"]:.2f} | – |')
    lines += ['', '## 每組 7 項標準', '', '| 規則 | ' + ' | '.join(checks(m)) + ' |', '|---|' + '---|' * len(checks(m))]
    for k, x in res['metrics'].items():
        lines.append(f'| {k} | ' + ' | '.join('✓' if v else '✗' for v in checks(x).values()) + ' |')
    end = f'{res["pick"]} 研究期通過，進考卷。' if res['pick'] else '沒有規則通過，不進考卷。'
    lines += ['', '## 結論', '', end]
    if exam:
        lines += ['', '## 考卷（只考一次）', '', '```', json.dumps(exam, ensure_ascii=False, indent=2), '```']
    return '\n'.join(lines) + '\n'
