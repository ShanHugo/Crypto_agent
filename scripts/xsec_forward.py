"""gbm 前瞻模擬的每週目標（DESIGN.md 6.16）：補資料 → 選前 50 大 → 算特徵 → gbm → 寫 10 多 10 空。不用金鑰。

    .venv\\Scripts\\python.exe scripts/xsec_forward.py              # 排程每天 08:05 執行；這週已經寫過就直接結束
    .venv\\Scripts\\python.exe scripts/xsec_forward.py --dry-run    # 算到最後但不寫目標，印出名單（可以在第一週之前試跑）

訓練與預測在 _vendor/ml_venv（scripts/xsec_forward_train.py）。規則同第二十一輪，選幣、特徵、選股都用 lab/xsec.py。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from crypto_robot.core.timeutil import TAIPEI  # noqa: E402
from crypto_robot.lab import xsec  # noqa: E402
from crypto_robot.lab.hourly import ms  # noqa: E402
from crypto_robot.lab.xsec import D, W  # noqa: E402

FAPI = 'https://fapi.binance.com/fapi/v1/'
COINS = ROOT / 'data' / 'xsec' / 'coins'
PANEL = ROOT / 'data' / 'xsec' / 'forward_panel.csv'
STATE = ROOT / 'state' / 'futures'
FIRST_WEEK = ms(2026, 11, 9)
RECENT_WEEKS = 8
LONG_GONE = 14 * D


def get(path: str):
    for attempt in range(5):
        try:
            with urllib.request.urlopen(FAPI + path, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return []
            if attempt == 4:
                raise
            time.sleep(30 if e.code == 429 else 2 ** attempt)
        except OSError:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError


def tradable_symbols() -> list[str]:
    return sorted(x['symbol'] for x in get('exchangeInfo')['symbols']
                  if x['contractType'] == 'PERPETUAL' and x['status'] == 'TRADING' and x['quoteAsset'] == 'USDT'
                  and x.get('underlyingType') == 'COIN' and x['symbol'] not in xsec.EXCLUDE)


def read_coin(sym: str) -> dict:
    p = COINS / f'{sym}.json'
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'k': [], 'f': []}


def write_coin(sym: str, data: dict) -> None:
    tmp = (COINS / f'{sym}.json').with_suffix('.tmp')
    tmp.write_text(json.dumps(data), encoding='utf-8')
    os.replace(tmp, COINS / f'{sym}.json')


def update_bars(sym: str, now: int) -> int:
    """補已收盤的日 K。"""
    data = read_coin(sym)
    start = data['k'][-1][0] + D if data['k'] else 0
    added = 0
    while start + D <= now:
        rows = get(f'klines?symbol={urllib.parse.quote(sym)}&interval=1d&startTime={start}&limit=1000')
        rows = [r for r in rows if r[0] + D <= now]
        if not rows:
            break
        data['k'] += [[r[0], float(r[1]), float(r[4]), float(r[7]), float(r[10])] for r in rows]
        added += len(rows)
        start = rows[-1][0] + D
        if len(rows) < 1000:
            break
    if added:
        write_coin(sym, data)
    return added


def update_funding(sym: str, now: int) -> int:
    data = read_coin(sym)
    start = data['f'][-1][0] + 1 if data['f'] else (data['k'][0][0] if data['k'] else 0)
    added = 0
    while start < now:
        rows = get(f'fundingRate?symbol={urllib.parse.quote(sym)}&startTime={start}&limit=1000')
        if not rows:
            break
        data['f'] += [[r['fundingTime'], float(r['fundingRate'])] for r in rows]
        added += len(rows)
        start = rows[-1]['fundingTime'] + 1
        if len(rows) < 1000:
            break
        time.sleep(0.3)
    if added:
        write_coin(sym, data)
    return added


def current_week(now: int) -> int:
    return xsec.FIRST_MONDAY + (now - xsec.FIRST_MONDAY) // W * W


def write_panel(coins: dict[str, xsec.Coin], t_last: int, now: int) -> dict:
    """同第二十一輪的 write_panel，多了期末：持有還沒結束的週答案留空。"""
    market = xsec.market_returns(coins)
    tmp = PANEL.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['ts', 'sym', 'n', 'label'] + xsec.FEATURES)
        for t in range(xsec.FIRST_MONDAY, t_last + 1, W):
            uni = xsec.universe(coins, t)
            if len(uni) < xsec.MIN_UNI:
                continue
            done = t + W <= now
            ys = xsec.rank([xsec.label(c, t) for c in uni]) if done else [None] * len(uni)
            cols = [xsec.rank(list(col)) for col in zip(*(c.features(t, market) for c in uni))]
            for i, c in enumerate(uni):
                w.writerow([t, c.sym, len(uni), '' if ys[i] is None else round(ys[i], 6)] + [round(col[i], 6) for col in cols])
    os.replace(tmp, PANEL)
    return {'universe': [c.sym for c in xsec.universe(coins, t_last)]}


def predict(t: int) -> dict[str, float]:
    py = ROOT.parent / '_vendor' / 'ml_venv' / 'Scripts' / 'python.exe'
    out = STATE / 'preds' / f'{t}.json'
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    subprocess.run([str(py), '-I', str(ROOT / 'scripts' / 'xsec_forward_train.py'), str(t)], check=True, env=env, cwd=ROOT)
    return json.loads(out.read_text(encoding='utf-8'))


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    now = int(time.time() * 1000)
    t = current_week(now)
    target_path = STATE / 'targets.json'
    if not args.dry_run:
        if t < FIRST_WEEK:
            print(f'第一週是 2026-11-09，現在還沒開始（{datetime.fromtimestamp(now / 1000, TAIPEI):%Y-%m-%d %H:%M}）')
            return
        if target_path.exists() and json.loads(target_path.read_text(encoding='utf-8'))['week'] >= t:
            print('這週的目標已經寫過')
            return
    start = time.time()
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / 'preds').mkdir(exist_ok=True)
    existing = {p.stem for p in COINS.glob('*.json')}
    names = set(tradable_symbols())
    recent = [s for s in existing - names if (d := read_coin(s)['k']) and d[-1][0] >= now - LONG_GONE]
    for n, s in enumerate(sorted(names | set(recent)), 1):
        update_bars(s, now)
        if n % 100 == 0:
            print(f'K 線 {n}／{len(names) + len(recent)}，{time.time() - start:.0f} 秒', flush=True)
    coins = xsec.load_coins(COINS)
    need = {c.sym for w in range(RECENT_WEEKS) for c in xsec.universe(coins, t - w * W)}
    for s in sorted(need):
        update_funding(s, now)
        time.sleep(0.2)
    coins = xsec.load_coins(COINS)
    info = write_panel(coins, t, now)
    if len(info['universe']) < xsec.MIN_UNI:
        sys.exit(f"選幣池只有 {len(info['universe'])} 個，不換倉")
    preds = predict(t)
    longs, shorts = xsec.select({s: preds[s] for s in info['universe']})
    data = {'week': t, 'longs': longs, 'shorts': shorts, 'universe': info['universe'], 'created_at': now}
    print(f"{datetime.fromtimestamp(t / 1000, TAIPEI):%Y-%m-%d} 做多 {longs}\n做空 {shorts}\n耗時 {time.time() - start:.0f} 秒", flush=True)
    if args.dry_run:
        print('試跑：沒有寫入目標')
        return
    with (STATE / 'targets_history.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(data, ensure_ascii=False) + '\n')
    tmp = target_path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, target_path)


if __name__ == '__main__':
    main()
