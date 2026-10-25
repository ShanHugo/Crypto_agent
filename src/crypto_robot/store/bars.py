"""K 線快取（SQLite）。只存已收盤的 K 線；數字以字串保存，讀回時還原成 Decimal 不失真。"""
from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from crypto_robot.core.models import Bar

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    market TEXT NOT NULL,
    period_min INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL, close TEXT NOT NULL, volume TEXT NOT NULL,
    PRIMARY KEY (market, period_min, ts)
)
"""


class BarStore:
    def __init__(self, path: str | Path):
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def upsert(self, market: str, period_min: int, bars: list[Bar]) -> int:
        """寫入（同時間的 K 線以新資料覆蓋），回傳筆數。"""
        rows = [(market, period_min, b.ts, str(b.open), str(b.high), str(b.low), str(b.close), str(b.volume))
                for b in bars]
        with self._db:
            self._db.executemany('INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?)', rows)
        return len(rows)

    def load(self, market: str, period_min: int, start_ms: int | None = None, end_ms: int | None = None) -> list[Bar]:
        """讀 [start_ms, end_ms) 的 K 線，由舊到新。"""
        sql = 'SELECT ts, open, high, low, close, volume FROM bars WHERE market=? AND period_min=?'
        args: list = [market, period_min]
        if start_ms is not None:
            sql += ' AND ts >= ?'
            args.append(start_ms)
        if end_ms is not None:
            sql += ' AND ts < ?'
            args.append(end_ms)
        rows = self._db.execute(sql + ' ORDER BY ts', args).fetchall()
        return [Bar(ts, *(Decimal(x) for x in rest)) for ts, *rest in rows]

    def last_ts(self, market: str, period_min: int) -> int | None:
        row = self._db.execute('SELECT MAX(ts) FROM bars WHERE market=? AND period_min=?',
                               (market, period_min)).fetchone()
        return row[0]

    def first_ts(self, market: str, period_min: int) -> int | None:
        row = self._db.execute('SELECT MIN(ts) FROM bars WHERE market=? AND period_min=?',
                               (market, period_min)).fetchone()
        return row[0]
