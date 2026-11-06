"""第二十二輪：多幣排序改成每 3 天判斷（DESIGN.md 6.14，2026-11-06 營運者同意）。**看過結果後不改**。

選幣池、特徵、選股、換手與成本全部沿用第二十一輪（lab/xsec.py，不改），這裡只換持有天數與答案，只測 gbm。
2025-01～2026-10 考卷已用掉，只當參考段；研究期判定寫下來之後才能算。研究統計用 float（不碰交易路徑）。
"""
from __future__ import annotations

import bisect
import csv
import statistics as st
from pathlib import Path

from crypto_robot.lab import xsec
from crypto_robot.lab.hourly import ms
from crypto_robot.lab.xsec import D, GAP, MAX_DD, MIN_T, MIN_UNI, Hold, rank

HOLD_DAYS = 3
STEP = HOLD_DAYS * D
ANCHOR = ms(2022, 1, 3)
SHIFT = {'s0': 0, 's1': D}      # s1：晚 1 天起算
MIN_PERIODS = 300
GROUP, CONTROL = 'gbm', 'gbm_shuf'
PER_WEEK = 7 / HOLD_DAYS


def periods(a: int, b: int, shift: str = 's0') -> list[int]:
    """每 3 天一次，開始 ≥ a、持有在 b 以前結束。從 2022-01-03 往前後推。"""
    t0 = ANCHOR + SHIFT[shift]
    t0 += ((a - t0 + STEP - 1) // STEP) * STEP
    return list(range(t0, b - STEP + 1, STEP))


def hold(c: xsec.Coin, t: int) -> Hold:
    """t 進場持有 3 天；規則同 Coin.hold（沒有 t 那根用上一根收盤，後面沒有 K 線就是下架）。"""
    first = c.pos.get(t)
    prev = bisect.bisect_left(c.t, t) - 1
    c0 = c.o[first] if first is not None else c.c[prev]
    ratio, fund, last, path = 1.0, 0.0, prev, []
    for day in range(t, t + STEP, D):
        j = c.pos.get(day)
        if j is not None:
            fund += ratio * c.fund_sum(day, day + D)
            ratio, last = c.c[j] / c0, j
        path.append((ratio, fund))
    nxt = c.t[last + 1] if last + 1 < len(c.t) else None
    delisted = c.t[last] < t + (HOLD_DAYS - 1) * D and (nxt is None or nxt - c.t[last] > GAP)
    return Hold(ratio, fund, tuple(path), delisted)


class Book(xsec.Book):
    def hold(self, sym: str, t: int) -> Hold:
        key = (sym, t)
        if key not in self.cache:
            self.cache[key] = hold(self.coins[sym], t)
        return self.cache[key]


def label(c: xsec.Coin, t: int) -> float:
    h = hold(c, t)
    return h.ratio - 1 - h.fund


def write_panel(path: Path, coins: dict[str, xsec.Coin], shift: str) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    market = xsec.market_returns(coins)
    sizes = {}
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['ts', 'sym', 'n', 'label'] + xsec.FEATURES)
        for t in periods(xsec.FIRST_MONDAY, xsec.END, shift):
            uni = xsec.universe(coins, t)
            if len(uni) < MIN_UNI:
                continue
            ys = rank([label(c, t) for c in uni])
            cols = [rank(list(col)) for col in zip(*(c.features(t, market) for c in uni))]
            for i, c in enumerate(uni):
                w.writerow([t, c.sym, len(uni), round(ys[i], 6)] + [round(col[i], 6) for col in cols])
            sizes[t] = len(uni)
    return {'periods': len(sizes), 'first': min(sizes), 'smallest_in_dev': min((n for t, n in sizes.items() if t >= xsec.DEV[0]), default=0)}


