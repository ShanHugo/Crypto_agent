"""研究工具（第三輪：ETH／台幣比例再平衡）：研究期比較全部規則；考卷期只考一次。

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

from crypto_robot.lab import periods, rebalance_research, rebalance_variants  # noqa: E402
from crypto_robot.store.bars import BarStore  # noqa: E402

HOLDOUT_FILE = ROOT / 'state' / 'research' / 'holdout.json'


def git_commit() -> str:
    try:
        head = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain', '--', 'src'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return head + ('-dirty' if dirty else '')
    except OSError:
        return 'unknown'


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--stage', choices=['dev', 'holdout'], required=True)
    ap.add_argument('--variant', help='考卷期要考的規則名稱（見研究期報告）')
    ap.add_argument('--db', default=str(ROOT / 'data' / 'bars.db'))
    args = ap.parse_args()

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
