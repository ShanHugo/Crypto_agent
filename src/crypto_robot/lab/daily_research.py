"""第二輪研究（日 K）：研究期全部比較、考卷期只考一次。和第一輪共用同一個考卷檔。"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from crypto_robot.agents.data.resample import to_daily
from crypto_robot.core.models import Bar
from crypto_robot.lab import periods
from crypto_robot.lab.backtest import buy_and_hold
from crypto_robot.lab.daily_backtest import run_rule
from crypto_robot.lab.daily_variants import MAX_DD_VS_HODL, MIN_TRADES, DailyVariant, all_variants, passes
from crypto_robot.lab.metrics import segment_drawdown, segment_return, stats
from crypto_robot.lab.research import DEV_MID, day, pct, window
from crypto_robot.lab.variants import COSTS

FAMILY_LABEL = {'trend': '趨勢（站上均線才持有）', 'meanrev': '買低賣高：均線乖離', 'rsi': '買低賣高：RSI'}


def evaluate(hourly: list[Bar], v: DailyVariant, start_ms: int, end_ms: int, halves: list[tuple[int, int]]):
    daily = to_daily(window(hourly, start_ms, end_ms, (v.rule.warmup + 2) * 24))
    res = run_rule(daily, start_ms, end_ms, v.rule, COSTS)
    hodl = buy_and_hold(daily, start_ms, end_ms, COSTS)
    half_rows = [{
        'ret': segment_return(res, a, b), 'mdd': segment_drawdown(res, a, b),
        'hodl_ret': segment_return(hodl, a, b), 'hodl_mdd': segment_drawdown(hodl, a, b),
    } for a, b in halves]
    return res, hodl, half_rows


def judge(res, hodl, half_rows):
    s, hs = stats(res), stats(hodl)
    ok, fails = passes(s.trades, s.max_drawdown, hs.max_drawdown, half_rows)
    return s, hs, ok, fails


def run_dev(hourly: list[Bar]) -> list[dict]:
    halves = [(periods.DEV_START, DEV_MID), (DEV_MID, periods.DEV_END)]
    rows = []
    for v in all_variants():
        res, hodl, half_rows = evaluate(hourly, v, periods.DEV_START, periods.DEV_END, halves)
        s, hs, ok, fails = judge(res, hodl, half_rows)
        rows.append({'variant': v.name, 'family': v.family, 'stats': s, 'hodl': hs, 'halves': half_rows,
                     'passed': ok, 'fails': fails})
    return rows


def dev_report(rows: list[dict], generated: str) -> str:
    hodl, h = rows[0]['hodl'], rows[0]['halves']
    lines = [
        f'# 研究期報告（第二輪，日 K）：{periods.MARKET}（{generated}）', '',
        f'- 研究期 {day(periods.DEV_START)} ～ {day(periods.DEV_END - 1)}，前半／後半以 {day(DEV_MID)} 切開；日 K 以台北時間 00:00 為日界。',
        f'- 收盤判斷、隔天開盤成交；成本 taker {COSTS.taker_fee:%}＋滑價 {COSTS.slippage:%}（每次成交），全部資金進出。',
        f'- 同時比較 {len(rows)} 組，通過標準在看結果前訂定（`src/crypto_robot/lab/daily_variants.py`），五項全過才算：'
        f'交易 ≥ {MIN_TRADES} 筆；最大回撤 ≤ 持有的 {MAX_DD_VS_HODL:.0%}；前後半期報酬都 > 0；'
        '前後半期都不輸持有（報酬或報酬÷回撤）；前後半期回撤都比持有淺。',
        '- **已知污染**：研究期資料在第一輪已看過，趨勢類的靈感來自第一輪的觀察，研究期表現好的可信度打折。', '',
        '## 基準：一直持有 ETH', '',
        f'報酬 {pct(hodl.total_return)}、年化 {pct(hodl.cagr)}、最大回撤 {pct(hodl.max_drawdown)}；'
        f'前半 {pct(h[0]["hodl_ret"])}（回撤 {pct(h[0]["hodl_mdd"])}）、後半 {pct(h[1]["hodl_ret"])}（回撤 {pct(h[1]["hodl_mdd"])}）。',
    ]
    for fam, label in FAMILY_LABEL.items():
        lines += ['', f'## {label}', '',
                  '| 規則 | 通過 | 報酬 | 年化 | 最大回撤 | 交易數 | 勝率 | 每筆平均 | 持有時間 | 成本 | 前半（回撤） | 後半（回撤） | 未通過原因 |',
                  '|---|---|---|---|---|---|---|---|---|---|---|---|---|']
        for r in (r for r in rows if r['family'] == fam):
            s, hv = r['stats'], r['halves']
            lines.append(
                f'| {r["variant"]} | {"✅" if r["passed"] else "—"} | {pct(s.total_return)} | {pct(s.cagr)} '
                f'| {pct(s.max_drawdown)} | {s.trades}{"＋1 未平倉" if s.open_at_end else ""} | {s.win_rate:.0%} '
                f'| {pct(s.avg_trade)} | {s.exposure:.0%} | {s.cost_pct:.1%} '
                f'| {pct(hv[0]["ret"])}（{pct(hv[0]["mdd"])}） | {pct(hv[1]["ret"])}（{pct(hv[1]["mdd"])}） '
                f'| {"；".join(r["fails"])} |')
    passed = [r['variant'] for r in rows if r['passed']]
    lines += ['', '## 結論', '',
              (f'通過：{", ".join(f"`{p}`" for p in passed)}。從中選一組進考卷期（只考一次）。' if passed else
               '**沒有任何一組通過。** 不放寬標準去遷就結果；這輪規則都不進考卷期。'),
              '', '歷史統計，過去有效不代表未來有效。']
    return '\n'.join(lines) + '\n'


def run_holdout(hourly: list[Bar], v: DailyVariant, end_ms: int, out_path: Path, commit: str) -> dict:
    """考卷期只考一次：結果已存在就直接回傳，不重算。"""
    if out_path.exists():
        return json.loads(out_path.read_text(encoding='utf-8'))
    start = periods.HOLDOUT_START
    mid = start + (end_ms - start) // 2
    res, hodl, half_rows = evaluate(hourly, v, start, end_ms, [(start, mid), (mid, end_ms)])
    s, hs, ok, fails = judge(res, hodl, half_rows)
    record = {
        'family': 'daily', 'variant': v.name, 'tested_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'git_commit': commit, 'period': [day(start), day(end_ms - 1)],
        'stats': asdict(s), 'hodl': asdict(hs), 'halves': half_rows, 'passed': ok, 'fails': fails,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return record
