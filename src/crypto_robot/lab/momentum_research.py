"""第十輪研究（山寨幣追漲幅前幾名）：每週一個組合，報告統計用 float（只做報告，不碰交易路徑）。

- 第 d 天（週一，UTC）收盤排名，d+1 開盤進場、d+8 開盤出場（一週）。
- 可選的幣：前 30 天（含 d）每天都有資料、成交額中位數 ≥ MIN_LIQ，回看那天與進出場的日 K 都在。
- 成本只算換手：掉出名單的賣、新進名單的買，各付一邊（taker＋滑價）× 1/K。
  shortcut: 留在名單的幣不重新調成等金額（權重漂移忽略）。
- 可選的幣少於 2K 個的週持有現金（報酬 0；手上的幣全賣）；這種週不算進超額報酬的統計。
"""
from __future__ import annotations

import math
import random
import statistics
from decimal import Decimal

from crypto_robot.core.models import Bar
from crypto_robot.lab.metrics import max_drawdown, t_stat
from crypto_robot.lab.momentum_variants import (
    BENCH, DAY_MS, LIQ_DAYS, MIN_LIQ, RANDOM_RUNS, SLIPPAGE, STRESS_SLIPPAGE, TAKER_FEE, MomentumVariant, passes_dev,
)

Week = tuple[int, dict[str, float], dict[str, float]]     # (週一 ts, 可選幣的回看漲幅, 可選幣的持有一週報酬)


