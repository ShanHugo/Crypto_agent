"""抓 Binance 現貨 1h K 線到 data/bars.db（第十二輪研究的對照資料；只用公開 API，不需要金鑰）。

    .venv\\Scripts\\python.exe scripts/fetch_binance.py                  # ETHUSDT、BTCUSDT
    .venv\\Scripts\\python.exe scripts/fetch_binance.py --symbol ETHUSDT

存成 market='binance_ethusdt' 這類名稱，和 MAX 的資料分開；只存已收盤的 K 線。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from crypto_robot.core.models import Bar  # noqa: E402
from crypto_robot.store.bars import BarStore  # noqa: E402

URL = 'https://api.binance.com/api/v3/klines?symbol={}&interval=1h&startTime={}&limit=1000'
STEP = 3_600_000


def fetch(store: BarStore, symbol: str) -> int:
    market = 'binance_' + symbol.lower()
    last = store.last_ts(market, 60)
    since = 0 if last is None else last + STEP
    total = 0
    while True:
        with urllib.request.urlopen(URL.format(symbol, since), timeout=30) as r:
            rows = json.loads(r.read())
        now = int(time.time() * 1000)
        bars = [Bar(int(k[0]), *(Decimal(x) for x in k[1:6])) for k in rows if int(k[6]) < now]
        if not bars:
            return total
        total += store.upsert(market, 60, bars)
        since = bars[-1].ts + STEP
        time.sleep(0.2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', action='append', help='預設 ETHUSDT、BTCUSDT')
    ap.add_argument('--db', default=str(ROOT / 'data' / 'bars.db'))
    args = ap.parse_args()
    store = BarStore(args.db)
    for s in args.symbol or ['ETHUSDT', 'BTCUSDT']:
        print(s, '新增', fetch(store, s), '根')
    store.close()


if __name__ == '__main__':
    main()
