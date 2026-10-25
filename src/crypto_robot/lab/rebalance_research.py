"""第三輪研究（比例再平衡）：研究期全部比較、考卷期只考一次。和前兩輪共用同一個考卷檔。"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from crypto_robot.agents.data.resample import to_daily
from crypto_robot.core.models import Bar
from crypto_robot.lab import periods
from crypto_robot.lab.backtest import buy_and_hold
from crypto_robot.lab.metrics import segment_return, stats
from crypto_robot.lab.rebalance_backtest import rebalance_count, run_rebalance
from crypto_robot.lab.rebalance_variants import MIN_REBALANCES, RebalanceVariant, all_variants, passes
from crypto_robot.lab.research import DEV_MID, day, pct, window
from crypto_robot.lab.variants import COSTS


def evaluate(hourly: list[Bar], v: RebalanceVariant, start_ms: int, end_ms: int, halves: list[tuple[int, int]]) -> dict:
    daily = to_daily(window(hourly, start_ms, end_ms, 2 * 24))
    res = run_rebalance(daily, start_ms, end_ms, v.params, COSTS)
    static = run_rebalance(daily, start_ms, end_ms, v.params, COSTS, rebalance=False)
    hodl = buy_and_hold(daily, start_ms, end_ms, COSTS)
    s, ss = stats(res), stats(static)
    half_rows = [{'ret': segment_return(res, a, b), 'static_ret': segment_return(static, a, b),
                  'hodl_ret': segment_return(hodl, a, b)} for a, b in halves]
    n = rebalance_count(res)
    ok, fails = passes(n, s.total_return, ss.total_return, s.max_drawdown, ss.max_drawdown, half_rows)
    return {'variant': v.name, 'rebalances': n, 'stats': s, 'static': ss, 'hodl': stats(hodl),
            'halves': half_rows, 'passed': ok, 'fails': fails}


def run_dev(hourly: list[Bar]) -> list[dict]:
    halves = [(periods.DEV_START, DEV_MID), (DEV_MID, periods.DEV_END)]
    return [evaluate(hourly, v, periods.DEV_START, periods.DEV_END, halves) for v in all_variants()]


def dev_report(rows: list[dict], generated: str) -> str:
    hodl, h = rows[0]['hodl'], rows[0]['halves']
    lines = [
        f'# 研究期報告（第三輪，比例再平衡）：{periods.MARKET}（{generated}）', '',
        f'- 研究期 {day(periods.DEV_START)} ～ {day(periods.DEV_END - 1)}，前半／後半以 {day(DEV_MID)} 切開；日 K 以台北時間 00:00 為日界。',
        f'- 期初買到目標比例；每天收盤看 ETH 比例，偏離達門檻就在隔天開盤調回目標。成本 taker {COSTS.taker_fee:%}＋滑價 {COSTS.slippage:%}。',
        '- **基準：同比例買進後不再調整**。超額 = 再平衡報酬 − 不再平衡報酬，代表「買低賣高」本身多賺或少賺多少。',
        f'- 同時比較 {len(rows)} 組，通過標準在看結果前訂定（`src/crypto_robot/lab/rebalance_variants.py`），五項全過才算：'
        f'再平衡 ≥ {MIN_REBALANCES} 次；研究期多賺；前後半期都多賺；回撤不比不再平衡深；前後半期報酬都 > 0。',
        '- **已知污染**：研究期資料前兩輪已看過。', '',
        f'參考：全部持有 ETH 報酬 {pct(hodl.total_return)}、最大回撤 {pct(hodl.max_drawdown)}'
        f'（前半 {pct(h[0]["hodl_ret"])}、後半 {pct(h[1]["hodl_ret"])}）。', '',
        '| 規則 | 通過 | 再平衡次數 | 報酬 | 不再平衡 | 超額 | 最大回撤 | 不再平衡回撤 | 成本 | 前半超額 | 後半超額 | 未通過原因 |',
        '|---|---|---|---|---|---|---|---|---|---|---|---|',
    ]
    for r in rows:
        s, ss, hv = r['stats'], r['static'], r['halves']
        lines.append(
            f'| {r["variant"]} | {"✅" if r["passed"] else "—"} | {r["rebalances"]} | {pct(s.total_return)} '
            f'| {pct(ss.total_return)} | {pct(s.total_return - ss.total_return)} | {pct(s.max_drawdown)} '
            f'| {pct(ss.max_drawdown)} | {s.cost_pct:.1%} | {pct(hv[0]["ret"] - hv[0]["static_ret"])} '
            f'| {pct(hv[1]["ret"] - hv[1]["static_ret"])} | {"；".join(r["fails"])} |')
    passed = [r['variant'] for r in rows if r['passed']]
    lines += ['', '## 結論', '',
              (f'通過：{", ".join(f"`{p}`" for p in passed)}。從中選一組進考卷期（只考一次）。' if passed else
               '**沒有任何一組通過。** 不放寬標準去遷就結果；這輪規則都不進考卷期。'),
              '', '歷史統計，過去有效不代表未來有效。']
    return '\n'.join(lines) + '\n'


def run_holdout(hourly: list[Bar], v: RebalanceVariant, end_ms: int, out_path: Path, commit: str) -> dict:
    """考卷期只考一次：結果已存在就直接回傳，不重算。"""
    if out_path.exists():
        return json.loads(out_path.read_text(encoding='utf-8'))
    start = periods.HOLDOUT_START
    mid = start + (end_ms - start) // 2
    r = evaluate(hourly, v, start, end_ms, [(start, mid), (mid, end_ms)])
    record = {
        'family': 'rebalance', 'variant': v.name, 'tested_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'git_commit': commit, 'period': [day(start), day(end_ms - 1)], 'rebalances': r['rebalances'],
        'stats': asdict(r['stats']), 'static': asdict(r['static']), 'hodl': asdict(r['hodl']),
        'halves': r['halves'], 'passed': r['passed'], 'fails': r['fails'],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return record
