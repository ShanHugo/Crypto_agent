"""真單第一階段（唯讀，DESIGN.md 6.5）：讀真實餘額與公開 IP，存成每日紀錄。不下單。

紀錄在 state/live/balances.jsonl（一行一筆，只新增）；白名單 IP 在 state/live/public_ip.json。
這兩個檔案含個人資訊，只在本機，不進 git。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import aiohttp

from crypto_robot.core.timeutil import TAIPEI
from crypto_robot.exchange.max.private import MaxPrivateClient
from crypto_robot.exchange.max.public import MaxApiError

IP_SERVICE = 'https://api.ipify.org'
VALUED = ('twd', 'eth', 'usdt')      # 估值只算這三種（模擬規則用到的）


async def public_ip(session: aiohttp.ClientSession) -> str | None:
    try:
        async with session.get(IP_SERVICE, timeout=aiohttp.ClientTimeout(total=10)) as r:
            return (await r.text()).strip() if r.status == 200 else None
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None


def valuation(balances: dict[str, dict[str, Decimal]], prices: dict[str, Decimal]) -> Decimal:
    """台幣估值：台幣＋各幣（可用＋凍結）× 中間價。沒有價格的幣不計入。"""
    total = Decimal(0)
    for cur, b in balances.items():
        qty = b['balance'] + b['locked']
        if cur == 'twd':
            total += qty
        elif cur in prices:
            total += qty * prices[cur]
    return total


async def read_snapshot(session: aiohttp.ClientSession, key: str, secret: str, server_ms: int,
                        prices: dict[str, Decimal], ref_ip: str | None, source: str = 'scheduled') -> dict:
    """讀一次真實帳戶。失敗不丟例外，記在 ok／error（例如 IP 變了被白名單擋）。"""
    now = datetime.now(TAIPEI)
    ip = await public_ip(session)
    rec = {'date': now.date().isoformat(), 'time': now.strftime('%H:%M'), 'source': source, 'ip': ip,
           'ip_changed': bool(ref_ip and ip and ip != ref_ip), 'ok': False, 'error': None}
    try:
        client = MaxPrivateClient(session, key, secret)
        client.sync_clock(server_ms)
        info = await client.info()
        bal = await client.balances()
    except (MaxApiError, ValueError, aiohttp.ClientError, asyncio.TimeoutError) as e:
        rec['error'] = f'HTTP {e.status}：{e.body[:120]}' if isinstance(e, MaxApiError) else f'{type(e).__name__}: {e}'
        return rec
    rec.update(ok=True, taker_fee=str(info['taker_fee']), maker_fee=str(info['maker_fee']),
               balances={c: {'balance': str(b['balance']), 'locked': str(b['locked'])} for c, b in bal.items() if c in VALUED},
               other=sorted(c for c in bal if c not in VALUED),
               prices={c: str(p) for c, p in prices.items()}, equity=str(valuation(bal, prices)))
    return rec


def summary(rec: dict) -> str:
    """一行摘要（08:30 報告與命令列共用）。不含 IP（推送到 Discord）。"""
    if not rec['ok']:
        return (f'🔴 真實帳戶（唯讀）讀取失敗：{rec["error"]}'
                + ('；公開 IP 和白名單不同，到 MAX 更新白名單後執行 live_check.py --accept-ip' if rec['ip_changed'] else ''))
    b = rec['balances']
    parts = []
    for c in VALUED:
        if c in b:
            q = Decimal(b[c]['balance']) + Decimal(b[c]['locked'])
            parts.append(f'{c.upper()} {q:,.0f}' if c == 'twd' else f'{c.upper()} {q.normalize():f}')
    head = f'💰 真實帳戶（唯讀）：估值 {Decimal(rec["equity"]):,.0f} 元' + (f'（{"、".join(parts)}）' if parts else '（沒有餘額）')
    return head + ('；⚠️ 公開 IP 和白名單不同，到 MAX 更新白名單後執行 live_check.py --accept-ip' if rec['ip_changed'] else '')


def append(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def load(path: Path, n: int = 30) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding='utf-8').splitlines()[-n:] if ln.strip()]


def ref_ip(path: Path) -> str | None:
    return json.loads(path.read_text(encoding='utf-8'))['ip'] if path.exists() else None


def set_ref_ip(path: Path, ip: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'ip': ip, 'set_at': datetime.now(TAIPEI).isoformat(timespec='seconds')}), encoding='utf-8')
