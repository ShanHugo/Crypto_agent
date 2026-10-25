"""第四輪研究（凌晨時段買低賣高）：研究期全部比較、考卷期只考一次。和前幾輪共用同一個考卷檔。

需要兩種 K 線：5 分 K（交易）與 1h K（合成日 K 給不接刀開關用），以 (bars_1h, bars_5m) 傳入。
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from crypto_robot.agents.data.resample import to_daily
from crypto_robot.core.models import Bar
from crypto_robot.lab import periods
from crypto_robot.lab.intraday_backtest import (MAKER_FEE, TARGET_NOTIONAL, VOLUME_CAP, avg_fill, run_nights)
from crypto_robot.lab.intraday_variants import (MAX_DRAWDOWN, MIN_AVG_FILL, MIN_T_STAT, MIN_TRADES, IntradayVariant,
                                                all_variants, passes)
from crypto_robot.lab.metrics import segment_return, stats
from crypto_robot.lab.research import DEV_MID, day, pct
from crypto_robot.lab.variants import COSTS

DAY_MS = 86_400_000


def evaluate(data: tuple[list[Bar], list[Bar]], v: IntradayVariant, start_ms: int, end_ms: int,
             halves: list[tuple[int, int]]) -> dict:
    hourly, bars5 = data
    daily = to_daily([b for b in hourly if start_ms - 40 * DAY_MS <= b.ts < end_ms])
    bars = [b for b in bars5 if start_ms - DAY_MS <= b.ts < end_ms]
    out = run_nights(bars, daily, start_ms, end_ms, v.rule, v.use_filter, COSTS)
    stressed = run_nights(bars, daily, start_ms, end_ms, v.rule, v.use_filter, COSTS, stress=True)
    s = stats(out.result)
    stress_ret = stats(stressed.result).total_return
    fill = float(avg_fill(out))
    half_rows = [{'ret': segment_return(out.result, a, b), 'hold_ret': segment_return(out.hold, a, b)}
                 for a, b in halves]
    ok, fails = passes(s.trades, s.t_stat, s.max_drawdown, stress_ret, fill, half_rows)
    reasons = {}
    for t in out.result.trades:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1
    return {'variant': v.name, 'stats': s, 'stress_ret': stress_ret, 'avg_fill': fill, 'halves': half_rows,
            'nights': out.nights, 'skipped': out.skipped, 'filtered': out.filtered, 'exits': reasons,
            'hold_ret': stats(out.hold).total_return, 'passed': ok, 'fails': fails}


def run_dev(data) -> list[dict]:
    halves = [(periods.DEV_START, DEV_MID), (DEV_MID, periods.DEV_END)]
    return [evaluate(data, v, periods.DEV_START, periods.DEV_END, halves) for v in all_variants()]


def dev_report(rows: list[dict], generated: str) -> str:
    lines = [
        f'# 研究期報告（第四輪，凌晨時段買低賣高）：{periods.MARKET}（{generated}）', '',
        f'- 研究期 {day(periods.DEV_START)} ～ {day(periods.DEV_END - 1)}，前半／後半以 {day(DEV_MID)} 切開。5 分 K，台北時間。',
        '- 每天 01:30 開始、07:20 撤單並市價平倉，不留倉過夜；每晚最多 3 次來回；虧損 5% 市價停損、當晚不再進場。',
        f'- 進場與停利用限價單（maker {MAKER_FEE:%}），價格要穿過掛價才成交，每根最多成交該根成交量的 {VOLUME_CAP:.0%}；'
        f'停損與平倉用市價單（taker {COSTS.taker_fee:%}＋滑價 {COSTS.slippage:%}）。資金 {TARGET_NOTIONAL:,.0f} 台幣。',
        f'- 同時比較 {len(rows)} 組，通過標準在看結果前訂定（`src/crypto_robot/lab/intraday_variants.py`），七項全過才算：'
        f'交易 ≥ {MIN_TRADES} 筆；t ≥ {MIN_T_STAT}；前後半期報酬都 > 0；前後半期都贏時段內持有（不計成本）；'
        f'回撤 ≤ {MAX_DRAWDOWN:.0%}；限價單改以市價計仍賺錢；平均每筆成交 ≥ {MIN_AVG_FILL:,} 台幣。',
        '- **已知污染**：研究期的日 K 與 1h 資料前三輪已看過；5 分 K 只看過流動性。', '',
        '| 規則 | 通過 | 報酬 | 最大回撤 | 交易數 | 勝率 | 每筆平均 | t | 平均每筆金額 | 出場（停利／停損／平倉） '
        '| 壓力測試 | 時段內持有 | 前半 | 後半 | 交易夜數 | 未通過原因 |',
        '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|',
    ]
    for r in rows:
        s, hv, ex = r['stats'], r['halves'], r['exits']
        lines.append(
            f'| {r["variant"]} | {"✅" if r["passed"] else "—"} | {pct(s.total_return)} | {pct(s.max_drawdown)} '
            f'| {s.trades} | {s.win_rate:.0%} | {s.avg_trade:+.2%} | {s.t_stat:.2f} | {r["avg_fill"]:,.0f} '
            f'| {ex.get("take", 0)}／{ex.get("stop", 0)}／{ex.get("close", 0)} | {pct(r["stress_ret"])} '
            f'| {pct(r["hold_ret"])} | {pct(hv[0]["ret"])} | {pct(hv[1]["ret"])} | {r["nights"]} | {"；".join(r["fails"])} |')
    skipped = rows[0]['skipped']
    passed = [r['variant'] for r in rows if r['passed']]
    lines += ['', f'資料不完整而跳過的夜晚：{skipped} 晚。「時段內持有」不計成本，只用來看時段內價格本身的漲跌。',
              '', '## 結論', '',
              (f'通過：{", ".join(f"`{p}`" for p in passed)}。從中選一組進考卷期（只考一次）。' if passed else
               '**沒有任何一組通過。** 不放寬標準去遷就結果；這輪規則都不進考卷期。'),
              '', '歷史統計，過去有效不代表未來有效。']
    return '\n'.join(lines) + '\n'


def run_holdout(data, v: IntradayVariant, end_ms: int, out_path: Path, commit: str) -> dict:
    """考卷期只考一次：結果已存在就直接回傳，不重算。"""
    if out_path.exists():
        return json.loads(out_path.read_text(encoding='utf-8'))
    start = periods.HOLDOUT_START
    mid = start + (end_ms - start) // 2
    r = evaluate(data, v, start, end_ms, [(start, mid), (mid, end_ms)])
    record = {'family': 'intraday', 'tested_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'git_commit': commit, 'period': [day(start), day(end_ms - 1)],
              **{k: v for k, v in r.items() if k != 'stats'}, 'stats': asdict(r['stats'])}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return record
