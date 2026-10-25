"""研究期全部比較、考卷期只考一次（DESIGN.md 6.3）。"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from crypto_robot.agents.data.quality import check_bars
from crypto_robot.core.models import Bar
from crypto_robot.lab import periods
from crypto_robot.lab.backtest import buy_and_hold, run
from crypto_robot.lab.metrics import segment_drawdown, segment_return, stats
from crypto_robot.lab.variants import BASELINE, COSTS, Variant, all_variants, passes

PERIOD_MS = periods.PERIOD_MIN * 60_000
DEV_MID = int(datetime(2022, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)


def day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%d')


def window(bars: list[Bar], start_ms: int, end_ms: int, warmup: int) -> list[Bar]:
    """[start_ms − 暖機, end_ms) 的 K 線；資料有 blocking 問題就停止，不產生不可靠的報告。"""
    out = [b for b in bars if start_ms - warmup * PERIOD_MS <= b.ts < end_ms]
    report = check_bars(out, PERIOD_MS)
    if not report.ok:
        first = report.blocking[0]
        raise RuntimeError(f'資料有 {len(report.blocking)} 個問題（第一個：{first.kind} {day(first.ts)}），先用 fetch_bars.py 修正')
    if not out or out[0].ts > start_ms - warmup * PERIOD_MS:
        raise RuntimeError('暖機資料不足')
    return out


def evaluate(bars: list[Bar], v: Variant, start_ms: int, end_ms: int, halves: list[tuple[int, int]]):
    data = window(bars, start_ms, end_ms, v.strategy.warmup_bars)
    res = run(data, start_ms, end_ms, v.strategy, v.exit, COSTS)
    hodl = buy_and_hold(data, start_ms, end_ms, COSTS)
    half_rows = [{
        'ret': segment_return(res, a, b), 'mdd': segment_drawdown(res, a, b),
        'hodl_ret': segment_return(hodl, a, b), 'hodl_mdd': segment_drawdown(hodl, a, b),
    } for a, b in halves]
    return res, hodl, half_rows


def run_dev(bars: list[Bar]) -> list[dict]:
    halves = [(periods.DEV_START, DEV_MID), (DEV_MID, periods.DEV_END)]
    rows = []
    for v in all_variants():
        res, hodl, half_rows = evaluate(bars, v, periods.DEV_START, periods.DEV_END, halves)
        s = stats(res)
        ok, fails = passes(s.trades, s.t_stat, half_rows)
        rows.append({'variant': v.name, 'stats': s, 'hodl': stats(hodl), 'halves': half_rows,
                     'passed': ok, 'fails': fails})
    return rows


def pct(x: float) -> str:
    return f'{x:+.1%}'


def dev_report(rows: list[dict], generated: str) -> str:
    hodl = rows[0]['hodl']
    h = rows[0]['halves']
    lines = [
        f'# 研究期報告：{periods.MARKET} {periods.PERIOD_MIN}m（{generated}）', '',
        f'- 研究期 {day(periods.DEV_START)} ～ {day(periods.DEV_END - 1)}（UTC），前半／後半以 {day(DEV_MID)} 切開。',
        f'- 成本：taker {COSTS.taker_fee:%}＋滑價 {COSTS.slippage:%}（每次成交）。全部資金進出，便於和一直持有比較。',
        f'- 同時比較 {len(rows)} 組，通過標準在看結果前訂定（`src/crypto_robot/lab/variants.py`）。'
        '多組比較時，就算全部沒用，也可能有幾組「看起來」不錯，所以要四項全過。',
        f'- 原始規格的規則是 `{BASELINE}`。', '',
        '## 基準：一直持有 ETH', '',
        f'報酬 {pct(hodl.total_return)}、年化 {pct(hodl.cagr)}、最大回撤 {pct(hodl.max_drawdown)}；'
        f'前半 {pct(h[0]["hodl_ret"])}（回撤 {pct(h[0]["hodl_mdd"])}）、後半 {pct(h[1]["hodl_ret"])}（回撤 {pct(h[1]["hodl_mdd"])}）。', '',
        '## 全部規則', '',
        '| 規則 | 通過 | 報酬 | 年化 | 最大回撤 | 交易數 | 勝率 | 每筆平均 | t | 持有時間 | 成本 | 前半 | 後半 | 未通過原因 |',
        '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|',
    ]
    for r in rows:
        s = r['stats']
        name = f'**{r["variant"]}**' if r['variant'] == BASELINE else r['variant']
        lines.append(
            f'| {name} | {"✅" if r["passed"] else "—"} | {pct(s.total_return)} | {pct(s.cagr)} | {pct(s.max_drawdown)} '
            f'| {s.trades} | {s.win_rate:.0%} | {pct(s.avg_trade)} | {s.t_stat:.2f} | {s.exposure:.0%} | {s.cost_pct:.1%} '
            f'| {pct(r["halves"][0]["ret"])} | {pct(r["halves"][1]["ret"])} | {"；".join(r["fails"])} |')
    passed = [r['variant'] for r in rows if r['passed']]
    held = [r for r in rows if r['stats'].open_at_end and r['stats'].exposure >= 0.8]
    if held:
        lines += ['', f'**注意**：有 {len(held)} 組持有時間 ≥ 80% 且期末仍未平倉。沒有死叉出場時，唯一的出場是停損；'
                  '進場後價格沒有再跌回停損價，就會一直持有到期末，結果等於「從某天開始買進持有」，'
                  '高度取決於第一筆的進場時點，不代表規則有選時能力。']
    lines += ['', '## 結論', '',
              (f'通過：{", ".join(f"`{p}`" for p in passed)}。從中選一組進考卷期（只考一次）。' if passed else
               '**沒有任何一組通過。** 依 Stock_Robot 的做法，不放寬標準去遷就結果；這組規則族不進考卷期。'),
              '', '歷史統計，過去有效不代表未來有效。']
    return '\n'.join(lines) + '\n'


def run_holdout(bars: list[Bar], v: Variant, end_ms: int, out_path: Path, commit: str) -> dict:
    """考卷期只考一次：結果已存在就直接回傳，不重算。"""
    if out_path.exists():
        return json.loads(out_path.read_text(encoding='utf-8'))
    start = periods.HOLDOUT_START
    mid = start + (end_ms - start) // 2
    res, hodl, half_rows = evaluate(bars, v, start, end_ms, [(start, mid), (mid, end_ms)])
    s = stats(res)
    ok, fails = passes(s.trades, s.t_stat, half_rows)
    record = {
        'variant': v.name, 'tested_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'git_commit': commit, 'period': [day(start), day(end_ms - 1)],
        'stats': asdict(s), 'hodl': asdict(stats(hodl)), 'halves': half_rows, 'passed': ok, 'fails': fails,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return record
