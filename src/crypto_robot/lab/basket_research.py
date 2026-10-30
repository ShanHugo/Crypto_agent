"""第九輪研究（BTC＋ETH＋USDT＋台幣 再平衡）：日 K（台北 00:00 日界），引擎同第五輪（multi_backtest.run_multi）。"""
from __future__ import annotations

from dataclasses import replace

from crypto_robot.core.models import Bar
from crypto_robot.lab.basket_variants import (
    BASELINE, BASELINE_NAME, DEV_END, DEV_MID, DEV_START, MIN_REBALANCE_DAYS, STRESS_MULT, BasketVariant, all_variants,
    passes_dev,
)
from crypto_robot.lab.common import COSTS, day, pct
from crypto_robot.lab.metrics import segment_return, stats
from crypto_robot.lab.multi_backtest import rebalance_days, run_multi
from crypto_robot.lab.multi_research import daily_bars

STRESS = replace(COSTS, slippage=COSTS.slippage * STRESS_MULT)


def evaluate(hourly: dict[str, list[Bar]], v: BasketVariant, start_ms: int, end_ms: int, mid_ms: int) -> dict:
    daily = daily_bars(hourly, start_ms, end_ms)
    base_daily = {a: daily[a] for a in ('eth', 'usdt')}
    res = run_multi(daily, start_ms, end_ms, v.params, COSTS)
    base = run_multi(base_daily, start_ms, end_ms, BASELINE, COSTS)
    s, bs = stats(res), stats(base)
    sr = stats(run_multi(daily, start_ms, end_ms, v.params, STRESS)).total_return
    sbr = stats(run_multi(base_daily, start_ms, end_ms, BASELINE, STRESS)).total_return
    halves = [{'ret': segment_return(res, a, b), 'base_ret': segment_return(base, a, b)}
              for a, b in ((start_ms, mid_ms), (mid_ms, end_ms))]
    n = rebalance_days(res)
    ok, fails = passes_dev(n, s.total_return, bs.total_return, s.max_drawdown, bs.max_drawdown, halves, sr, sbr)
    return {'variant': v.name, 'days': n, 'base_days': rebalance_days(base), 'stats': s, 'base': bs,
            'halves': halves, 'stress': sr, 'stress_base': sbr, 'passed': ok, 'fails': fails}


def run_dev(hourly: dict[str, list[Bar]]) -> list[dict]:
    return [evaluate(hourly, v, DEV_START, DEV_END, DEV_MID) for v in all_variants()]


def dev_report(rows: list[dict], generated: str) -> str:
    lines = [
        f'# 研究期報告（第九輪，BTC＋ETH＋USDT＋台幣 四資產再平衡）（{generated}）', '',
        f'- 研究期 {day(DEV_START)} ～ {day(DEV_END - 1)}，前半／後半以 {day(DEV_MID)} 切開；日 K 以台北時間 00:00 為日界。',
        f'- USDT 37.5%＋台幣 12.5% 不變，虛擬貨幣 50% 拆成 BTC＋ETH；任何一項偏離達門檻就全部調回。成本 taker {COSTS.taker_fee:%}＋滑價 {COSTS.slippage:%}。',
        f'- **比較對象：現在真單的規則 `{BASELINE_NAME}`**（ETH 50%＋USDT 37.5%＋台幣 12.5%，門檻 5）。',
        f'- 通過標準在看結果前訂定（`src/crypto_robot/lab/basket_variants.py`），五項全過才算：再平衡 ≥ {MIN_REBALANCE_DAYS} 天；'
        f'研究期多賺；前後半期都多賺；回撤不比現行規則深；滑價 ×{STRESS_MULT} 後仍多賺。',
        '- **已知限制**：研究期的 ETH 已看過六次；BTC 和 ETH 高度相關。', '',
        '| 規則 | 通過 | 再平衡天數 | 報酬 | 現行規則 | 差距 | 最大回撤 | 現行回撤 | 成本 | 前半差距 | 後半差距 '
        f'| 滑價×{STRESS_MULT} 差距 | 未通過原因 |',
        '|---|---|---|---|---|---|---|---|---|---|---|---|---|',
    ]
    for r in rows:
        s, b, hv = r['stats'], r['base'], r['halves']
        lines.append(
            f'| {r["variant"]} | {"✅" if r["passed"] else "—"} | {r["days"]} | {pct(s.total_return)} | {pct(b.total_return)} '
            f'| {pct(s.total_return - b.total_return)} | {pct(s.max_drawdown)} | {pct(b.max_drawdown)} | {s.cost_pct:.1%} '
            f'| {pct(hv[0]["ret"] - hv[0]["base_ret"])} | {pct(hv[1]["ret"] - hv[1]["base_ret"])} '
            f'| {pct(r["stress"] - r["stress_base"])} | {"；".join(r["fails"])} |')
    passed = [r for r in rows if r['passed']]
    best = max(passed, key=lambda r: r['stats'].total_return - r['base'].total_return) if passed else None
    lines += ['', '## 結論', '',
              (f'通過：{", ".join("`" + r["variant"] + "`" for r in passed)}。考卷期只考研究期領先最多的 `{best["variant"]}`，只考一次。'
               if passed else '**沒有任何一組通過。** 不放寬標準，也不進考卷期；維持現行規則。'),
              '', '歷史統計，過去有效不代表未來有效。']
    return '\n'.join(lines) + '\n'
