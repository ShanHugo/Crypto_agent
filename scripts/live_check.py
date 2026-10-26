"""真單第一階段（唯讀）：手動讀一次真實餘額、手續費等級與公開 IP，並記進 state/live/balances.jsonl。

    .venv\\Scripts\\python.exe scripts/live_check.py                 # 讀一次
    .venv\\Scripts\\python.exe scripts/live_check.py --accept-ip     # 已經到 MAX 更新白名單後，把目前 IP 設成新的白名單 IP

每天 01:35 的排程（paper_rebalance.py）也會自動讀一次。只呼叫查詢 API，不會下單；不印出金鑰。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import aiohttp  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

from crypto_robot.exchange.max.public import MaxPublicClient  # noqa: E402
from crypto_robot.live import readonly  # noqa: E402
from crypto_robot.paper.rebalance_account import mid_price  # noqa: E402

LIVE_DIR = ROOT / 'state' / 'live'
RECORDS = LIVE_DIR / 'balances.jsonl'
IP_FILE = LIVE_DIR / 'public_ip.json'
MARKETS = {'eth': 'ethtwd', 'usdt': 'usdttwd'}


async def main_async(accept_ip: bool) -> int:
    env = dotenv_values(ROOT / '.env')
    async with aiohttp.ClientSession() as session:
        pub = MaxPublicClient(session)
        server_ms = await pub.server_time_ms()
        prices = {a: mid_price(await pub.order_book(m, 5)) for a, m in MARKETS.items()}
        if accept_ip:
            ip = await readonly.public_ip(session)
            if not ip:
                print('查不到公開 IP，沒有更新')
                return 1
            readonly.set_ref_ip(IP_FILE, ip)
            print('已把目前的公開 IP 設成白名單 IP（只存在本機 state/live/public_ip.json）')
        rec = await readonly.read_snapshot(session, (env.get('MAX_API_KEY') or '').strip(), (env.get('MAX_API_SECRET') or '').strip(),
                                           server_ms, prices, readonly.ref_ip(IP_FILE), source='manual')
    readonly.append(RECORDS, rec)
    print(readonly.summary(rec))
    if rec['ok']:
        print(f'手續費：maker {Decimal(rec["maker_fee"]):.4%}、taker {Decimal(rec["taker_fee"]):.4%}')
        if rec['other']:
            print(f'其他幣別（不計入估值）：{", ".join(c.upper() for c in rec["other"])}')
    elif '401' in (rec['error'] or '') or '2001' in (rec['error'] or ''):
        print('→ 金鑰錯誤、IP 不在白名單，或金鑰沒有查詢權限；到 MAX 的 API 金鑰頁面確認')
    print('只做了查詢，沒有下單。')
    return 0 if rec['ok'] else 1


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--accept-ip', action='store_true', help='已更新 MAX 白名單後，把目前的公開 IP 設成白名單 IP')
    sys.exit(asyncio.run(main_async(ap.parse_args().accept_ip)))


if __name__ == '__main__':
    main()
