"""第二十一輪：抓 Binance USDT 永續合約的日 K 與資金費率（DESIGN.md 6.12）。公開資料，不用金鑰。

    .venv\\Scripts\\python.exe scripts/fetch_xsec.py                  # 全部（已抓過的幣跳過，可以中斷後再跑）
    .venv\\Scripts\\python.exe scripts/fetch_xsec.py --symbols SOLUSDT LUNAUSDT

來源 data.binance.vision（含已下架的幣），每個幣存成 data/xsec/coins/<代號>.json：
    {"k": [[開盤毫秒, 開, 收, 成交額, 主動買入額], ...], "f": [[毫秒, 費率], ...]}
壓縮檔只在記憶體裡當文字讀，不解壓到硬碟、不執行。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from crypto_robot.lab.xsec import EXCLUDE, LAST_MONTH  # noqa: E402

OUT = ROOT / 'data' / 'xsec'
BUCKET = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'
FILES = 'https://data.binance.vision/'
INFO = 'https://fapi.binance.com/fapi/v1/exchangeInfo'
KLINES = 'data/futures/um/monthly/klines/{}/1d/'
FUNDING = 'data/futures/um/monthly/fundingRate/{}/'


def get(url: str) -> bytes:
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.parse.quote(url, safe=':/?=&%'), timeout=60) as r:   # 有中文代號的幣
                return r.read()
        except OSError:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError


def listing(prefix: str, delimiter: bool = False) -> list[str]:
    """列出 S3 目錄：delimiter=True 回傳子目錄，否則回傳檔案。"""
    out, marker = [], ''
    while True:
        q = {'prefix': prefix, **({'delimiter': '/'} if delimiter else {}), **({'marker': marker} if marker else {})}
        text = get(BUCKET + '?' + urllib.parse.urlencode(q)).decode('utf-8')
        tag = 'Prefix' if delimiter else 'Key'
        found = re.findall(rf'<{tag}>([^<]+)</{tag}>', text)
        out += [x for x in found if x != prefix]
        if '<IsTruncated>true</IsTruncated>' not in text:
            return out
        m = re.search(r'<NextMarker>([^<]+)', text)
        marker = m.group(1) if m else found[-1]


def symbols() -> tuple[list[str], dict[str, str]]:
    """全部 USDT 合約目錄，扣掉股票、商品、指數、穩定幣；已下架、不在 exchangeInfo 的幣留著。"""
    info = {x['symbol']: x for x in json.loads(get(INFO))['symbols']}
    dirs = [p.rstrip('/').rsplit('/', 1)[1] for p in listing('data/futures/um/monthly/klines/', delimiter=True)]
    keep, dropped = [], {}
    for s in sorted(d for d in dirs if d.endswith('USDT')):
        x = info.get(s)
        if s in EXCLUDE:
            dropped[s] = '名單排除'
        elif x and (x['contractType'] != 'PERPETUAL' or x.get('underlyingType') != 'COIN'):
            dropped[s] = f"{x['contractType']}/{x.get('underlyingType')}"
        else:
            keep.append(s)
    return keep, dropped


def rows(url: str) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(get(url))) as z:
        text = z.read(z.namelist()[0]).decode('utf-8')
    return [r for r in csv.reader(io.StringIO(text)) if r and r[0].isdigit()]        # 早期的檔案沒有標題列


def in_range(key: str) -> bool:
    m = re.search(r'(\d{4}-\d{2})\.zip$', key)
    return bool(m) and m.group(1) <= LAST_MONTH


def fetch_one(sym: str) -> tuple[str, int, int]:
    path = OUT / 'coins' / f'{sym}.json'
    if path.exists():
        return sym, -1, -1
    k, f = {}, {}
    for key in filter(in_range, listing(KLINES.format(sym))):
        for r in rows(FILES + key):
            k[int(r[0])] = [int(r[0]), float(r[1]), float(r[4]), float(r[7]), float(r[10])]
    for key in filter(in_range, listing(FUNDING.format(sym))):
        for r in rows(FILES + key):
            f[int(r[0])] = float(r[2])
    data = {'k': [k[t] for t in sorted(k)], 'f': [[t, f[t]] for t in sorted(f)]}
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data), encoding='utf-8')
    tmp.replace(path)
    return sym, len(k), len(f)


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', nargs='*')
    args = ap.parse_args()
    (OUT / 'coins').mkdir(parents=True, exist_ok=True)
    if args.symbols:
        names = args.symbols
    else:
        names, dropped = symbols()
        (OUT / 'symbols.json').write_text(json.dumps({'keep': names, 'dropped': dropped}, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'{len(names)} 個幣，排除 {len(dropped)} 個', flush=True)
    start, failed = time.time(), []
    with ThreadPoolExecutor(8) as pool:
        futures = {s: pool.submit(fetch_one, s) for s in names}
        for n, (s, fut) in enumerate(futures.items(), 1):
            try:
                _, bars, rates = fut.result()
                if bars == 0:
                    print(f'{s}：沒有 K 線', flush=True)
            except OSError as e:
                failed.append(s)
                print(f'{s} 失敗：{e}', flush=True)
            if n % 50 == 0:
                print(f'{n}/{len(names)}，{time.time() - start:.0f} 秒', flush=True)
    print(f'完成 {len(names) - len(failed)}/{len(names)}，{time.time() - start:.0f} 秒；失敗：{failed}')
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
