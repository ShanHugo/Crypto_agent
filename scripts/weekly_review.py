"""每週事實檢討（DESIGN.md 6.6 第 2 關）：只列數字、不給下單建議。

每週一 08:30 報告推送後自動執行（scripts/paper_rebalance.py --report），也可以手動：

    .venv\\Scripts\\python.exe scripts/weekly_review.py --no-send               # 只印出，不推送、不存檔
    .venv\\Scripts\\python.exe scripts/weekly_review.py --date 2026-11-02       # 指定檢討日

結果存在 reports/weekly_YYYYMMDD.md（不進 git），推送 Discord。只讀交易紀錄，不連交易所。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import aiohttp  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from crypto_robot import weekly  # noqa: E402
from crypto_robot.core.timeutil import TAIPEI  # noqa: E402
from crypto_robot.live.executor import LiveStore  # noqa: E402
from crypto_robot.notify.discord import send  # noqa: E402
from crypto_robot.paper.multi_account import MultiPaperStore  # noqa: E402

LIVE_DB = ROOT / 'state' / 'live' / 'live.db'
PAPER_DB = ROOT / 'state' / 'paper_main.db'
REPORTS = ROOT / 'reports'


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


async def run(day: date, no_send: bool) -> int:
    text = weekly.text(build(day))
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
