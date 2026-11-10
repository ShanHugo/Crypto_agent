"""第二十六輪（DESIGN 6.22）：抓幣安美股合約對應的股票日 K（Yahoo，免費、不用金鑰）。

    .venv\\Scripts\\python.exe scripts/fetch_usstock.py

1. 股票池：幣安 EQUITY 合約、狀態 TRADING，Yahoo instrumentType 是 EQUITY 的才算（ETF、槓桿 ETF、查不到的都不算）。
   第一次執行時寫成 data/usstock/universe.json（2026-11-10 凍結），之後只讀、不改，幣安新上市的不加。
2. 每檔（加 SPY）抓上市以來的日 K：開高低收、還原收盤、成交量，存 data/usstock/daily/<代號>.json。
只用公開網址；不碰金鑰、資料庫、真單。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / 'data' / 'usstock'
UNIVERSE = DIR / 'universe.json'
DAILY = DIR / 'daily'
EXINFO = 'https://fapi.binance.com/fapi/v1/exchangeInfo'
CHART = 'https://query1.finance.yahoo.com/v8/finance/chart/{}?period1=0&period2={}&interval=1d&events=div%7Csplit'
META = 'https://query1.finance.yahoo.com/v8/finance/chart/{}?range=5d&interval=1d'
UA = {'User-Agent': 'Mozilla/5.0'}


def get(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return json.load(r)


def build_universe() -> dict:
    syms = [s for s in get(EXINFO)['symbols'] if s.get('underlyingType') == 'EQUITY' and s['status'] == 'TRADING']
    keep, dropped = [], {}
    for s in sorted(syms, key=lambda s: s['symbol']):
        try:
            m = get(META.format(s['baseAsset']))['chart']['result'][0]['meta']
            kind = m.get('instrumentType')
        except Exception as e:      # noqa: BLE001  查不到就排除（BRKB 在 Yahoo 是 BRK-B，照 6.22 也排除）
            kind = f'not found: {type(e).__name__}'
        if kind == 'EQUITY':
            keep.append({'perp': s['symbol'], 'ticker': s['baseAsset'], 'onboard': s['onboardDate']})
        else:
            dropped[s['symbol']] = kind
        time.sleep(0.2)
    return {'frozen_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'stocks': keep, 'dropped': dropped}


def daily(ticker: str) -> list[list]:
    r = get(CHART.format(ticker, int(time.time())))['chart']['result'][0]
    q, adj = r['indicators']['quote'][0], r['indicators']['adjclose'][0]['adjclose']
    out = []
    for i, ts in enumerate(r.get('timestamp') or []):
        o, h, lo, c, v, a = q['open'][i], q['high'][i], q['low'][i], q['close'][i], q['volume'][i], adj[i]
        if None in (o, c, a) or o <= 0 or c <= 0:
            continue
        day = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
        out.append([day, o, h, lo, c, a, v or 0])
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    DAILY.mkdir(parents=True, exist_ok=True)
    if UNIVERSE.exists():
        uni = json.loads(UNIVERSE.read_text(encoding='utf-8'))
    else:
        uni = build_universe()
        UNIVERSE.write_text(json.dumps(uni, ensure_ascii=False, indent=1), encoding='utf-8')
    tickers = [s['ticker'] for s in uni['stocks']] + ['SPY']
    print(f"股票池 {len(uni['stocks'])} 檔（凍結於 {uni['frozen_at']}），排除 {len(uni['dropped'])} 個")
    fails = []
    for t in tickers:
        try:
            rows = daily(t)
            (DAILY / f'{t}.json').write_text(json.dumps(rows), encoding='utf-8')
        except Exception as e:      # noqa: BLE001
            fails.append(f'{t}: {e}')
        time.sleep(0.3)
    print(f'日 K 完成 {len(tickers) - len(fails)}／{len(tickers)}' + (f'，失敗：{fails}' if fails else ''))


if __name__ == '__main__':
    main()
