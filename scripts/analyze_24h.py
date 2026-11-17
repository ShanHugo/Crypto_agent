"""「我的合約」24 小時多空分析（DESIGN 6.30；未驗證、只記錄，抓幣安公開資料、不用金鑰、不下單）。

    .venv\\Scripts\\python.exe scripts/analyze_24h.py ZEC          # 分析 ZECUSDT，記到 state/forecast/analyze_24h.jsonl
    .venv\\Scripts\\python.exe scripts/analyze_24h.py --score      # 只替滿 24 小時的舊分析對答案

Kronos 在另一個 venv 跑（scripts/kronos_model.py，週線 → 5 分 6 個週期，模型只載入一次）。
每次執行都會先替滿 24 小時的舊分析對答案（analyze_24h_result.jsonl，只新增）。第一行印給操作台看的摘要。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import weekly_review as wr  # noqa: E402

from crypto_robot.core.timeutil import TAIPEI  # noqa: E402
from crypto_robot.forecast import mtf24, picks, trend  # noqa: E402

LOG = ROOT / 'state' / 'forecast' / 'analyze_24h.jsonl'
RESULTS = ROOT / 'state' / 'forecast' / 'analyze_24h_result.jsonl'
KRONOS_PY = ROOT.parent / '_vendor' / 'kronos_venv' / 'Scripts' / 'python.exe'
CALL = {1: '偏多', -1: '偏空', 0: '不明'}


def append(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


def score_due(now: int) -> int:
    done = {(r['t'], r['sym']) for r in trend.read_log(RESULTS)}
    new = []
    for row in trend.read_log(LOG):
        end = row['t'] + mtf24.D
        if (row['t'], row['sym']) in done or end + 5 * mtf24.M > now:
            continue
        k = wr.fapi('/fapi/v1/klines', symbol=row['sym'], interval='5m', startTime=end, limit=1)
        if k:
            new.append(mtf24.settle(row, float(k[0][1])))
    append(RESULTS, new)
    return len(new)


def rules(sym: str, now: int) -> dict:
    """規則面（不進結論）：週線＋日線均線方向、2×ATR 停損距離、3～5 倍可用倍數、資金費率。"""
    d = trend.Daily.from_klines(wr.fapi('/fapi/v1/klines', symbol=sym, interval='1d', limit=200))
    T = now // trend.D * trend.D
    sig = picks.lean(d, T) if T in d.pos else None
    fund = float(wr.fapi('/fapi/v1/premiumIndex', symbol=sym)['lastFundingRate'])
    if not sig:
        return {'side': 0, 'breakout': False, 'stop_pct': None, 'lev': None, 'funding': fund}
    side, _, atr, breakout = sig
    stop_pct = trend.STOP_K * atr / d.o[d.pos[T]]
    return {'side': side, 'breakout': breakout, 'stop_pct': stop_pct, 'lev': picks.leverage(stop_pct), 'funding': fund}


def analyze(sym: str, now: int) -> dict:
    reqs, last = {}, {}
    for name, step, steps in mtf24.FRAMES:
        rows = mtf24.closed(wr.fapi('/fapi/v1/klines', symbol=sym, interval=name, limit=mtf24.LOOKBACK + 1), now)
        req = mtf24.model_req(rows, step, steps, now // mtf24.M)
        if req:
            reqs[name], last[name] = req, req['close'][-1]
    if not reqs:
        raise SystemExit(f'{sym} K 線太少，沒辦法分析')
    out = subprocess.run([str(KRONOS_PY), str(ROOT / 'scripts' / 'kronos_model.py')], input=json.dumps({'frames': reqs}),
                         capture_output=True, text=True, timeout=900)
    if out.returncode != 0:
        raise SystemExit('Kronos 失敗：' + out.stderr[-500:])
    rets = json.loads(out.stdout.strip().splitlines()[-1])['frames']
    frames = {name: (mtf24.frame_row(rets[name], last[name]) if name in rets else None) for name, _, _ in mtf24.FRAMES}
    price = float(wr.fapi('/fapi/v1/ticker/price', symbol=sym)['price'])
    return {'t': now, 'sym': sym, 'price': price, 'frames': frames, **mtf24.combine(frames), 'rules': rules(sym, now)}


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('symbol', nargs='?')
    ap.add_argument('--score', action='store_true')
    a = ap.parse_args()
    now = int(time.time() * 1000)
    if a.score or not a.symbol:
        print(f'對答案 {score_due(now)} 筆')
        return 0
    sym = re.sub(r'[^A-Z0-9]', '', a.symbol.upper())
    sym = sym if sym.endswith('USDT') else sym + 'USDT'
    if not 5 <= len(sym) <= 24:
        print('幣名不對')
        return 2
    try:
        wr.fapi('/fapi/v1/ticker/price', symbol=sym)
    except OSError:
        print(f'幣安 USDT 永續找不到 {sym}')
        return 2
    t0 = time.time()
    row = analyze(sym, now)
    append(LOG, [row])
    scored = score_due(now)
    at = datetime.fromtimestamp(now / 1000, TAIPEI).strftime('%m-%d %H:%M')
    tail = '（逆週線）' if row['against_week'] else ''
    score = '—' if row['score'] is None else f"{row['score']:.0%}"
    print(f"{at} {sym} 接下來 24 小時：{CALL[row['call']]}{tail}，收漲比例 {score}（{time.time() - t0:.0f} 秒；對答案 {scored} 筆）")
    print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