def simulate(book: Book, picks: list[tuple[int, list[str], list[str]]], cost: float = xsec.COST) -> dict:
    prev: dict[str, float] = {}
    equity, marks, rows = 1.0, [1.0], []
    for t, longs, shorts in picks:
        if equity <= 0:
            rows.append({'t': t, 'ret': 0.0, 'turnover': 0.0, 'long': 0.0, 'short': 0.0, 'fund': 0.0, 'by': {}})
            continue
        s = xsec.step(book, t, longs, shorts, prev, cost)
        for d in range(HOLD_DAYS):                          # 每天收盤記權益
            pnl = sum(w * (book.hold(c, t).path[d][0] - 1 - book.hold(c, t).path[d][1]) for c, w in s['tgt'].items())
            marks.append(max(equity * (1 - s['fee'] + pnl), 0.0))
        equity *= 1 + s['ret']
        marks[-1] = equity
        prev = s['weights']
        rows.append({'t': t, **{k: s[k] for k in ('ret', 'turnover', 'long', 'short', 'fund', 'by')}})
    return {'rows': rows, 'marks': marks, 'equity': equity}


def dev_checks(m: dict, p95: float, stress_mean: float, shift_mean: float) -> dict[str, bool]:
    return {'有持倉 ≥ 300 次': m['weeks'] >= MIN_PERIODS, '每次平均 > 0': m['mean'] > 0, 't ≥ 3': m['t'] >= MIN_T,
            '前後半都 > 0': m['h1'] > 0 and m['h2'] > 0, '贏隨機第 95 百分位': m['mean'] > p95, '成本加倍仍 > 0': stress_mean > 0,
            '回撤不深於 -30%': m['dd'] >= MAX_DD, '拿掉最賺的幣仍 > 0': m['drop_top'] > 0, '晚 1 天起算仍 > 0': shift_mean > 0}


def run_dev(coins: dict[str, xsec.Coin], panels: dict[str, dict], preds: dict) -> dict:
    book = Book(coins)
    ts, later = periods(*xsec.DEV, 's0'), periods(*xsec.DEV, 's1')
    rand = xsec.random_means(book, panels['s0'], ts)
    p95 = xsec.percentile(rand, 0.95)
    picks = xsec.picks_for(GROUP, panels['s0'], preds, 's0', ts)
    m = xsec.metrics(simulate(book, picks), xsec.DEV_MID)
    stress = xsec.metrics(simulate(book, picks, xsec.STRESS), xsec.DEV_MID)['mean']
    shift = xsec.metrics(simulate(book, xsec.picks_for(GROUP, panels['s1'], preds, 's1', later)), xsec.DEV_MID)['mean']
    checks = dev_checks(m, p95, stress, shift)
    control = xsec.metrics(simulate(book, xsec.picks_for(CONTROL, panels['s0'], preds, 's0', ts)), xsec.DEV_MID)
    return {**m, 'rand_mean': st.fmean(rand), 'p95': p95, 'stress': stress, 'shift': shift, 'checks': checks, 'passed': all(checks.values()),
            'control': control, 'leak': xsec.leaked(control), 'ic': xsec.spearman_ic(panels['s0'], preds.get((GROUP, 's0'), {}), GROUP, ts)}


def run_reference(coins: dict[str, xsec.Coin], panel: dict, preds: dict) -> dict:
    """2025-01～2026-10：只報告，不判定通過。"""
    book = Book(coins)
    ts = periods(*xsec.EXAM, 's0')
    sim = simulate(book, xsec.picks_for(GROUP, panel, preds, 's0', ts))
    m = xsec.metrics(sim, xsec.EXAM_MID)
    worst = min(sim['rows'], key=lambda r: r['ret'])
    return {**m, 'worst': worst['ret'], 'worst_t': worst['t'], 'rand_mean': st.fmean(xsec.random_means(book, panel, ts))}


def _pct(x: float, digits: int = 2) -> str:
    return f'{x * 100:+.{digits}f}%'


