"""抓第十七輪基本面用的公開資料到 data/macro/（DESIGN.md 6.8；都不需要金鑰）。

    .venv\\Scripts\\python.exe scripts/fetch_macro.py

每份存成 [[毫秒, 數值], ...]（日期是資料本身的日期，UTC 00:00；公布延遲在 lab/ta_fa.py 處理）：
- stablecoins：DefiLlama 穩定幣總流通量（美元）
- WALCL、RRPONTSYD、WTREGEN、DTWEXBGS、DFII10：FRED（CSV，缺值略過）
- premium_ethusdt：Binance ETHUSDT 永續合約溢價指數日 K 的收盤（基差）
"""
from __future__ import annotations

import csv
import io
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'macro'
FRED = ['WALCL', 'RRPONTSYD', 'WTREGEN', 'DTWEXBGS', 'DFII10']
PREMIUM = 'https://fapi.binance.com/fapi/v1/premiumIndexKlines?symbol=ETHUSDT&interval=1d&startTime={}&limit=1000'


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': 'crypto-robot-research'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def fred(sid: str) -> list:
    rows = csv.reader(io.StringIO(_get(f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}').decode()))
    next(rows)
    return [[int(datetime.strptime(d, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp() * 1000), float(v)]
            for d, v in rows if v not in ('', '.')]


def stablecoins() -> list:
    rows = json.loads(_get('https://stablecoins.llama.fi/stablecoincharts/all'))
    return [[int(r['date']) * 1000, float(r['totalCirculatingUSD'].get('peggedUSD', 0))] for r in rows]


def premium() -> list:
    out, since = [], 1_569_888_000_000
    now = int(time.time() * 1000)
    while True:
        rows = json.loads(_get(PREMIUM.format(since)))
        out += [[int(k[0]), float(k[4])] for k in rows if int(k[6]) < now]     # 只留已收盤
        if len(rows) < 1000:
            return out
        since = int(rows[-1][0]) + 1
        time.sleep(0.2)


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = {**{s: (lambda s=s: fred(s)) for s in FRED}, 'stablecoins': stablecoins, 'premium_ethusdt': premium}
    for name, fn in jobs.items():
        rows = fn()
        (OUT / f'{name}.json').write_text(json.dumps(rows), encoding='utf-8')
        print(f'{name}: {len(rows)} 筆，{datetime.fromtimestamp(rows[0][0] / 1000, timezone.utc):%Y-%m-%d}～'
              f'{datetime.fromtimestamp(rows[-1][0] / 1000, timezone.utc):%Y-%m-%d}')


if __name__ == '__main__':
    main()
