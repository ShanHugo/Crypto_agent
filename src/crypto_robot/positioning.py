"""大戶多空比與未平倉量的資料收集（DESIGN.md 6.19）：只存資料，不接模型、模擬或真單。

Binance 只給最近約 30 天，所以每天抓最近 31 天；已有的不覆蓋，每筆記第一次看到的時間（first_seen）。
主動買賣量的時間是那一天的開頭，當天還不知道答案；之後研究只能用決策當下已經看過的資料。
"""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Callable

from crypto_robot.lab.xsec import EXCLUDE

KINDS = ('topLongShortPositionRatio', 'topLongShortAccountRatio', 'globalLongShortAccountRatio',
         'takerlongshortRatio', 'openInterestHist')
TOP_N = 100
DAYS = 31
STOP_CODES = (418, 429)     # 被限流：馬上停，同一個 IP 還有前瞻模擬要讀報價
MAX_FAILS_IN_ROW = 10       # 連續失敗多半是斷線，不用每個都等到逾時


def top_symbols(info: dict, tickers: list[dict], n: int = TOP_N) -> list[str]:
    """可以交易的 USDT 永續（排除規則同 lab/xsec.py），依 24 小時成交額排前 n 個。"""
    ok = {x['symbol'] for x in info['symbols']
          if x['contractType'] == 'PERPETUAL' and x['status'] == 'TRADING' and x['quoteAsset'] == 'USDT'
          and x.get('underlyingType') == 'COIN' and x['symbol'] not in EXCLUDE}
    rows = [t for t in tickers if t['symbol'] in ok]
    return [t['symbol'] for t in sorted(rows, key=lambda t: (-float(t['quoteVolume']), t['symbol']))[:n]]


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS rows (kind TEXT, symbol TEXT, ts INTEGER, first_seen INTEGER, payload TEXT,'
                        ' PRIMARY KEY (kind, symbol, ts)) WITHOUT ROWID')
        self.db.execute('CREATE TABLE IF NOT EXISTS runs (time INTEGER PRIMARY KEY, coins INTEGER, added INTEGER, failed INTEGER, stopped TEXT)')

    def add(self, kind: str, symbol: str, rows: list[dict], now_ms: int) -> int:
        """已有的不覆蓋（Binance 之後改數字也留第一次看到的）。回傳新增筆數；欄位不對就整批不寫。"""
        items = []
        for r in rows:
            data = {k: v for k, v in r.items() if k not in ('symbol', 'timestamp')}
            items.append((kind, symbol, int(r['timestamp']), now_ms, json.dumps(data, ensure_ascii=False, sort_keys=True)))
        before = self.db.total_changes
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO rows VALUES (?, ?, ?, ?, ?)', items)
        return self.db.total_changes - before

    def record_run(self, now_ms: int, coins: int, added: int, failed: int, stopped: str = '') -> None:
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?, ?)', (now_ms, coins, added, failed, stopped))

    def close(self) -> None:
        self.db.close()


def last_added(path: Path) -> dict | None:
    """最後一次真的存到新資料的執行（給健康檢查）；沒有資料庫或沒存過回傳 None。"""
    if not path.exists():
        return None
    db = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)
    try:
        r = db.execute('SELECT time, added, failed FROM runs WHERE added > 0 ORDER BY time DESC LIMIT 1').fetchone()
    finally:
        db.close()
    return {'time': r[0], 'added': r[1], 'failed': r[2]} if r else None


def collect(fetch: Callable[[str], object], store: Store, symbols: list[str], now_ms: int, pause: float = 0.1) -> dict:
    """逐幣逐種抓。單筆失敗跳過（30 天內再抓就補回）；被限流或連續失敗就提早結束，已抓到的留著。"""
    ok = failed = added = in_row = 0
    stopped = ''
    for sym in symbols:
        for kind in KINDS:
            try:
                rows = fetch(f'/futures/data/{kind}?symbol={urllib.parse.quote(sym)}&period=1d&limit={DAYS}')
                if not isinstance(rows, list):
                    raise ValueError('回傳不是清單')
                added += store.add(kind, sym, rows, now_ms)
                ok += 1
                in_row = 0
            except urllib.error.HTTPError as e:
                if e.code in STOP_CODES:
                    failed += 1
                    stopped = f'HTTP {e.code}'
                    break
                failed += 1
                in_row += 1
                print(f'{sym} {kind}：HTTP {e.code}')
            except (OSError, ValueError, KeyError, TypeError) as e:
                failed += 1
                in_row += 1
                print(f'{sym} {kind}：{type(e).__name__} {e}')
            if in_row >= MAX_FAILS_IN_ROW:
                stopped = f'連續失敗 {in_row} 次'
                break
            time.sleep(pause)
        if stopped:
            break
    store.record_run(now_ms, len(symbols), added, failed, stopped)
    return {'ok': ok, 'failed': failed, 'added': added, 'stopped': stopped}
