"""第二十五輪：抓決策時點的多空比與未平倉量快照（DESIGN.md 6.20）。公開資料，不用金鑰。

    .venv\\Scripts\\python.exe scripts/fetch_xsec_metrics.py     # 已抓過的跳過，可以中斷後再跑

來源 data.binance.vision 的 metrics 每日檔（含已下架的幣）。只抓研究用到的那些天：
選幣池裡每個幣在每個決策時點的前一天；週一的另外抓 7 天前，算未平倉量變化（只報告）。
壓縮檔只在記憶體裡當文字讀，不解壓到硬碟、不執行。結果存 data/xsec/metrics_weekly.csv（可重新下載）。
"""
from __future__ import annotations

import csv
import io
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from crypto_robot.lab import xsec, xsec_positioning as xp  # noqa: E402
from crypto_robot.lab.xsec import D  # noqa: E402

FILES = 'https://data.binance.vision/data/futures/um/daily/metrics/'
FOLDER = ROOT / 'data' / 'xsec'
OUT = FOLDER / 'metrics_weekly.csv'
COLS = ['t', 'sym', 'status', 'create_time', *xp.FIELDS]


def get(url: str) -> bytes | None:
    """沒有這個檔回傳 None；其他錯誤重試，最後丟出去（這筆不寫，下次再抓）。"""
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.parse.quote(url, safe=':/?=&%'), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == 4:
                raise
        except OSError:
            if attempt == 4:
                raise
        time.sleep(2 ** attempt)
    raise AssertionError


def utc_ms(text: str) -> int:
    return int(datetime.strptime(text, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp() * 1000)


def one(key: tuple[int, str]) -> dict:
    t, sym = key
    day = datetime.fromtimestamp((t - D) / 1000, timezone.utc).strftime('%Y-%m-%d')
    raw = get(f'{FILES}{sym}/{sym}-metrics-{day}.zip')
    row = {'t': t, 'sym': sym, 'status': 'no_file', 'create_time': ''}
    if raw is None:
        return row
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        text = z.read(z.namelist()[0]).decode('utf-8')
    rows = [(utc_ms(r['create_time']), r) for r in csv.DictReader(io.StringIO(text))]
    snap = xp.snapshot(rows, t)
    if snap is None:
        return {**row, 'status': 'no_row'}
    return {**row, 'status': 'ok', 'create_time': snap[0], **{k: '' if v is None else v for k, v in snap[1].items()}}


def wanted() -> list[tuple[int, str]]:
    """2023-01-02 起到參考段結束；週一的另外要 7 天前。"""
    keys = set()
    for wd, offset in xsec.OFFSET.items():
        with (FOLDER / f'panel_{wd}.csv').open(encoding='utf-8') as f:
            for r in csv.DictReader(f):
                t = int(r['ts'])
                if xp.DEV[0] <= t < xsec.END:
                    keys.add((t, r['sym']))
                    if not offset:
                        keys.add((t - 7 * D, r['sym']))
    return sorted(keys)


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8')
    keys = wanted()
    done = set()
    if OUT.exists():
        with OUT.open(encoding='utf-8') as f:
            done = {(int(r['t']), r['sym']) for r in csv.DictReader(f)}
    todo = [k for k in keys if k not in done]
    print(f'要 {len(keys)} 筆，已有 {len(done)} 筆，這次抓 {len(todo)} 筆', flush=True)
    new = not OUT.exists()
    failed, t0 = 0, time.time()
    with OUT.open('a', encoding='utf-8', newline='') as f, ThreadPoolExecutor(8) as ex:
        w = csv.DictWriter(f, COLS)
        if new:
            w.writeheader()

        def safe(k):
            try:
                return one(k)
            except Exception as e:  # noqa: BLE001
                return {'error': f'{k}: {type(e).__name__} {e}'}
        for i, r in enumerate(ex.map(safe, todo), 1):
            if 'error' in r:
                failed += 1
                print(r['error'], flush=True)
                continue
            w.writerow(r)
            if i % 1000 == 0:
                f.flush()
                print(f'{i}/{len(todo)}，{time.time() - t0:.0f} 秒，失敗 {failed}', flush=True)
    print(f'完成：{len(todo) - failed} 筆，失敗 {failed} 筆（重跑會補）', flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
