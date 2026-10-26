"""第五輪研究（三資產再平衡）：只有研究期，沒有考卷（考卷期已在第三輪用掉）。"""
from __future__ import annotations

from crypto_robot.agents.data.resample import to_daily
from crypto_robot.core.models import Bar
from crypto_robot.lab import periods
from crypto_robot.lab.common import COSTS, DEV_MID, day, pct, window
from crypto_robot.lab.metrics import segment_return, stats
from crypto_robot.lab.multi_backtest import rebalance_days, run_multi
from crypto_robot.lab.multi_variants import MIN_REBALANCE_DAYS, SHADOW, MultiVariant, all_variants, passes

MARKETS = {'eth': 'ethtwd', 'usdt': 'usdttwd'}


def daily_bars(hourly: dict[str, list[Bar]], start_ms: int, end_ms: int) -> dict[str, list[Bar]]:
    """hourly：資產 → 1h K 線。各自檢查資料品質後合成日 K。"""
    return {a: to_daily(window(bars, start_ms, end_ms, 48)) for a, bars in hourly.items()}


def evaluate(hourly: dict[str, list[Bar]], v: MultiVariant, start_ms: int, end_ms: int,
             halves: list[tuple[int, int]]) -> dict:
    daily = daily_bars(hourly, start_ms, end_ms)
    res = run_multi(daily, start_ms, end_ms, v.params, COSTS)
    base = run_multi({'eth': daily['eth'], 'usdt': daily['usdt']}, start_ms, end_ms, v.baseline, COSTS)
    static = run_multi(daily, start_ms, end_ms, v.params, COSTS, rebalance=False)
    s, bs = stats(res), stats(base)
    half_rows = [{'ret': segment_return(res, a, b), 'base_ret': segment_return(base, a, b),
                  'static_ret': segment_return(static, a, b)} for a, b in halves]
    n = rebalance_days(res)
    ok, fails = passes(n, s.total_return, bs.total_return, s.max_drawdown, bs.max_drawdown, half_rows)
    return {'variant': v.name, 'days': n, 'base_days': rebalance_days(base), 'stats': s, 'base': bs,
            'static': stats(static), 'halves': half_rows, 'passed': ok, 'fails': fails}


def run_dev(hourly: dict[str, list[Bar]]) -> list[dict]:
    halves = [(periods.DEV_START, DEV_MID), (DEV_MID, periods.DEV_END)]
    return [evaluate(hourly, v, periods.DEV_START, periods.DEV_END, halves) for v in all_variants()]


def dev_report(rows: list[dict], generated: str) -> str:
    lines = [
        f'# 研究期報告（第五輪，ETH＋USDT＋台幣 三資產再平衡）（{generated}）', '',
        f'- 研究期 {day(periods.DEV_START)} ～ {day(periods.DEV_END - 1)}，前半／後半以 {day(DEV_MID)} 切開；日 K 以台北時間 00:00 為日界。',
        f'- ETH 固定 50%，剩下的現金一部分換成 USDT（USDT/TWD ≈ 美元兌台幣）。任何一項偏離目標達門檻就全部調回；成本 taker {COSTS.taker_fee:%}＋滑價 {COSTS.slippage:%}（兩個市場相同）。',
        '- **比較對象：現在的兩資產再平衡（ETH 50%＋台幣 50%，同一個門檻）**；同比例不再平衡只供參考。',
        f'- 同時比較 {len(rows)} 組，通過標準在看結果前訂定（`src/crypto_robot/lab/multi_variants.py`），五項全過才算：'
        f'再平衡 ≥ {MIN_REBALANCE_DAYS} 天；研究期多賺；前後半期都多賺；回撤不比兩資產深；前後半期報酬都 > 0。',
        '- **已知限制**：研究期的 ETH 資料已看過四次；考卷期已用掉，這輪沒有考卷。'
        f'不論結果，`{SHADOW}` 都會放進影子帳戶往後觀察（使用者決定）。', '',
        '| 規則 | 通過 | 再平衡天數 | 報酬 | 兩資產 | 差距 | 最大回撤 | 兩資產回撤 | 不再平衡 | 成本 | 前半差距 | 後半差距 | 未通過原因 |',
        '|---|---|---|---|---|---|---|---|---|---|---|---|---|',
    ]
    for r in rows:
        s, b, hv = r['stats'], r['base'], r['halves']
        name = f'**{r["variant"]}**' if r['variant'] == SHADOW else r['variant']
        lines.append(
            f'| {name} | {"✅" if r["passed"] else "—"} | {r["days"]} | {pct(s.total_return)} | {pct(b.total_return)} '
            f'| {pct(s.total_return - b.total_return)} | {pct(s.max_drawdown)} | {pct(b.max_drawdown)} '
            f'| {pct(r["static"].total_return)} | {s.cost_pct:.1%} | {pct(hv[0]["ret"] - hv[0]["base_ret"])} '
            f'| {pct(hv[1]["ret"] - hv[1]["base_ret"])} | {"；".join(r["fails"])} |')
    passed = [r['variant'] for r in rows if r['passed']]
    lines += ['', '## 結論', '',
              (f'通過：{", ".join(f"`{p}`" for p in passed)}。' if passed else '**沒有任何一組通過。** 不放寬標準。'),
              f'不論結果，`{SHADOW}` 放進影子帳戶，和現在的兩資產模擬帳戶並行觀察（沒有考卷，只能靠往後的紀錄）。',
              '', '歷史統計，過去有效不代表未來有效。']
    return '\n'.join(lines) + '\n'
