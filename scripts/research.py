"""研究工具：比例再平衡（第三輪，ETH＋台幣）與三資產再平衡（第五輪，ETH＋USDT＋台幣，用 --family rebalance3，沒有考卷）。

    .venv\\Scripts\\python.exe scripts/research.py --stage dev
    .venv\\Scripts\\python.exe scripts/research.py --stage holdout       # 已考過：只顯示當時的結果

第 1、2、4 輪（1h EMA 交叉、日 K 趨勢／買低賣高、凌晨時段買低賣高）研究期全部未通過，程式已刪除，結果記在 docs/DESIGN.md 6.3。
先執行 scripts/fetch_bars.py 更新 K 線。考卷結果存在 state/research/holdout.json，之後執行只顯示當時的結果。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from crypto_robot.lab import multi_research, periods, rebalance_research, rebalance_variants  # noqa: E402
from crypto_robot.store.bars import BarStore  # noqa: E402

HOLDOUT_FILE = ROOT / 'state' / 'research' / 'holdout.json'


def git_commit() -> str:
    try:
        head = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain', '--', 'src'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return head + ('-dirty' if dirty else '')
    except OSError:
        return 'unknown'


def leverage(args) -> None:
    """第十一輪（槓桿第 3 關）：ETH 槓桿再平衡。沒有考卷期，只有 --stage dev（全部資料、前後兩半都要過）。"""
    from crypto_robot.lab import leverage_backtest as lb, leverage_variants as lv
    from crypto_robot.lab.common import day, pct
    if args.stage != 'dev':
        sys.exit('第十一輪沒有考卷期（ETH 的考卷已用掉）；只有 --stage dev')
    store = BarStore(args.db)
    hourly = {a: store.load(m, periods.PERIOD_MIN) for a, m in lv.MARKETS.items()}
    store.close()
    if not all(hourly.values()):
        sys.exit('快取缺 K 線，先執行 scripts/fetch_bars.py（ethtwd、usdttwd）')
    day_ms, h8 = 86_400_000, 8 * 3_600_000
    end = (min(b[-1].ts for b in hourly.values()) + h8) // day_ms * day_ms - h8      # 最後一個完整台北日之前
    rows = lb.align(hourly['eth'], hourly['usdt'], lv.START, end)
    halves = lambda r: [lb.segment(r, lv.START, lv.MID), lb.segment(r, lv.MID, end)]
    base = lb.simulate(rows, 1.0, 1.0, 0.0)
    bh = halves(base)
    from crypto_robot.lab.metrics import max_drawdown
    from crypto_robot.lab.multi_backtest import run_multi
    from crypto_robot.lab.multi_research import daily_bars
    from crypto_robot.lab.multi_variants import by_name as round5
    from crypto_robot.lab.common import COSTS
    from crypto_robot.lab.metrics import segment_return, stats
    spot = run_multi(daily_bars(hourly, lv.START, end), lv.START, end, round5('rebal3-eth50-cashusdt75-band5').params, COSTS)
    spot_h = [segment_return(spot, lv.START, lv.MID), segment_return(spot, lv.MID, end)]
    lines = [
        f'# 第十一輪報告（槓桿第 3 關：ETH 槓桿再平衡）（{datetime.now():%Y-%m-%d %H:%M}）', '',
        f'- 期間 {day(lv.START)} ～ {day(end - 1)}（全部資料，沒有考卷期），前半／後半以 {day(lv.MID)} 切開；逐小時模擬，期初 10,000 元。',
        f'- ETH 部位＝權益 × L，不足借 USDT；每小時按 USDT 利率 {lv.USDT_HOURLY_RATE:.7f}（年化約 {lv.USDT_HOURLY_RATE * 8760:.0%}）複利；'
        f'用每小時最低價算維持率，≤ {lv.LIQUIDATION_AD} 強平（殘值扣 {lv.LIQUIDATION_PENALTY:.0%}）。每天台北 00:00 收盤檢查，偏離 ±B 就調回。成本 {lb.COST:.2%}。',
        f'- 通過標準在看結果前訂定（`src/crypto_robot/lab/leverage_variants.py`），五項全過才算：沒強平；維持率最低 > {lv.MARGIN_CALL_AD}；'
        f'前後半期都贏 1 倍（一直持有 ETH）；最大回撤不超過 {lv.MAX_DRAWDOWN:.0%}；利率 ×{lv.STRESS_RATE_MULT} 後前後半期仍贏 1 倍。',
        '- **已知限制**：維持率門檻、強平處理來自第三方文章；利率固定用 2026-11-01 的數字；USDT 目前可借額度 0；ETH 價格已看過多次。', '',
        f'參考：1 倍（一直持有 ETH）前半 {pct(bh[0])}、後半 {pct(bh[1])}、最大回撤 {pct(max_drawdown([10_000.0] + [v for _, v in base.equity]))}；'
        f'現在真單的現貨規則前半 {pct(spot_h[0])}、後半 {pct(spot_h[1])}、最大回撤 {pct(stats(spot).max_drawdown)}。', '',
        '| 規則 | 通過 | 期末（10,000 元起） | 前半 | 後半 | 最大回撤 | 維持率最低 | 強平 | 再平衡次數 | 累計利息 | 利率×2 前半 | 利率×2 後半 | 未通過原因 |',
        '|---|---|---|---|---|---|---|---|---|---|---|---|---|',
    ]
    for v in lv.all_variants():
        r = lb.simulate(rows, v.lev, v.band, lv.USDT_HOURLY_RATE)
        s = lb.simulate(rows, v.lev, v.band, lv.USDT_HOURLY_RATE * lv.STRESS_RATE_MULT)
        h, sh = halves(r), halves(s)
        mdd = max_drawdown([10_000.0] + [x for _, x in r.equity])
        ok, fails = lv.passes(r.liquidated_at is not None, r.min_ad, h, bh, mdd, sh)
        lines.append(
            f'| {v.name} | {"✅" if ok else "—"} | {r.equity[-1][1]:,.0f} | {pct(h[0])} | {pct(h[1])} | {pct(mdd)} | {r.min_ad:.2f} '
            f'| {day(r.liquidated_at) if r.liquidated_at else "—"} | {r.rebalances} | {r.interest:,.0f} | {pct(sh[0])} | {pct(sh[1])} | {"；".join(fails)} |')
    lines += ['', '沒有通過就不做槓桿（DESIGN.md 6.6），不放寬標準。歷史統計，過去有效不代表未來有效。']
    text = '\n'.join(lines) + '\n'
    out = ROOT / 'reports' / f'research_leverage_{datetime.now():%Y%m%d}.md'
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding='utf-8')
    print(text)
    print(f'已寫入 {out}')


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--family', choices=['rebalance', 'rebalance3', 'leverage'], default='rebalance')
    ap.add_argument('--stage', choices=['dev', 'holdout'], required=True)
    ap.add_argument('--variant', help='考卷期要考的規則名稱（見研究期報告）')
    ap.add_argument('--db', default=str(ROOT / 'data' / 'bars.db'))
    args = ap.parse_args()

    if args.family == 'leverage':
        leverage(args)
        return

    if args.family == 'rebalance3':
        if args.stage == 'holdout':
            sys.exit('考卷期已在第三輪用掉，第五輪沒有考卷；往後靠影子帳戶觀察')
        store = BarStore(args.db)
        hourly = {a: store.load(m, periods.PERIOD_MIN) for a, m in multi_research.MARKETS.items()}
        store.close()
        if not all(hourly.values()):
            sys.exit('快取缺 K 線，先執行 scripts/fetch_bars.py 與 scripts/fetch_bars.py --market usdttwd --since 2020-12-01')
        text = multi_research.dev_report(multi_research.run_dev(hourly), datetime.now().strftime('%Y-%m-%d %H:%M'))
        out = ROOT / 'reports' / f'research_dev_rebalance3_{datetime.now():%Y%m%d}.md'
        out.parent.mkdir(exist_ok=True)
        out.write_text(text, encoding='utf-8')
        print(text)
        print(f'已寫入 {out}')
        return

    if args.stage == 'holdout' and HOLDOUT_FILE.exists():
        print('考卷期已經考過，以下是當時的結果（不重算）：')
        print(HOLDOUT_FILE.read_text(encoding='utf-8'))
        return

    store = BarStore(args.db)
    bars = store.load(periods.MARKET, periods.PERIOD_MIN)
    store.close()
    if not bars:
        sys.exit('快取沒有 K 線，先執行 scripts/fetch_bars.py')

    if args.stage == 'dev':
        text = rebalance_research.dev_report(rebalance_research.run_dev(bars), datetime.now().strftime('%Y-%m-%d %H:%M'))
        out = ROOT / 'reports' / f'research_dev_rebalance_{datetime.now():%Y%m%d}.md'
        out.parent.mkdir(exist_ok=True)
        out.write_text(text, encoding='utf-8')
        print(text)
        print(f'已寫入 {out}')
        return

    if not args.variant:
        sys.exit('考卷期要指定 --variant')
    commit = git_commit()
    if commit.endswith('-dirty'):
        sys.exit('src/ 有未 commit 的修改；先 commit，考卷結果才能對應到確切的程式版本')
    variant = rebalance_variants.by_name(args.variant)
    if input(f'考卷期只能考一次，確定用 {variant.name} 嗎？輸入 yes 繼續：').strip() != 'yes':
        sys.exit('已取消')
    end_ms = bars[-1].ts + periods.PERIOD_MIN * 60_000
    record = rebalance_research.run_holdout(bars, variant, end_ms, HOLDOUT_FILE, commit)
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
