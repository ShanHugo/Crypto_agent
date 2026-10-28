"""第六輪研究（每小時檢查的三資產再平衡）：只有研究期，沒有考卷。"""
from __future__ import annotations

from dataclasses import replace

from crypto_robot.core.models import Bar
from crypto_robot.lab import periods
from crypto_robot.lab.common import COSTS, DEV_MID, day, pct, window
from crypto_robot.lab.hourly_variants import MIN_REBALANCES, SLIPPAGE_STRESS, HourlyVariant, all_variants, passes
from crypto_robot.lab.metrics import segment_return, stats
from crypto_robot.lab.multi_backtest import rebalance_days, run_multi
from crypto_robot.lab.multi_research import daily_bars

STRESS = replace(COSTS, slippage=COSTS.slippage * SLIPPAGE_STRESS)


def evaluate(hourly: dict[str, list[Bar]], v: HourlyVariant, start_ms: int, end_ms: int,
             halves: list[tuple[int, int]]) -> dict:
    h = {a: window(bars, start_ms, end_ms, 48) for a, bars in hourly.items()}
    daily = daily_bars(hourly, start_ms, end_ms)
    res = run_multi(h, start_ms, end_ms, v.params, COSTS, can_trade=v.can_trade)
    base = run_multi(daily, start_ms, end_ms, v.params, COSTS)
    s_res = run_multi(h, start_ms, end_ms, v.params, STRESS, can_trade=v.can_trade)
    s_base = run_multi(daily, start_ms, end_ms, v.params, STRESS)
    s, bs = stats(res), stats(base)
    half_rows = [{'ret': segment_return(res, a, b), 'base_ret': segment_return(base, a, b)} for a, b in halves]
    n = rebalance_days(res)      # 1h K 時是「有成交的小時數」＝再平衡次數
    sr, sbr = stats(s_res).total_return, stats(s_base).total_return
    ok, fails = passes(n, s.total_return, bs.total_return, s.max_drawdown, bs.max_drawdown, half_rows, sr, sbr)
    return {'variant': v.name, 'n': n, 'base_n': rebalance_days(base), 'stats': s, 'base': bs,
            'halves': half_rows, 'stress': sr, 'stress_base': sbr, 'passed': ok, 'fails': fails}


def run_dev(hourly: dict[str, list[Bar]]) -> list[dict]:
    halves = [(periods.DEV_START, DEV_MID), (DEV_MID, periods.DEV_END)]
    return [evaluate(hourly, v, periods.DEV_START, periods.DEV_END, halves) for v in all_variants()]


def dev_report(rows: list[dict], generated: str) -> str:
    lines = [
        f'# 研究期報告（第六輪，三資產再平衡改成每小時檢查）（{generated}）', '',
        f'- 研究期 {day(periods.DEV_START)} ～ {day(periods.DEV_END - 1)}，前半／後半以 {day(DEV_MID)} 切開。',
        '- 規則：ETH 50%＋USDT 37.5%＋台幣 12.5%，任何一項偏離達門檻就全部調回。'
        '每小時收盤判斷、下一小時開盤成交；`-win` 只在台北 01:30～08:00 下單（觸發保留到時段內再重算）。',
        f'- **比較對象：同一條規則、一天檢查一次**（日 K，台北 00:00 日界）。成本 taker {COSTS.taker_fee:%}＋滑價 {COSTS.slippage:%}。',
        f'- 同時比較 {len(rows)} 組，通過標準在看結果前訂定（`src/crypto_robot/lab/hourly_variants.py`），五項全過才算：'
        f'再平衡 ≥ {MIN_REBALANCES} 次；研究期多賺；前後半期都多賺；回撤不比每日版深；滑價 ×{SLIPPAGE_STRESS} 後仍多賺。',
        '- **已知限制**：研究期已看過五次；考卷期已用掉，這輪沒有考卷。通過只代表值得再觀察。', '',
        '| 規則 | 通過 | 再平衡次數 | 每日版次數 | 報酬 | 每日版 | 差距 | 最大回撤 | 每日版回撤 | 成本 | 前半差距 | 後半差距 '
        f'| 滑價×{SLIPPAGE_STRESS} 差距 | 未通過原因 |',
        '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|',
    ]
    for r in rows:
        s, b, hv = r['stats'], r['base'], r['halves']
        lines.append(
            f'| {r["variant"]} | {"✅" if r["passed"] else "—"} | {r["n"]} | {r["base_n"]} | {pct(s.total_return)} '
            f'| {pct(b.total_return)} | {pct(s.total_return - b.total_return)} | {pct(s.max_drawdown)} '
            f'| {pct(b.max_drawdown)} | {s.cost_pct:.1%} | {pct(hv[0]["ret"] - hv[0]["base_ret"])} '
            f'| {pct(hv[1]["ret"] - hv[1]["base_ret"])} | {pct(r["stress"] - r["stress_base"])} | {"；".join(r["fails"])} |')
    passed = [r['variant'] for r in rows if r['passed']]
    lines += ['', '## 結論', '',
              (f'通過：{", ".join(f"`{p}`" for p in passed)}。沒有考卷，只代表值得再觀察，不直接改真單。'
               if passed else '**沒有任何一組通過。** 不放寬標準，維持一天檢查一次。'),
              '', '歷史統計，過去有效不代表未來有效。']
    return '\n'.join(lines) + '\n'
