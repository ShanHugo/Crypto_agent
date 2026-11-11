"""第二十七輪：抓幣安 USDT 永續合約 15 分 K（DESIGN.md 6.23）。公開資料，不用金鑰。

    .venv\\Scripts\\python.exe scripts/fetch_k15.py        # 已抓過的月份跳過，可以中斷後再跑

先要有 data/xsec/coins（scripts/fetch_xsec.py）：用它的日 K 算每月名單（lab/k15.py 的 universe），
存 data/k15/universe.json；只抓「在名單的月份＋前後各 1 個月」（BTC 全部月份），存 data/k15/raw/<代號>/<年-月>.csv：
    開盤毫秒, 開, 高, 低, 收, 成交金額, 主動買入金額
壓縮檔只在記憶體裡當文字讀，不解壓到硬碟、不執行。
"""
from __future__ import annotations

import csv
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from crypto_robot.lab import k15, xsec  # noqa: E402

OUT = ROOT / 'data' / 'k15'
URL = 'https://data.binance.vision/data/futures/um/monthly/klines/{0}/15m/{0}-15m-{1}.zip'


def needed(uni: dict[str, list[str]]) -> set[tuple[str, str]]:
    keys = k15.months()
    out = {('BTCUSDT', m) for m in keys}
    for i, m in enumerate(keys):
        for s in uni[m]:
            out.update((s, keys[j]) for j in (i - 1, i, i + 1) if 0 <= j < len(keys))
    return out


def fetch_one(sym: str, month: str) -> int:
    path = OUT / 'raw' / sym / f'{month}.csv'
    if path.exists():
        return -1
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.parse.quote(URL.format(sym, month), safe=':/'), timeout=60) as r:   # 有中文代號的幣
                blob = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 0
            if attempt == 4:
                raise
        except OSError:
            if attempt == 4:
                raise
        time.sleep(2 ** attempt)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        text = z.read(z.namelist()[0]).decode('utf-8')
    rows = [[r[0], r[1], r[2], r[3], r[4], r[7], r[10]] for r in csv.reader(io.StringIO(text)) if r and r[0].isdigit()]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8', newline='') as f:
        csv.writer(f).writerows(rows)
    tmp.replace(path)
    return len(rows)


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    coins = xsec.load_coins(ROOT / 'data' / 'xsec' / 'coins')
    if not coins:
        sys.exit('缺 data/xsec/coins，先執行 scripts/fetch_xsec.py')
    OUT.mkdir(parents=True, exist_ok=True)
    uni = k15.universe(coins, k15.months())
    (OUT / 'universe.json').write_text(json.dumps(uni, indent=0), encoding='utf-8')
    jobs = sorted(needed(uni))
    print(f'{len({s for s, _ in jobs})} 個幣、{len(jobs)} 個月檔', flush=True)
    start, failed, missing = time.time(), [], []
    with ThreadPoolExecutor(8) as pool:
        futs = {j: pool.submit(fetch_one, *j) for j in jobs}
        for n, (j, fut) in enumerate(futs.items(), 1):
            try:
                if fut.result() == 0:
                    missing.append('-'.join(j))
            except OSError as e:
                failed.append('-'.join(j))
                print(f'{j} 失敗：{e}', flush=True)
            if n % 200 == 0:
                print(f'{n}/{len(jobs)}，{time.time() - start:.0f} 秒', flush=True)
    (OUT / 'missing.json').write_text(json.dumps(missing), encoding='utf-8')
    print(f'完成，{time.time() - start:.0f} 秒；資料站沒有的月檔 {len(missing)} 個；失敗 {len(failed)} 個：{failed[:10]}')
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
