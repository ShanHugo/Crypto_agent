"""研究工具：比例再平衡（第三輪，ETH＋台幣）與三資產再平衡（第五輪，ETH＋USDT＋台幣，用 --family rebalance3，沒有考卷）、
Binance 永續合約（第十四輪，--family futures；先跑 scripts/fetch_binance.py --futures）、
技術面與基本面研究 Agent（第十七輪，--family ta_fa；再先跑 scripts/fetch_macro.py）、
Kronos 加外部資訊再訓練（第十八輪，--family kronos_cov：--stage data 產生訓練資料，訓練與預測用 scripts/kronos_cov.py）。

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

from crypto_robot.lab import futures, multi_research, periods, rebalance_research, rebalance_variants, ta_fa, kronos_cov  # noqa: E402
from crypto_robot.lab.hourly import Series  # noqa: E402
from crypto_robot.store.bars import BarStore  # noqa: E402

HOLDOUT_FILE = ROOT / 'state' / 'research' / 'holdout.json'
HOLDOUT_FU = ROOT / 'state' / 'research' / 'holdout_futures.json'
HOLDOUT_TF = ROOT / 'state' / 'research' / 'holdout_ta_fa.json'
HOLDOUT_KC = ROOT / 'state' / 'research' / 'holdout_kronos_cov.json'
KC_DEV = ROOT / 'state' / 'research' / 'kronos_cov_dev.json'
KC_PREDS = ROOT / 'state' / 'research' / 'kronos_cov_preds.jsonl'


def git_commit() -> str:
    try:
        head = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain', '--', 'src'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return head + ('-dirty' if dirty else '')
    except OSError:
        return 'unknown'


def run_futures(args) -> None:
    if args.stage == 'holdout' and HOLDOUT_FU.exists():
        print('考卷已經考過，以下是當時的結果（不重算）：')
        print(HOLDOUT_FU.read_text(encoding='utf-8'))
        return
    store = BarStore(args.db)
    eth = Series.of(store.load(futures.MARKET, 60))
    store.close()
    fpath = ROOT / 'data' / 'funding_ethusdt.json'
    if not eth.c or not fpath.exists():
        sys.exit('缺合約資料，先執行 scripts/fetch_binance.py --futures')
    fund = futures.Funding([(t, float(r)) for t, r in json.loads(fpath.read_text(encoding='utf-8'))])
    res = futures.run_dev(eth, fund)
    if args.stage == 'dev':
        text = futures.dev_report(res, datetime.now().strftime('%Y-%m-%d %H:%M'))
        out = ROOT / 'reports' / f'research_dev_futures_{datetime.now():%Y%m%d}.md'
        out.parent.mkdir(exist_ok=True)
        out.write_text(text, encoding='utf-8')
        print(text)
        print(f'已寫入 {out}')
        return
    name = args.variant or futures.pick(res)       # --variant：營運者破例（2026-11-02 fund_streak-hold8，DESIGN 6.3）
    if not name:
        sys.exit('研究期沒有組合通過，不考')
    commit = git_commit()
    if commit.endswith('-dirty'):
        sys.exit('src/ 有未 commit 的修改；先 commit')
    if input(f'考卷只能考一次，考 {name}？輸入 yes 繼續：').strip() != 'yes':
        sys.exit('已取消')
    print(json.dumps(futures.run_holdout(eth, fund, name, HOLDOUT_FU, commit), ensure_ascii=False, indent=2))


def run_ta_fa(args) -> None:
    if args.stage == 'holdout' and HOLDOUT_TF.exists():
        print('考卷已經考過，以下是當時的結果（不重算）：')
        print(HOLDOUT_TF.read_text(encoding='utf-8'))
        return
    store = BarStore(args.db)
    eth, spot = Series.of(store.load(futures.MARKET, 60)), Series.of(store.load('binance_ethusdt', 60))
    store.close()
    fpath, mdir = ROOT / 'data' / 'funding_ethusdt.json', ROOT / 'data' / 'macro'
    if not eth.c or not spot.c or not fpath.exists() or not (mdir / 'WALCL.json').exists():
        sys.exit('缺資料，先執行 scripts/fetch_binance.py、scripts/fetch_binance.py --futures、scripts/fetch_macro.py')
    fund = futures.Funding([(t, float(r)) for t, r in json.loads(fpath.read_text(encoding='utf-8'))])
    m = ta_fa.load_macro(mdir)
    res = ta_fa.run_dev(eth, fund, m)
    exam = None
    if args.stage == 'holdout':
        commit = git_commit()
        if commit.endswith('-dirty'):
            sys.exit('src/ 有未 commit 的修改；先 commit')
        ok = [n for n, p in res['passed'].items() if p]
        if not ok:
            sys.exit('研究期沒有組合通過，不考')
        if input(f'考卷只能考一次（研究期通過：{"、".join(ok)}），輸入 yes 繼續：').strip() != 'yes':
            sys.exit('已取消')
        exam = ta_fa.run_exam(res, eth, spot, fund, m, HOLDOUT_TF, commit)
    text = ta_fa.report(res, datetime.now().strftime('%Y-%m-%d %H:%M'), exam)
    out = ROOT / 'reports' / f'research_{args.stage}_ta_fa_{datetime.now():%Y%m%d}.md'
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding='utf-8')
    print(text)
    print(f'已寫入 {out}')


def run_kronos_cov(args) -> None:
    if args.stage == 'holdout' and HOLDOUT_KC.exists():
        print('考卷已經考過，以下是當時的結果（不重算）：')
        print(HOLDOUT_KC.read_text(encoding='utf-8'))
        return
    store = BarStore(args.db)
    eth = Series.of(store.load(futures.MARKET, 60))
    store.close()
    fund = futures.Funding([(t, float(r)) for t, r in json.loads((ROOT / 'data' / 'funding_ethusdt.json').read_text(encoding='utf-8'))])
    if args.stage == 'data':
        info = kronos_cov.write_csv(ROOT / 'data' / 'kronos_cov' / 'eth_1h.csv', eth, fund, ta_fa.load_macro(ROOT / 'data' / 'macro'))
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return
    preds = kronos_cov.read_preds(KC_PREDS)
    res = kronos_cov.run_dev(preds, eth, fund)
    exam = None
    if args.stage == 'dev':
        KC_DEV.write_text(json.dumps({'passed': res['passed'], 'have': res['have'], 'at': datetime.now().isoformat()}, ensure_ascii=False), encoding='utf-8')
    else:
        if not res['passed']['K2']:
            sys.exit('研究期 K2 沒通過，不考')
        commit = git_commit()
        if commit.endswith('-dirty'):
            sys.exit('src/ 有未 commit 的修改；先 commit')
        exam = {**kronos_cov.run_exam(preds, eth, fund), 'commit': commit, 'at': datetime.now().isoformat()}
        HOLDOUT_KC.write_text(json.dumps(exam, ensure_ascii=False, indent=2), encoding='utf-8')
    text = kronos_cov.report(res, datetime.now().strftime('%Y-%m-%d %H:%M'), exam)
    out = ROOT / 'reports' / f'research_{args.stage}_kronos_cov_{datetime.now():%Y%m%d}.md'
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding='utf-8')
    print(text)
    print(f'已寫入 {out}')


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--family', choices=['rebalance', 'rebalance3', 'futures', 'ta_fa', 'kronos_cov'], default='rebalance')
    ap.add_argument('--stage', choices=['dev', 'holdout', 'data'], required=True)
    ap.add_argument('--variant', help='考卷期要考的規則名稱（見研究期報告）')
    ap.add_argument('--db', default=str(ROOT / 'data' / 'bars.db'))
    args = ap.parse_args()

    if args.family == 'futures':
        run_futures(args)
        return
    if args.family == 'ta_fa':
        run_ta_fa(args)
        return
    if args.family == 'kronos_cov':
        run_kronos_cov(args)
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
