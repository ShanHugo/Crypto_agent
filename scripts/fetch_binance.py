"""抓 Binance 現貨 1h K 線到 data/bars.db（第十二輪研究的對照資料；只用公開 API，不需要金鑰）。

    .venv\\Scripts\\python.exe scripts/fetch_binance.py                  # ETHUSDT、BTCUSDT
    .venv\\Scripts\\python.exe scripts/fetch_binance.py --symbol ETHUSDT
    .venv\\Scripts\\python.exe scripts/fetch_binance.py --futures       # ETHUSDT 永續合約 1h＋資金費率（第十四輪）

存成 market='binance_ethusdt' 這類名稱（合約是 'binancef_ethusdt'），和 MAX 的資料分開；只存已收盤的 K 線。
資金費率整份重抓，存在 data/funding_ethusdt.json（[[毫秒, 費率字串], ...]）。
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
FURL = 'https://fapi.binance.com/fapi/v1/klines?symbol={}&interval=1h&startTime={}&limit=1000'
FUNDING = 'https://fapi.binance.com/fapi/v1/fundingRate?symbol={}&startTime={}&limit=1000'
STEP = 3_600_000


def _get(url: str):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def fetch_funding(symbol: str, path: Path) -> int:
    out, since = [], 1_569_888_000_000          # 2019-10-01；startTime=0 會被當成沒給，只回最近的
    while True:
        rows = _get(FUNDING.format(symbol, since))
        out += [[int(x['fundingTime']), x['fundingRate']] for x in rows]
        if len(rows) < 1000:
            break
        since = out[-1][0] + 1
        time.sleep(0.2)
    path.write_text(json.dumps(out), encoding='utf-8')
    return len(out)


def fetch(store: BarStore, symbol: str, futures: bool = False) -> int:
    market = ('binancef_' if futures else 'binance_') + symbol.lower()
    last = store.last_ts(market, 60)
    since = 0 if last is None else last + STEP
    total = 0
    while True:
        rows = _get((FURL if futures else URL).format(symbol, since))
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
    ap.add_argument('--futures', action='store_true', help='ETHUSDT 永續合約 K 線與資金費率')
    ap.add_argument('--db', default=str(ROOT / 'data' / 'bars.db'))
    args = ap.parse_args()
    store = BarStore(args.db)
    if args.futures:
        print('合約 K 線新增', fetch(store, 'ETHUSDT', futures=True), '根')
        print('資金費率', fetch_funding('ETHUSDT', ROOT / 'data' / 'funding_ethusdt.json'), '筆')
        store.close()
        return
    for s in args.symbol or ['ETHUSDT', 'BTCUSDT']:
        print(s, '新增', fetch(store, s), '根')
    store.close()


if __name__ == '__main__':
    main()
