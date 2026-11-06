"""每天存大戶多空比與未平倉量（DESIGN.md 6.19）。只用公開資料，不用金鑰、不下單，資料不接模型、模擬或真單。

    .venv\\Scripts\\python.exe scripts/fetch_positioning.py     # 排程每天 08:15 執行；重跑不會重複存
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from crypto_robot import positioning as pos  # noqa: E402

API = 'https://fapi.binance.com'
DB = ROOT / 'state' / 'positioning' / 'positioning.db'


def fetch(path: str):
    with urllib.request.urlopen(API + path, timeout=30) as r:
        return json.loads(r.read())


def main() -> int:
    now = int(time.time() * 1000)
    try:
        symbols = pos.top_symbols(fetch('/fapi/v1/exchangeInfo'), fetch('/fapi/v1/ticker/24hr'))
    except (OSError, ValueError, KeyError) as e:
        print(f'讀不到合約清單或成交額：{type(e).__name__} {e}；明天重抓就會補回')
        return 1
    store = pos.Store(DB)
    try:
        r = pos.collect(fetch, store, symbols, now)
    finally:
        store.close()
    print(f"{len(symbols)} 個幣：成功 {r['ok']} 次，失敗 {r['failed']} 次，新增 {r['added']} 筆" + (f"；提早結束：{r['stopped']}" if r['stopped'] else ''))
    return 1 if r['stopped'] or not r['ok'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