def mondays(start_ms: int, end_ms: int) -> list[int]:
    d = start_ms + (-(start_ms // DAY_MS + 3) % 7) * DAY_MS      # 1970-01-01 是週四
    out = []
    while d + 8 * DAY_MS < end_ms:                             # 出場那天的開盤也要在區間內
        out.append(d)
        d += 7 * DAY_MS
    return out


def weeks(data: dict[str, dict[int, Bar]], lookback: int, start_ms: int, end_ms: int) -> list[Week]:
    out = []
    for d in mondays(start_ms, end_ms):
        mom, ret = {}, {}
        for c, m in data.items():
            if c == BENCH:
                continue
            past = [m.get(d - k * DAY_MS) for k in range(LIQ_DAYS)]
            back, a, b = m.get(d - lookback * DAY_MS), m.get(d + DAY_MS), m.get(d + 8 * DAY_MS)
            if not all(past) or not (back and a and b) or back.close <= 0:
                continue
            if statistics.median(x.volume * x.close for x in past) < MIN_LIQ:
                continue
            mom[c] = float(past[0].close / back.close) - 1
            ret[c] = float(b.open / a.open) - 1
        out.append((d, mom, ret))
    return out


def side_cost(slippage: Decimal) -> float:
    return float(TAKER_FEE + slippage)


def simulate(ws: list[Week], k: int, pick, cost: float) -> list[tuple[int, float, bool]]:
    """pick(week) → 這週要持有的幣（list）。回傳每週 (ts, 扣成本後報酬, 有沒有交易)。"""
    held: set[str] = set()
    out = []
    for w in ws:
        d, mom, ret = w
        if len(mom) < 2 * k:
            out.append((d, -len(held) / k * cost if held else 0.0, False))
            held = set()
            continue
        sel = set(pick(w))
        turnover = len(held - sel) + len(sel - held)
        out.append((d, statistics.fmean(ret[c] for c in sel) - turnover / k * cost, True))
        held = sel
    return out


def compound(rs: list[float]) -> float:
    return math.prod(1 + r for r in rs) - 1


def curve(rs: list[float]) -> list[float]:
    eq, out = 1.0, []
    for r in rs:
        eq *= 1 + r
        out.append(eq)
    return out


def top_k(k: int):
    return lambda w: sorted(w[1], key=lambda c: (-w[1][c], c))[:k]


def evaluate(data: dict[str, list[Bar]], v: MomentumVariant, start_ms: int, end_ms: int, mid_ms: int,
             runs: int = RANDOM_RUNS, seed: int = 7) -> dict:
    by_ts = {c: {b.ts: b for b in bars} for c, bars in data.items()}
    ws = weeks(by_ts, v.lookback, start_ms, end_ms)
    k, cost = v.top, side_cost(SLIPPAGE)
    port = simulate(ws, k, top_k(k), cost)
    stress = simulate(ws, k, top_k(k), side_cost(STRESS_SLIPPAGE))
    basket = [statistics.fmean(w[2].values()) if traded else 0.0 for w, (_, _, traded) in zip(ws, port)]
    eth = by_ts[BENCH]
    eth_r = [float(eth[d + 8 * DAY_MS].open / eth[d + DAY_MS].open) - 1 if eth.get(d + DAY_MS) and eth.get(d + 8 * DAY_MS)
             else 0.0 for d, _, _ in ws]
    excess = [(d, r - b) for (d, r, traded), b in zip(port, basket) if traded]
    xs = [x for _, x in excess]
    mean = lambda a: statistics.fmean(a) if a else 0.0
    total = compound([r for _, r, _ in port])
    rng = random.Random(seed)
    rand_pick = lambda w: rng.sample(sorted(w[1]), k)
    beat = sum(total > compound([r for _, r, _ in simulate(ws, k, rand_pick, cost)]) for _ in range(runs)) / runs
    picks: dict[str, int] = {}
    for w, (_, _, traded) in zip(ws, port):
        if traded:
            for c in top_k(k)(w):
                picks[c] = picks.get(c, 0) + 1
    r = {
        'variant': v.name, 'weeks': len(ws), 'traded': len(xs), 'total': total,
        'basket_total': compound(basket), 'eth_total': compound(eth_r), 'stress_total': compound([x for _, x, _ in stress]),
        'mean_excess': mean(xs), 't': t_stat(xs),
        'halves': [mean([x for d, x in excess if d < mid_ms]), mean([x for d, x in excess if d >= mid_ms])],
        'random_pct': beat, 'mdd': max_drawdown([1.0] + curve([r for _, r, _ in port])),
        'eth_mdd': max_drawdown([1.0] + curve(eth_r)), 'picks': dict(sorted(picks.items(), key=lambda x: -x[1])),
    }
    r['passed'], r['fails'] = passes_dev(r['traded'], r['t'], r['halves'], beat, r['stress_total'], r['basket_total'],
                                         r['mdd'], r['eth_mdd'])
    return r


def dev_report(rows: list[dict], generated: str) -> str:
    from crypto_robot.lab.common import day, pct
    from crypto_robot.lab.momentum_variants import DEV_END, DEV_MID, DEV_START, MIN_T, MIN_WEEKS, RANDOM_PCT
    lines = [
        f'# 研究期報告（第十輪，山寨幣追漲幅前幾名）（{generated}）', '',
        f'- 研究期 {day(DEV_START)} ～ {day(DEV_END - 1)}，前半／後半以 {day(DEV_MID)} 切開；MAX 台幣市場日 K（UTC）。',
        f'- 每週一收盤，在前 30 天每日成交額中位數 ≥ {MIN_LIQ:,.0f} 元的幣裡挑過去 L 天漲最多的前 K 名，隔天開盤等金額買、持有一週；'
        f'可選的幣少於 2K 個就持有現金。成本只算換手：taker {TAKER_FEE:%}＋滑價每邊 {SLIPPAGE:%}。',
        '- 超額＝每週扣成本後報酬 − 同一週全部可選幣等權重（不扣成本），只算有交易的週。',
        f'- 通過標準在看結果前訂定（`src/crypto_robot/lab/momentum_variants.py`），六項全過才算：有交易 ≥ {MIN_WEEKS} 週；t ≥ {MIN_T:g}；'
        f'前後半期都 > 0；贏過 {RANDOM_PCT:.0%} 的隨機挑選；滑價加倍仍贏全部等權重；回撤不比持有 ETH 深。',
        '- **已知限制**：只有現在還在 MAX 上的幣（下架的不在，結果偏樂觀）；成本用 2026-10 的價差估計。', '',
        '| 規則 | 通過 | 有交易週 | 總報酬 | 全部等權重 | 持有 ETH | 每週平均超額 | t | 前半超額 | 後半超額 | 贏過隨機 | 滑價×2 總報酬 | 最大回撤 | ETH 回撤 | 未通過原因 |',
        '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|',
    ]
    for r in rows:
        h = r['halves']
        lines.append(
            f'| {r["variant"]} | {"✅" if r["passed"] else "—"} | {r["traded"]}／{r["weeks"]} | {pct(r["total"])} | {pct(r["basket_total"])} '
            f'| {pct(r["eth_total"])} | {r["mean_excess"]:+.2%} | {r["t"]:.2f} | {h[0]:+.2%} | {h[1]:+.2%} | {r["random_pct"]:.0%} '
            f'| {pct(r["stress_total"])} | {pct(r["mdd"])} | {pct(r["eth_mdd"])} | {"；".join(r["fails"])} |')
    lines += ['', '## 最常被選中的幣（週數）', '']
    for r in rows:
        lines.append(f'- {r["variant"]}：' + '、'.join(f'{c.upper()} {n}' for c, n in list(r['picks'].items())[:8]))
    passed = [r['variant'] for r in rows if r['passed']]
    lines += ['', '## 結論', '',
              (f'通過：{", ".join("`" + p + "`" for p in passed)}。考卷期只考其中 t 最高的一組，只考一次。'
               if passed else '**沒有任何一組通過。** 不放寬標準，也不進考卷期。'),
              '', '歷史統計，過去有效不代表未來有效。']
    return '\n'.join(lines) + '\n'
