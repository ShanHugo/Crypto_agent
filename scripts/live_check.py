"""真單第一階段（唯讀）：確認 .env 的 MAX 金鑰可以用，列出真實餘額與手續費等級，記錄目前的公開 IP。

    .venv\\Scripts\\python.exe scripts/live_check.py

只呼叫查詢 API，不會下單；不印出金鑰。公開 IP 和上次不同時會提醒更新 MAX 的 IP 白名單。
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import aiohttp  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

from crypto_robot.core.timeutil import TAIPEI  # noqa: E402
from crypto_robot.exchange.max.private import MaxPrivateClient  # noqa: E402
from crypto_robot.exchange.max.public import MaxApiError, MaxPublicClient  # noqa: E402

IP_FILE = ROOT / 'state' / 'live' / 'public_ip.json'
IP_SERVICE = 'https://api.ipify.org'


async def public_ip(session: aiohttp.ClientSession) -> str | None:
    try:
        async with session.get(IP_SERVICE, timeout=aiohttp.ClientTimeout(total=10)) as r:
            return (await r.text()).strip() if r.status == 200 else None
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None


async def main_async() -> int:
    env = dotenv_values(ROOT / '.env')
    async with aiohttp.ClientSession() as session:
        ip = await public_ip(session)
        prev = json.loads(IP_FILE.read_text(encoding='utf-8')) if IP_FILE.exists() else None
        print(f'公開 IP：{ip or "查不到"}' + (f'（上次 {prev["ip"]}，⚠️ 變了，MAX 白名單要更新）'
                                          if prev and ip and prev['ip'] != ip else ''))
        if ip:
            IP_FILE.parent.mkdir(parents=True, exist_ok=True)
            IP_FILE.write_text(json.dumps({'ip': ip, 'checked_at': datetime.now(TAIPEI).isoformat(timespec='seconds')}),
                               encoding='utf-8')
        client = MaxPrivateClient(session, (env.get('MAX_API_KEY') or '').strip(), (env.get('MAX_API_SECRET') or '').strip())
        client.sync_clock(await MaxPublicClient(session).server_time_ms())
        try:
            info = await client.info()
            bal = await client.balances()
        except MaxApiError as e:
            print(f'🔴 私有 API 失敗：HTTP {e.status}，{e.body[:200]}')
            print('→ 401／2001：金鑰錯誤、IP 不在白名單，或金鑰沒有查詢權限；請到 MAX 的 API 金鑰頁面確認')
            return 1
    print(f'帳號等級 {info["level"]}；手續費等級 VIP {info["vip_level"]}：maker {info["maker_fee"]:.4%}、taker {info["taker_fee"]:.4%}')
    if not bal:
        print('現貨錢包目前沒有任何餘額')
    for cur, b in sorted(bal.items()):
        print(f'{cur.upper():>6}：可用 {b["balance"]}，凍結 {b["locked"]}')
    print('只做了查詢，沒有下單。')
    return 0


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(asyncio.run(main_async()))


if __name__ == '__main__':
    main()
