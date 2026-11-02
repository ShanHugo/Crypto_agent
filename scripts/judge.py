"""記下營運者今天的多空判斷（DESIGN.md 6.7 Kronos＋人工判斷前瞻紀錄，只記錄、不下單）。

    .venv\\Scripts\\python.exe scripts/judge.py long  "理由（可省略）"     # 做多
    .venv\\Scripts\\python.exe scripts/judge.py short                      # 做空
    .venv\\Scripts\\python.exe scripts/judge.py flat                       # 今天不做
    .venv\\Scripts\\python.exe scripts/judge.py --status                   # 最近幾筆

每天 02:00 那筆預測只能記一次、記了不能改；從記錄當下的 Binance 合約價格起算，到隔天 02:00 結算。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from crypto_robot.core.timeutil import TAIPEI  # noqa: E402
from crypto_robot.forecast import kronos_ls as kl  # noqa: E402

FORECASTS = ROOT / 'state' / 'forecast' / 'kronos_ls.jsonl'
JUDGMENTS = ROOT / 'state' / 'forecast' / 'kronos_judge.jsonl'
PRICE = 'https://fapi.binance.com/fapi/v1/ticker/price?symbol=ETHUSDT'
WORD = {1: '做多', -1: '做空', 0: '不做'}


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('call', nargs='?', choices=list(kl.CALLS))
    ap.add_argument('reason', nargs='?', default='')
    ap.add_argument('--status', action='store_true')
    args = ap.parse_args()
    forecasts, judgments = kl.read(FORECASTS), kl.read(JUDGMENTS)
    if args.status or not args.call:
        for j in judgments[-10:]:
            print(f'{datetime.fromtimestamp(j["at"] / 1000, TAIPEI):%m-%d %H:%M}  你 {WORD[j["call"]]}、Kronos {WORD[j["kronos"]]}'
                  f'  @ {j["price"]:,.2f}  {j["reason"]}')
        return 0
    now = int(time.time() * 1000)
    f, why = kl.open_forecast(forecasts, judgments, now)
    if f is None:
        print(why)
        return 1
    with urllib.request.urlopen(PRICE, timeout=15) as r:
        price = float(json.loads(r.read())['price'])
    kl.append(JUDGMENTS, kl.judgment_row(f, args.call, price, now, args.reason))
    print(f'已記錄：你 {WORD[kl.CALLS[args.call]]}、Kronos {WORD[f["dir"]]}，ETHUSDT 合約 {price:,.2f}，隔天 02:00 結算')
    return 0


if __name__ == '__main__':
    sys.exit(main())
