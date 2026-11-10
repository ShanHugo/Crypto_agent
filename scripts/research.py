"""研究工具：比例再平衡（第三輪，ETH＋台幣）與三資產再平衡（第五輪，ETH＋USDT＋台幣，用 --family rebalance3，沒有考卷）、
Binance 永續合約（第十四輪，--family futures；先跑 scripts/fetch_binance.py --futures）、
技術面與基本面研究 Agent（第十七輪，--family ta_fa；再先跑 scripts/fetch_macro.py）、
多幣排序（第二十一輪，--family xsec：先跑 scripts/fetch_xsec.py，--stage data 產生資料，訓練與預測用 scripts/xsec_train.py）。

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

from crypto_robot.lab import futures, multi_research, periods, rebalance_research, rebalance_variants, ta_fa, usstock, xsec, xsec_blend  # noqa: E402
from crypto_robot.lab.hourly import Series  # noqa: E402
from crypto_robot.store.bars import BarStore  # noqa: E402

HOLDOUT_FILE = ROOT / 'state' / 'research' / 'holdout.json'
HOLDOUT_FU = ROOT / 'state' / 'research' / 'holdout_futures.json'
HOLDOUT_TF = ROOT / 'state' / 'research' / 'holdout_ta_fa.json'
HOLDOUT_XS = ROOT / 'state' / 'research' / 'holdout_xsec.json'
XS_DEV = ROOT / 'state' / 'research' / 'xsec_dev.json'
XS_PREDS = ROOT / 'state' / 'research' / 'xsec_preds.jsonl'
XB_DEV = ROOT / 'state' / 'research' / 'xsec_blend_dev.json'
XB_RIDGE = ROOT / 'state' / 'research' / 'xsec_blend_ridge.jsonl'
US_DIR = ROOT / 'data' / 'usstock'
US_DEV = ROOT / 'state' / 'research' / 'usstock_dev.json'
US_PREDS = ROOT / 'state' / 'research' / 'usstock_preds.jsonl'
HOLDOUT_US = ROOT / 'state' / 'research' / 'holdout_usstock.json'


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


def run_xsec(args) -> None:
    if args.stage == 'holdout' and HOLDOUT_XS.exists():
        print('考卷已經考過，以下是當時的結果（不重算）：')
        print(HOLDOUT_XS.read_text(encoding='utf-8'))
        return
    folder = ROOT / 'data' / 'xsec'
    coins = xsec.load_coins(folder / 'coins')
    if not coins:
        sys.exit('缺資料，先執行 scripts/fetch_xsec.py')
    if args.stage == 'data':
        info = {wd: xsec.write_panel(folder / f'panel_{wd}.csv', coins, wd) for wd in xsec.OFFSET}
        print(json.dumps({'coins': len(coins), **info}, ensure_ascii=False, indent=2))
        return
    panels = {wd: xsec.read_panel(folder / f'panel_{wd}.csv') for wd in xsec.OFFSET}
    preds = xsec.read_preds(XS_PREDS)
    res = xsec.run_dev(coins, panels, preds)
    exam = None
    if args.stage == 'dev':
        XS_DEV.write_text(json.dumps({'pick': res['pick'], 'leak': res['leak'], 'at': datetime.now().isoformat()}, ensure_ascii=False), encoding='utf-8')
    else:
        name = args.variant or res['pick']         # --variant：營運者破例（2026-11-06 gbm，DESIGN 6.3 第二十一輪）
        if not name:
            sys.exit('研究期沒有組通過，不考')
        commit = git_commit()
        if commit.endswith('-dirty'):
            sys.exit('src/ 有未 commit 的修改；先 commit')
        exam = {**xsec.run_exam(coins, panels['mon'], preds, name), 'commit': commit, 'at': datetime.now().isoformat(), 'operator_exception': name != res['pick']}
        HOLDOUT_XS.write_text(json.dumps(exam, ensure_ascii=False, indent=2), encoding='utf-8')
    text = xsec.report(res, datetime.now().strftime('%Y-%m-%d %H:%M'), exam)
    out = ROOT / 'reports' / f'research_{args.stage}_xsec_{datetime.now():%Y%m%d}.md'
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding='utf-8')
    print(text)
    print(f'已寫入 {out}')


def run_xsec_blend(args) -> None:
    """第二十四輪：dev 算研究期並寫下判定；holdout 是參考段（判定寫下、補算 ridge 之後才能算）。"""
    folder = ROOT / 'data' / 'xsec'
    coins = xsec.load_coins(folder / 'coins')
    when = datetime.now().strftime('%Y-%m-%d %H:%M')
    preds = xsec.read_preds(XS_PREDS)
    if args.stage == 'dev':
        panels = {wd: xsec.read_panel(folder / f'panel_{wd}.csv') for wd in xsec.OFFSET}
        res = xsec_blend.run_dev(coins, panels, preds)
        XB_DEV.write_text(json.dumps({**res, 'at': datetime.now().isoformat()}, ensure_ascii=False, indent=1), encoding='utf-8')
        text = xsec_blend.dev_report(res, when)
    else:
        if not XB_DEV.exists():
            sys.exit('研究期判定還沒寫下，不准算參考段')
        if not XB_RIDGE.exists():
            sys.exit('先執行 scripts/xsec_blend_ridge.py 補算 ridge')
        for key, weeks in xsec.read_preds(XB_RIDGE).items():
            preds.setdefault(key, {}).update(weeks)
        dev = json.loads(XB_DEV.read_text(encoding='utf-8'))
        res = xsec_blend.run_reference(coins, xsec.read_panel(folder / 'panel_mon.csv'), preds)
        text = xsec_blend.reference_report(res, dev, when)
    name = 'dev' if args.stage == 'dev' else 'reference'
    out = ROOT / 'reports' / f'research_{name}_xsec_blend_{datetime.now():%Y%m%d}.md'
    out.write_text(text, encoding='utf-8')
    print(text)
    print(f'已寫入 {out}')


def run_usstock(args) -> None:
    """第二十六輪（DESIGN 6.22）：data 寫特徵表；dev 研究期判定（只能一次）；holdout 只考研究期通過的組，只考一次。"""
    if args.stage == 'holdout' and HOLDOUT_US.exists():
        print('考卷已經考過，以下是當時的結果（不重算）：')
        print(HOLDOUT_US.read_text(encoding='utf-8'))
        return
    stocks, spy = usstock.load(US_DIR / 'daily', US_DIR / 'universe.json')
    if args.stage == 'data':
        print(json.dumps(usstock.write_panel(US_DIR / 'panel.csv', stocks, spy), ensure_ascii=False))
        return
    panel, preds = usstock.read_panel(US_DIR / 'panel.csv'), usstock.read_preds(US_PREDS)
    when = datetime.now().strftime('%Y-%m-%d %H:%M')
    if args.stage == 'dev':
        if US_DEV.exists():
            sys.exit('研究期判定已經寫下，不重跑')
        res = usstock.run_dev(stocks, spy, panel, preds)
        US_DEV.write_text(json.dumps({'pick': res['pick'], 'leak': res['leak'], 'at': datetime.now().isoformat()}, ensure_ascii=False), encoding='utf-8')
        text = usstock.report(res, when)
    else:
        if not US_DEV.exists():
            sys.exit('研究期還沒判定')
        pick = json.loads(US_DEV.read_text(encoding='utf-8'))['pick']
        if not pick:
            sys.exit('研究期沒有組通過，不考')
        commit = git_commit()
        if commit.endswith('-dirty'):
            sys.exit('src/ 有未 commit 的修改；先 commit')
        exam = {**usstock.run_exam(stocks, spy, panel, preds, pick), 'commit': commit, 'at': datetime.now().isoformat()}
        HOLDOUT_US.write_text(json.dumps(exam, ensure_ascii=False, indent=2), encoding='utf-8')
        text = usstock.report(usstock.run_dev(stocks, spy, panel, preds), when, exam)
    out = ROOT / 'reports' / f'research_{args.stage}_usstock_{datetime.now():%Y%m%d}.md'
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding='utf-8')
    print(text)
    print(f'已寫入 {out}')


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--family', choices=['rebalance', 'rebalance3', 'futures', 'ta_fa', 'xsec', 'xsec_blend', 'usstock'], default='rebalance')
    ap.add_argument('--stage', choices=['data', 'dev', 'holdout'], required=True)
    ap.add_argument('--variant', help='考卷期要考的規則名稱（見研究期報告）')
    ap.add_argument('--db', default=str(ROOT / 'data' / 'bars.db'))
    args = ap.parse_args()

    if args.stage == 'data' and args.family not in ('xsec', 'usstock'):
        sys.exit('--stage data 只有 --family xsec、usstock 用')
    if args.family == 'usstock':
        run_usstock(args)
        return
    if args.family == 'futures':
        run_futures(args)
        return
    if args.family == 'ta_fa':
        run_ta_fa(args)
        return
    if args.family == 'xsec':
        run_xsec(args)
        return
    if args.family == 'xsec_blend':
        run_xsec_blend(args)
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
