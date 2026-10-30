"""研究工具：比例再平衡（第三輪，ETH＋台幣）、三資產再平衡（第五輪，--family rebalance3）與每小時檢查版（第六輪，--family rebalance3-1h）；五、六輪沒有考卷。

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

from crypto_robot.lab import hourly_research, multi_research, periods, rebalance_research, rebalance_variants  # noqa: E402
from crypto_robot.store.bars import BarStore  # noqa: E402

HOLDOUT_FILE = ROOT / 'state' / 'research' / 'holdout.json'
BREAKOUT_HOLDOUT = ROOT / 'state' / 'research' / 'holdout_breakout.json'     # 第七輪的考卷（山寨幣，只考一次）


def git_commit() -> str:
    try:
        head = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain', '--', 'src'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return head + ('-dirty' if dirty else '')
    except OSError:
        return 'unknown'


def breakout(args) -> None:
    """第七輪：山寨幣橫盤後帶量突破。日 K 先用 fetch_bars.py --period 1440 補齊。"""
    from crypto_robot.lab import breakout_research as br, breakout_variants as bv
    store = BarStore(args.db)
    data = {c: store.load(f'{c}twd', bv.PERIOD_MIN) for c in (*bv.COINS, bv.BENCH)}
    store.close()
    if not data[bv.BENCH]:
        sys.exit('快取沒有日 K，先執行 scripts/fetch_bars.py --market ethtwd --period 1440（其他幣同樣）')
    if args.stage == 'dev':
        text = br.dev_report(br.run_dev(data, bv.all_variants()), datetime.now().strftime('%Y-%m-%d %H:%M'))
        out = ROOT / 'reports' / f'research_dev_breakout_{datetime.now():%Y%m%d}.md'
        out.parent.mkdir(exist_ok=True)
        out.write_text(text, encoding='utf-8')
        print(text)
        print(f'已寫入 {out}')
        return
    if BREAKOUT_HOLDOUT.exists():
        print('考卷期已經考過，以下是當時的結果（不重算）：')
        print(BREAKOUT_HOLDOUT.read_text(encoding='utf-8'))
        return
    if not args.variant:
        sys.exit('考卷期要指定 --variant（研究期通過組中 t 最高的一組）')
    commit = git_commit()
    if commit.endswith('-dirty'):
        sys.exit('src/ 有未 commit 的修改；先 commit，考卷結果才能對應到確切的程式版本')
    v = bv.by_name(args.variant)
    if input(f'考卷期只能考一次，確定用 {v.name} 嗎？輸入 yes 繼續：').strip() != 'yes':
        sys.exit('已取消')
    end = data[bv.BENCH][-1].ts + bv.DAY_MS
    mid = (bv.HOLDOUT_START + end) // 2 // bv.DAY_MS * bv.DAY_MS
    r = br.evaluate(data, v, bv.HOLDOUT_START, end, mid)
    ok, fails = bv.passes_holdout(r['n'], r['mean_excess'], r['random_pct'])
    record = {'variant': v.name, 'commit': commit, 'tested_at': datetime.now().isoformat(timespec='seconds'),
              'n': r['n'], 'coins': r['coins'], 'mean_excess': r['mean_excess'], 't': r['t'],
              'random_pct': r['random_pct'], 'halves': r['halves'], 'passed': ok, 'fails': fails}
    BREAKOUT_HOLDOUT.parent.mkdir(parents=True, exist_ok=True)
    BREAKOUT_HOLDOUT.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(record, ensure_ascii=False, indent=2))


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--family', choices=['rebalance', 'rebalance3', 'rebalance3-1h', 'breakout'], default='rebalance')
    ap.add_argument('--stage', choices=['dev', 'holdout'], required=True)
    ap.add_argument('--variant', help='考卷期要考的規則名稱（見研究期報告）')
    ap.add_argument('--db', default=str(ROOT / 'data' / 'bars.db'))
    args = ap.parse_args()

    if args.family == 'breakout':
        breakout(args)
        return

    if args.family in ('rebalance3', 'rebalance3-1h'):
        if args.stage == 'holdout':
            sys.exit('考卷期已在第三輪用掉，第五、六輪沒有考卷；往後靠模擬帳戶觀察')
        store = BarStore(args.db)
        hourly = {a: store.load(m, periods.PERIOD_MIN) for a, m in multi_research.MARKETS.items()}
        store.close()
        if not all(hourly.values()):
            sys.exit('快取缺 K 線，先執行 scripts/fetch_bars.py 與 scripts/fetch_bars.py --market usdttwd --since 2020-12-01')
        mod = hourly_research if args.family == 'rebalance3-1h' else multi_research
        text = mod.dev_report(mod.run_dev(hourly), datetime.now().strftime('%Y-%m-%d %H:%M'))
        out = ROOT / 'reports' / f'research_dev_{args.family}_{datetime.now():%Y%m%d}.md'
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