def dev_report(m: dict, when: str) -> str:
    c = m['control']
    miss = [k for k, ok in m['checks'].items() if not ok]
    return '\n'.join([
        f'# 第二十二輪：多幣排序每 3 天判斷（{when}）', '',
        '研究期 2022-01-03～2024-12-30，`gbm`，每 3 天前 50 大、10 多 10 空、合計 1 倍，已扣成本。', '',
        '| 項目 | 數字 |', '|---|---|',
        f"| 持倉次數 | {m['weeks']} |",
        f"| 每次平均（換算每週） | {_pct(m['mean'], 3)}（{_pct(m['mean'] * PER_WEEK)}） |",
        f"| t | {m['t']:+.2f} |",
        f"| 前半／後半（每次） | {_pct(m['h1'], 3)}／{_pct(m['h2'], 3)} |",
        f"| 總報酬 | {_pct(m['total'], 1)} |",
        f"| 最大回撤 | {_pct(m['dd'], 1)} |",
        f"| 隨機挑幣（每次平均／第 95 百分位） | {_pct(m['rand_mean'], 3)}／{_pct(m['p95'], 3)} |",
        f"| 成本加倍（每次） | {_pct(m['stress'], 3)} |",
        f"| 晚 1 天起算（每次） | {_pct(m['shift'], 3)} |",
        f"| 每次換手（換算每週成本） | {m['turnover'] * 100:.0f}%（{m['turnover'] * PER_WEEK * xsec.COST * 100:.2f}%） |",
        f"| 多方／空方／資金費率（每次） | {_pct(m['long'], 3)}／{_pct(m['short'], 3)}／{_pct(m['fund'], 3)} |",
        f"| 最賺的幣、拿掉後每次 | {m['top']}、{_pct(m['drop_top'], 3)} |",
        f"| 排名相關 | {m['ic']:+.3f} |", '',
        f"對照組 `gbm_shuf`：每次 {_pct(c['mean'], 3)}，t {c['t']:+.2f}。洩漏：{'有，整輪作廢' if m['leak'] else '沒有'}。", '',
        f"判定：{'**九項全過**' if m['passed'] else '沒過：' + '、'.join(miss)}。第二十一輪 `gbm` 研究期：每週 +0.34%、t 1.26、回撤 -30.8%。", ''])


def reference_report(m: dict, dev: dict, when: str) -> str:
    from datetime import datetime, timezone
    day = datetime.fromtimestamp(m['worst_t'] / 1000, timezone.utc).strftime('%Y-%m-%d')
    return '\n'.join([
        f'# 第二十二輪參考段（{when}）', '',
        f"2025-01～2026-10，不判定通過；研究期判定已先寫下：{'通過' if dev['passed'] else '沒過'}。", '',
        '| | 每 3 天（這一輪） | 每週（第二十一輪考卷） |', '|---|---|---|',
        f"| 換算每週 | {_pct(m['mean'] * PER_WEEK)} | +0.58% |",
        f"| t | {m['t']:+.2f} | +1.08 |",
        f"| 總報酬 | {_pct(m['total'], 1)} | +50.3% |",
        f"| 最大回撤 | {_pct(m['dd'], 1)} | -49.8% |",
        f"| 最差一次 | {_pct(m['worst'], 1)}（{day}） | -29.6%（一週） |",
        f"| 資金費率換算每週 | {_pct(m['fund'] * PER_WEEK)} | -0.51% |",
        f"| 多方／空方換算每週 | {_pct(m['long'] * PER_WEEK)}／{_pct(m['short'] * PER_WEEK)} | -0.11%／+0.77% |",
        f"| 前半／後半（每次） | {_pct(m['h1'], 3)}／{_pct(m['h2'], 3)} | +0.32%／+0.84%（每週） |", '',
        f"同期隨機挑幣每次平均 {_pct(m['rand_mean'], 3)}。最賺的幣 {m['top']}。", ''])
