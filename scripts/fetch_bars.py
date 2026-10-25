"""補 MAX K 線到 data/bars.db，並印出資料品質與每年流動性摘要。

    .venv\\Scripts\\python.exe scripts/fetch_bars.py                 # ethtwd 1h，從 2018 年起
    .venv\\Scripts\\python.exe scripts/fetch_bars.py --market btctwd --since 2020-01-01
    .venv\\Scripts\\python.exe scripts/fetch_bars.py --no-fetch      # 只看快取
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import aiohttp  # noqa: E402

from crypto_robot.agents.data.quality import check_bars  # noqa: E402
from crypto_robot.agents.data.sync import sync_bars  # noqa: E402
from crypto_robot.exchange.max.public import MaxPublicClient  # noqa: E402
from crypto_robot.store.bars import BarStore  # noqa: E402


def utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M')


def yearly_profile(bars) -> list[tuple]:
    """每年：K 線數、成交量 0 的比例、價格完全沒動的比例、每小時成交額中位數（計價幣）。"""
    by_year = defaultdict(list)
    for b in bars:
        by_year[datetime.fromtimestamp(b.ts / 1000, timezone.utc).year].append(b)
    rows = []
    for year, bs in sorted(by_year.items()):
        zero = sum(1 for b in bs if b.volume == 0)
        flat = sum(1 for b in bs if b.high == b.low)
        turnover = statistics.median(b.volume * b.close for b in bs)
        rows.append((year, len(bs), zero / len(bs), flat / len(bs), turnover))
    return rows


async def fetch(store: BarStore, market: str, period: int, since_ms: int) -> int:
    async with aiohttp.ClientSession() as session:
        return await sync_bars(MaxPublicClient(session), store, market, period, since_ms)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--market', default='ethtwd')
    ap.add_argument('--period', type=int, default=60, help='分鐘')
    ap.add_argument('--since', default='2018-01-01', help='快取是空的時候從哪天開始（UTC）')
    ap.add_argument('--db', default=str(ROOT / 'data' / 'bars.db'))
    ap.add_argument('--no-fetch', action='store_true')
    args = ap.parse_args()

    store = BarStore(args.db)
    if not args.no_fetch:
        since_ms = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp() * 1000)
        n = asyncio.run(fetch(store, args.market, args.period, since_ms))
        print(f'新寫入 {n} 根')

    bars = store.load(args.market, args.period)
    if not bars:
        print('快取沒有資料')
        return
    report = check_bars(bars, args.period * 60_000)
    print(f'{args.market} {args.period}m：{len(bars)} 根，{utc(bars[0].ts)} ～ {utc(bars[-1].ts)} UTC')
    kinds = sorted({i.kind for i in report.issues})
    print('品質：' + ('、'.join(f'{k} {report.count(k)}' for k in kinds) if kinds else '沒有問題'))
    for i in report.blocking[:10]:
        print(f'  {i.kind:<12} {utc(i.ts)}  {i.detail}')
    if len(report.blocking) > 10:
        print(f'  …另有 {len(report.blocking) - 10} 筆')
    print('\n年份  K線數  成交量0  價格不動  每小時成交額中位數')
    for year, n, zero, flat, turnover in yearly_profile(bars):
        print(f'{year}  {n:>5}  {zero:>7.1%}  {flat:>7.1%}  {turnover:>16,.0f}')
    store.close()


if __name__ == '__main__':
    main()
