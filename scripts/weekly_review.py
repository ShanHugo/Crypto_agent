"""每週事實檢討（DESIGN.md 6.6 第 2 關）：只列數字、不給下單建議。

每週一 08:30 報告推送後自動執行（scripts/paper_rebalance.py --report），也可以手動：

    .venv\\Scripts\\python.exe scripts/weekly_review.py --no-send               # 只印出，不推送、不存檔
    .venv\\Scripts\\python.exe scripts/weekly_review.py --date 2026-11-02       # 指定檢討日

結果存在 reports/weekly_YYYYMMDD.md（不進 git），推送 Discord。只讀交易紀錄，不連 MAX。
最後附上幣安資金費率做多的前瞻紀錄（DESIGN 6.7）：抓幣安公開資料、補記到 state/forecast/funding_long.jsonl；--no-send 時不抓、不寫。
再附 Kronos＋人工判斷多空的成績（只讀 state/forecast/kronos_*.jsonl，DESIGN 6.7）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import aiohttp  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from crypto_robot import weekly  # noqa: E402
from crypto_robot.core.timeutil import TAIPEI  # noqa: E402
from crypto_robot.forecast import funding_long, kronos_ls  # noqa: E402
from crypto_robot.futures_agent import paper  # noqa: E402
from crypto_robot.lab import futures  # noqa: E402
from crypto_robot.lab.hourly import Series  # noqa: E402
from crypto_robot.store.bars import BarStore  # noqa: E402
from crypto_robot.live.executor import LiveStore  # noqa: E402
from crypto_robot.notify.discord import send  # noqa: E402
from crypto_robot.paper.multi_account import MultiPaperStore  # noqa: E402

LIVE_DB = ROOT / 'state' / 'live' / 'live.db'
PAPER_DB = ROOT / 'state' / 'paper_main.db'
REPORTS = ROOT / 'reports'
FUNDING_LOG = ROOT / 'state' / 'forecast' / 'funding_long.jsonl'


def report_path(day: date) -> Path:
    return REPORTS / f'weekly_{day:%Y%m%d}.md'


def build(day: date) -> dict:
    runs, orders, hist, trades = [], [], [], []
    if LIVE_DB.exists():
        ls = LiveStore(LIVE_DB)
        try:
            runs, orders = ls.runs(400), ls.orders()
        finally:
            ls.close()
    if PAPER_DB.exists():
        ps = MultiPaperStore(PAPER_DB)
        try:
            hist, trades = ps.history(), ps.trade_rows()
        finally:
            ps.close()
    return weekly.build(day, runs, orders, hist, trades)


def funding_section(update: bool) -> str:
    """幣安資金費率做多的前瞻紀錄（DESIGN 6.7）；抓公開資料、補記交易。失敗只回一行，不影響週報。"""
    try:
        if update:
            subprocess.run([sys.executable, str(ROOT / 'scripts' / 'fetch_binance.py'), '--futures'],
                           check=True, capture_output=True, timeout=600)
        store = BarStore(ROOT / 'data' / 'bars.db')
        try:
            eth = Series.of(store.load(futures.MARKET, 60))
        finally:
            store.close()
        rows = json.loads((ROOT / 'data' / 'funding_ethusdt.json').read_text(encoding='utf-8'))
        fund = futures.Funding([(t, float(r)) for t, r in rows])
        if update:
            logged, pool, added = funding_long.update(eth, fund, FUNDING_LOG)
        else:
            logged, pool = funding_long.read_log(FUNDING_LOG), funding_long.simulate(eth, fund)[1]
            added = 0
        return funding_long.text(funding_long.status(logged, pool), added)
    except Exception as e:   # noqa: BLE001
        return f'幣安資金費率前瞻紀錄失敗：{type(e).__name__}: {e}'


def kronos_section() -> str:
    """Kronos＋人工判斷多空前瞻紀錄（DESIGN 6.7）；資料由 funding_section 先抓好。失敗只回一行。"""
    try:
        store = BarStore(ROOT / 'data' / 'bars.db')
        try:
            eth = Series.of(store.load(futures.MARKET, 60))
        finally:
            store.close()
        rows = json.loads((ROOT / 'data' / 'funding_ethusdt.json').read_text(encoding='utf-8'))
        fund = futures.Funding([(t, float(r)) for t, r in rows])
        d = ROOT / 'state' / 'forecast'
        return kronos_ls.weekly_text(kronos_ls.read(d / 'kronos_ls.jsonl'), kronos_ls.read(d / 'kronos_judge.jsonl'), eth, fund)
    except Exception as e:   # noqa: BLE001
        return f'Kronos＋人工判斷前瞻紀錄失敗：{type(e).__name__}: {e}'


def futures_section() -> str:
    """blend 前瞻模擬（DESIGN 6.18，只模擬）。失敗只回一行。"""
    path = ROOT / 'state' / 'futures' / 'futures.db'
    try:
        if not path.exists():
            return paper.text(paper.summary([], [], [], [], paper.Account('main')))
        led = paper.Ledger(path, readonly=True)
        try:
            return paper.text(paper.summary(led.equity_rows('main'), led.equity_rows('reference'), led.trades('main'), led.events('main'), led.load('main')))
        finally:
            led.close()
    except Exception as e:   # noqa: BLE001
        return f'blend 前瞻模擬失敗：{type(e).__name__}: {e}'


async def run(day: date, no_send: bool) -> int:
    text = weekly.text(build(day)) + '\n\n' + funding_section(update=not no_send) + '\n\n' + kronos_section() + '\n\n' + futures_section()
    print(text)
    if no_send:
        return 0
    REPORTS.mkdir(exist_ok=True)
    report_path(day).write_text(text + '\n', encoding='utf-8')
    load_dotenv(ROOT / '.env')
    async with aiohttp.ClientSession() as session:
        await send(session, os.getenv('PAPER_WEBHOOK_URL') or os.getenv('DISCORD_WEBHOOK_URL'), text)
    return 0


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', type=date.fromisoformat, help='檢討日（預設今天）')
    ap.add_argument('--no-send', action='store_true', help='只印出，不推送、不存檔')
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args.date or datetime.now(TAIPEI).date(), args.no_send)))


if __name__ == '__main__':
    main()
