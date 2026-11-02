"""研究工具：比例再平衡（第三輪，ETH＋台幣）與三資產再平衡（第五輪，ETH＋USDT＋台幣，用 --family rebalance3，沒有考卷）、
3 小時方向（第十二輪，--family direction3h，MAX＋Binance；先跑 scripts/fetch_binance.py）、
跌破 24h 低點後做多（第十三輪，--family fakeout）、
Binance 永續合約（第十四輪，--family futures；先跑 scripts/fetch_binance.py --futures）、
Kronos 模型 3 小時預測（第十五輪，--family kronos --stage holdout；先跑 scripts/kronos_predict.py）、
Kronos 預測隔天（第十六輪，--family kronos_daily；先跑 scripts/kronos_predict_daily.py）。

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

from crypto_robot.lab import direction3h, fakeout, futures, kronos3h, kronos_daily, multi_research, periods, rebalance_research, rebalance_variants  # noqa: E402
from crypto_robot.store.bars import BarStore  # noqa: E402

HOLDOUT_FILE = ROOT / 'state' / 'research' / 'holdout.json'
HOLDOUT_D3 = ROOT / 'state' / 'research' / 'holdout_direction3h.json'
HOLDOUT_FO = ROOT / 'state' / 'research' / 'holdout_fakeout.json'
HOLDOUT_FU = ROOT / 'state' / 'research' / 'holdout_futures.json'
HOLDOUT_KR = ROOT / 'state' / 'research' / 'holdout_kronos.json'
HOLDOUT_KD = ROOT / 'state' / 'research' / 'holdout_kronos_daily.json'


def git_commit() -> str:
    try:
        head = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain', '--', 'src'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return head + ('-dirty' if dirty else '')
    except OSError:
        return 'unknown'


def run_direction3h(args) -> None:
    if args.stage == 'holdout' and HOLDOUT_D3.exists():
        print('考卷已經考過，以下是當時的結果（不重算）：')
        print(HOLDOUT_D3.read_text(encoding='utf-8'))
        return
    store = BarStore(args.db)
    series = {ds: {k: (direction3h.Series.of(store.load(m, 60)) if m else None) for k, m in mk.items()}
              for ds, mk in direction3h.DATASETS.items()}
    store.close()
    if not all(sr['eth'].c for sr in series.values()):
        sys.exit('快取缺 K 線，先執行 scripts/fetch_bars.py 與 scripts/fetch_binance.py')
    res = direction3h.run_dev(series)
    if args.stage == 'dev':
        text = direction3h.dev_report(res, datetime.now().strftime('%Y-%m-%d %H:%M'))
        out = ROOT / 'reports' / f'research_dev_direction3h_{datetime.now():%Y%m%d}.md'
        out.parent.mkdir(exist_ok=True)
        out.write_text(text, encoding='utf-8')
        print(text)
        print(f'已寫入 {out}')
        return
    name = direction3h.pick(res)
    if not name:
        sys.exit('研究期沒有訊號通過，不考')
    commit = git_commit()
    if commit.endswith('-dirty'):
        sys.exit('src/ 有未 commit 的修改；先 commit')
    if input(f'考卷只能考一次，考 {name}？輸入 yes 繼續：').strip() != 'yes':
        sys.exit('已取消')
    print(json.dumps(direction3h.run_holdout(series, name, HOLDOUT_D3, commit), ensure_ascii=False, indent=2))


def run_fakeout(args) -> None:
    if args.stage == 'holdout' and HOLDOUT_FO.exists():
        print('考卷已經考過，以下是當時的結果（不重算）：')
        print(HOLDOUT_FO.read_text(encoding='utf-8'))
        return
    store = BarStore(args.db)
    eth = {ds: direction3h.Series.of(store.load(mk['eth'], 60)) for ds, mk in direction3h.DATASETS.items()}
    store.close()
    if not all(e.c for e in eth.values()):
        sys.exit('快取缺 K 線，先執行 scripts/fetch_bars.py 與 scripts/fetch_binance.py')
    res = fakeout.run_dev(eth)
    if args.stage == 'dev':
        text = fakeout.dev_report(res, datetime.now().strftime('%Y-%m-%d %H:%M'))
        out = ROOT / 'reports' / f'research_dev_fakeout_{datetime.now():%Y%m%d}.md'
        out.parent.mkdir(exist_ok=True)
        out.write_text(text, encoding='utf-8')
        print(text)
        print(f'已寫入 {out}')
        return
    name = fakeout.pick(res)
    if not name:
        sys.exit('研究期沒有組合通過，不考')
    commit = git_commit()
    if commit.endswith('-dirty'):
        sys.exit('src/ 有未 commit 的修改；先 commit')
    if input(f'考卷只能考一次，考 {name}？輸入 yes 繼續：').strip() != 'yes':
        sys.exit('已取消')
    print(json.dumps(fakeout.run_holdout(eth, name, HOLDOUT_FO, commit), ensure_ascii=False, indent=2))


def run_futures(args) -> None:
    if args.stage == 'holdout' and HOLDOUT_FU.exists():
        print('考卷已經考過，以下是當時的結果（不重算）：')
        print(HOLDOUT_FU.read_text(encoding='utf-8'))
        return
    store = BarStore(args.db)
    eth = direction3h.Series.of(store.load(futures.MARKET, 60))
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


def run_kronos(args) -> None:
    if HOLDOUT_KR.exists():
        print('考卷已經考過，以下是當時的結果（不重算）：')
        print(HOLDOUT_KR.read_text(encoding='utf-8'))
        return
    if args.stage == 'dev':
        sys.exit('第十五輪不調參數、沒有研究期，直接用 --stage holdout 考一次')
    store = BarStore(args.db)
    eth = {ds: direction3h.Series.of(store.load(m, 60)) for ds, m in kronos3h.DATASETS.items()}
    store.close()
    paths = {ds: ROOT / 'data' / 'kronos' / f'{m}.json' for ds, m in kronos3h.DATASETS.items()}
    if not all(p.exists() for p in paths.values()):
        sys.exit('缺預測，先用 Kronos 的 venv 跑 scripts/kronos_predict.py')
    commit = git_commit()
    if commit.endswith('-dirty'):
        sys.exit('src/ 有未 commit 的修改；先 commit')
    if input('考卷只能考一次，考 Kronos？輸入 yes 繼續：').strip() != 'yes':
        sys.exit('已取消')
    preds = {ds: kronos3h.load_preds(p) for ds, p in paths.items()}
    print(json.dumps(kronos3h.run_exam(eth, preds, HOLDOUT_KR, commit), ensure_ascii=False, indent=2))


def run_kronos_daily(args) -> None:
    if args.stage == 'holdout' and HOLDOUT_KD.exists():
        print('考卷已經考過，以下是當時的結果（不重算）：')
        print(HOLDOUT_KD.read_text(encoding='utf-8'))
        return
    from crypto_robot.agents.data.resample import to_daily
    kd = kronos_daily
    store = BarStore(args.db)
    hourly = {ds: store.load(m, 60) for ds, m in kd.DATASETS.items()}
    usdt = to_daily(store.load('usdttwd', 60))
    store.close()
    eth = {ds: direction3h.Series.of(b) for ds, b in hourly.items()}
    days = {ds: to_daily(b) for ds, b in hourly.items()}

    def load(stage):
        paths = {(ds, i): ROOT / 'data' / 'kronos_daily' / f'{m}_{i}.json' for ds, m in kd.DATASETS.items() for i in kd.INPUTS}
        if not all(p.exists() for p in paths.values()):
            sys.exit(f'缺預測，先用 Kronos 的 venv 跑 scripts/kronos_predict_daily.py --stage {stage}')
        preds = {ds: {i: kd.load_preds(paths[(ds, i)]) for i in kd.INPUTS} for ds in kd.DATASETS}
        slots = {ds: kd.slots_for(eth[ds], days[ds], stage) for ds in kd.DATASETS}
        miss = [ds for ds in kd.DATASETS for i in kd.INPUTS if any(s not in preds[ds][i] for s in slots[ds])]
        if miss:
            sys.exit(f'{miss} 的預測沒跑完，先跑 scripts/kronos_predict_daily.py --stage {stage}')
        return preds, slots

    res = kd.run('dev', eth, days, usdt, *load('dev'))
    if args.stage == 'dev':
        text = kd.report(res, 'dev', datetime.now().strftime('%Y-%m-%d %H:%M'))
        out = ROOT / 'reports' / f'research_dev_kronos_daily_{datetime.now():%Y%m%d}.md'
        out.parent.mkdir(exist_ok=True)
        out.write_text(text, encoding='utf-8')
        print(text)
        print(f'已寫入 {out}')
        return
    name = kd.pick(res)
    if not name:
        sys.exit('研究期沒有組合通過，不考')
    commit = git_commit()
    if commit.endswith('-dirty'):
        sys.exit('src/ 有未 commit 的修改；先 commit')
    if input(f'考卷只能考一次，考 {name}？輸入 yes 繼續：').strip() != 'yes':
        sys.exit('已取消')
    ex = kd.run('exam', eth, days, usdt, *load('exam'), only=name)
    print(kd.report(ex, 'exam', datetime.now().strftime('%Y-%m-%d %H:%M')))
    print(json.dumps(kd.save_exam(ex, name, HOLDOUT_KD, commit), ensure_ascii=False, indent=2, default=str))


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--family', choices=['rebalance', 'rebalance3', 'direction3h', 'fakeout', 'futures', 'kronos', 'kronos_daily'], default='rebalance')
    ap.add_argument('--stage', choices=['dev', 'holdout'], required=True)
    ap.add_argument('--variant', help='考卷期要考的規則名稱（見研究期報告）')
    ap.add_argument('--db', default=str(ROOT / 'data' / 'bars.db'))
    args = ap.parse_args()

    if args.family == 'direction3h':
        run_direction3h(args)
        return
    if args.family == 'fakeout':
        run_fakeout(args)
        return
    if args.family == 'futures':
        run_futures(args)
        return
    if args.family == 'kronos':
        run_kronos(args)
        return
    if args.family == 'kronos_daily':
        run_kronos_daily(args)
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
