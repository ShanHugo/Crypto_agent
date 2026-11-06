"""gbm 前瞻模擬的常駐程序（DESIGN.md 6.16）：每分鐘讀公開報價，模擬成交。不用金鑰、不下單。

    .venv\\Scripts\\python.exe scripts/futures_paper.py                 # 常駐（排程開機啟動）
    .venv\\Scripts\\python.exe scripts/futures_paper.py --once          # 跑一輪就結束
    .venv\\Scripts\\python.exe scripts/futures_paper.py --state DIR     # 換狀態資料夾（測試用）

暫停：建立 state/futures/paper.paused。目標部位由 scripts/xsec_forward.py 每週寫入 state/futures/targets.json。
"""
from __future__ import annotations

import argparse
import json
import msvcrt
import os
import sys
import time
import traceback
import urllib.parse
import urllib.request
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from crypto_robot.core.timeutil import TAIPEI  # noqa: E402
from crypto_robot.futures_agent import paper  # noqa: E402

FAPI = 'https://fapi.binance.com/fapi/v1/'
HOUR = 3_600_000
FUNDING_EVERY = 15 * 60_000
RULES_EVERY = HOUR


def get(path: str):
    with urllib.request.urlopen(FAPI + path, timeout=30) as r:
        return json.loads(r.read())


def quotes() -> dict[str, paper.Quote]:
    marks = {x['symbol']: Decimal(x['markPrice']) for x in get('premiumIndex')}
    out = {}
    for x in get('ticker/bookTicker'):
        s = x['symbol']
        bid, ask = Decimal(x['bidPrice']), Decimal(x['askPrice'])
        if s in marks and bid > 0 and ask > 0:
            out[s] = paper.Quote(bid, ask, marks[s])
    return out


def rules() -> dict[str, paper.Rule]:
    out = {}
    for x in get('exchangeInfo')['symbols']:
        if x['contractType'] == 'PERPETUAL' and x['status'] == 'TRADING' and x['quoteAsset'] == 'USDT':
            f = {i['filterType']: i for i in x['filters']}
            out[x['symbol']] = paper.Rule(Decimal(f['MARKET_LOT_SIZE']['stepSize']), Decimal(f['MIN_NOTIONAL']['notional']))
    return out


def funding(accounts: list[paper.Account], ledger: paper.Ledger, now: int) -> None:
    """用實際結算紀錄算資金費率（關機期間的也補得到）。"""
    syms = {s: min(a.positions[s].funded_ms for a in accounts if s in a.positions) for a in accounts for s in a.positions}
    for s, since in sorted(syms.items()):
        rows = get(f'fundingRate?symbol={urllib.parse.quote(s)}&startTime={since + 1}&limit=1000')
        for r in rows:
            mark = Decimal(r['markPrice']) if r.get('markPrice') else None
            for a in accounts:
                paper.apply_funding(a, s, Decimal(r['fundingRate']), mark, r['fundingTime'])
    for a in accounts:
        ledger.save(a, {'trades': [], 'skips': [], 'events': []}, now, False)


def read_target(path: Path) -> paper.Target | None:
    if not path.exists():
        return None
    d = json.loads(path.read_text(encoding='utf-8'))
    return paper.Target(d['week'], tuple(d['longs']), tuple(d['shorts']))


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    try:
        os.replace(tmp, path)
    except PermissionError:      # 有人剛好在讀（備份、操作台）：這次不更新，下一分鐘再寫
        pass


def one_round(state: Path, ledger: paper.Ledger, cache: dict, now: int) -> dict:
    if now - cache.get('rules_at', 0) >= RULES_EVERY:
        cache['rules'], cache['rules_at'] = rules(), now
    q = quotes()
    main, ref = ledger.load('main'), ledger.load('reference')
    if now - cache.get('funding_at', 0) >= FUNDING_EVERY:
        funding([main, ref], ledger, now)
        cache['funding_at'] = now
    target = read_target(state / 'targets.json')
    paused = (state / 'paper.paused').exists()
    hourly = now // HOUR != cache.get('hour')
    cache['hour'] = now // HOUR
    results = []
    for a, protect in ((main, True), (ref, False)):
        r = paper.tick(a, q, cache['rules'], target, now, paused, protect)
        ledger.save(a, r, now, hourly and bool(a.week))
        results.append(r)
        for e in r['events']:
            print(f"{datetime.fromtimestamp(now / 1000, TAIPEI):%Y-%m-%d %H:%M} {a.name} {e['kind']}：{e['detail']}", flush=True)
    return {'time': now, 'last_quote_ms': now, 'error': None, 'paused': paused, 'week': main.week, 'ended': main.ended,
            'equity': str(paper.equity(main)), 'reference': str(paper.equity(ref)), 'positions': len(main.positions)}


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--state', type=Path, default=ROOT / 'state' / 'futures')
    args = ap.parse_args()
    args.state.mkdir(parents=True, exist_ok=True)
    lock = (args.state / 'paper.lock').open('w')
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        sys.exit('已經有一個 futures_paper 在跑')
    ledger, cache, last_ok = paper.Ledger(args.state / 'futures.db'), {}, None
    print(f'{datetime.now(TAIPEI):%Y-%m-%d %H:%M} futures_paper 啟動', flush=True)
    while True:
        now = int(time.time() * 1000)
        try:
            beat = one_round(args.state, ledger, cache, now)
            last_ok = now
        except OSError as e:           # 連線問題：照實記，下一分鐘再試
            beat = {'time': now, 'last_quote_ms': last_ok, 'error': f'連線：{type(e).__name__}: {e}', 'kind': 'network'}
            print(f"{datetime.fromtimestamp(now / 1000, TAIPEI):%Y-%m-%d %H:%M} {beat['error']}", flush=True)
        except Exception as e:         # noqa: BLE001  程式錯誤：記下來給健康檢查，不讓常駐程序死掉
            beat = {'time': now, 'last_quote_ms': last_ok, 'error': f'{type(e).__name__}: {e}', 'kind': 'bug'}
            traceback.print_exc()
        write_json(args.state / 'heartbeat.json', beat)
        if args.once:
            print(json.dumps(beat, ensure_ascii=False))
            ledger.close()
            return
        time.sleep(max(1.0, 60 - (time.time() * 1000 - now) / 1000))


if __name__ == '__main__':
    main()
