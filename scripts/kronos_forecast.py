"""每天台北 00:00 的 Kronos 多空預測（DESIGN.md 6.7，只記錄、不下單）。排程 kronos_forecast，紀錄在 logs/kronos_forecast.log。

    .venv\\Scripts\\python.exe scripts/kronos_forecast.py            # 抓 Binance 合約 1h K 線與資金費率 → 預測 → 記到 state/forecast/kronos_ls.jsonl
    .venv\\Scripts\\python.exe scripts/kronos_forecast.py --dry-run  # 只印出，不寫

模型在另一個 venv 跑（scripts/kronos_model.py）；只用 Binance 公開 API，不需要金鑰。08:00 之後才補跑就略過（營運者已經醒了）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))

import fetch_binance as fb  # noqa: E402

from crypto_robot.core.timeutil import TAIPEI  # noqa: E402
from crypto_robot.forecast import kronos_ls as kl  # noqa: E402
from crypto_robot.lab.futures import MARKET  # noqa: E402
from crypto_robot.lab.hourly import H, Series  # noqa: E402
from crypto_robot.store.bars import BarStore  # noqa: E402

LOG = ROOT / 'state' / 'forecast' / 'kronos_ls.jsonl'
KRONOS_PY = ROOT.parent / '_vendor' / 'kronos_venv' / 'Scripts' / 'python.exe'


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    now = datetime.now(TAIPEI)
    ts = kl.forecast_ts(int(now.timestamp() * 1000))
    if not args.dry_run and now.hour >= 8:
        print(f'{now:%H:%M} 已過 08:00，今天不補跑')
        return 0
    if int(now.timestamp() * 1000) < ts:
        print('還沒到今天 00:00')
        return 0
    if any(r['ts'] == ts for r in kl.read(LOG)):
        print('今天已經預測過')
        return 0
    store = BarStore(ROOT / 'data' / 'bars.db')
    try:
        for attempt in range(6):                     # 00:00 剛收盤的那根可能晚幾秒才出現
            fb.fetch(store, 'ETHUSDT', futures=True)
            eth = Series.of(store.load(MARKET, 60))
            if (ts - H) in eth.idx:
                break
            time.sleep(30)
    finally:
        store.close()
    fb.fetch_funding('ETHUSDT', ROOT / 'data' / 'funding_ethusdt.json')
    req = kl.model_input(eth, ts)
    if req is None:
        print('🔴 前 512 根 K 線不齊全，今天不預測')
        return 1
    out = subprocess.run([str(KRONOS_PY), str(ROOT / 'scripts' / 'kronos_model.py')], input=json.dumps(req),
                         capture_output=True, text=True, timeout=480)
    if out.returncode != 0:
        print('🔴 Kronos 失敗：' + out.stderr[-500:])
        return 1
    res = json.loads(out.stdout.strip().splitlines()[-1])
    row = kl.forecast_row(ts, req['close'][-1], res['rets'])
    print(json.dumps({**row, 'device': res['device']}, ensure_ascii=False))
    if not args.dry_run:
        kl.append(LOG, row)
    return 0


if __name__ == '__main__':
    sys.exit(main())
