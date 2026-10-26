"""MAX 私有 REST v3：目前**只有查詢**（真單第一階段，DESIGN.md 6.5），刻意沒有下單、取消、提領的方法。

認證（官方文件 2026-10-26 查核）：參數加上 nonce（毫秒）與 path → JSON → Base64 = payload；
HMAC-SHA256(secret, payload) 的 hex = signature；header 帶 X-MAX-ACCESSKEY／X-MAX-PAYLOAD／X-MAX-SIGNATURE。
nonce 要和伺服器時間差 30 秒內且不可重複：用伺服器時間校正本機時鐘，並保證遞增。
金鑰只從呼叫端傳入（讀 .env），不寫進紀錄、不出現在錯誤訊息。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from decimal import Decimal
from urllib.parse import urlencode

import aiohttp

from crypto_robot.core.num import loads, to_decimal
from crypto_robot.exchange.max.public import BASE_URL, MaxApiError


def sign(secret: str, path: str, params: dict) -> tuple[str, str]:
    """回傳 (payload, signature)。params 要已經包含 nonce。"""
    payload = base64.b64encode(json.dumps({**params, 'path': path}).encode()).decode()
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload, signature


class MaxPrivateClient:
    def __init__(self, session: aiohttp.ClientSession, access_key: str, secret_key: str, *, base_url: str = BASE_URL):
        if not access_key or not secret_key:
            raise ValueError('缺少 MAX_API_KEY 或 MAX_API_SECRET')
        self._session = session
        self._key = access_key
        self._secret = secret_key
        self._base = base_url.rstrip('/')
        self._offset_ms = 0          # 伺服器時間 − 本機時間
        self._last_nonce = 0

    def __repr__(self) -> str:       # 避免金鑰出現在除錯輸出
        return 'MaxPrivateClient(<keys hidden>)'

    def sync_clock(self, server_ms: int, local_ms: int | None = None) -> None:
        self._offset_ms = server_ms - (local_ms if local_ms is not None else int(time.time() * 1000))

    def _nonce(self) -> int:
        n = max(int(time.time() * 1000) + self._offset_ms, self._last_nonce + 1)
        self._last_nonce = n
        return n

    async def _get(self, path: str, params: dict | None = None):
        q = {'nonce': self._nonce(), **(params or {})}
        payload, signature = sign(self._secret, path, q)
        headers = {'X-MAX-ACCESSKEY': self._key, 'X-MAX-PAYLOAD': payload, 'X-MAX-SIGNATURE': signature,
                   'Content-Type': 'application/json'}
        async with self._session.get(f'{self._base}{path}?{urlencode(q)}', headers=headers,
                                     timeout=aiohttp.ClientTimeout(total=20)) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise MaxApiError(resp.status, text, path)
            return loads(text)

    async def info(self) -> dict:
        """帳號資訊（含目前手續費等級）。回傳時移除 email 等個資，只留交易相關欄位。"""
        d = await self._get('/api/v3/info')
        vip = d.get('current_vip_level') or {}
        return {'level': d.get('level'), 'vip_level': vip.get('level'),
                'maker_fee': to_decimal(vip['maker_fee']) if vip.get('maker_fee') is not None else None,
                'taker_fee': to_decimal(vip['taker_fee']) if vip.get('taker_fee') is not None else None}

    async def balances(self) -> dict[str, dict[str, Decimal]]:
        """現貨錢包餘額：幣別 → {'balance': 可用, 'locked': 掛單凍結}。只回傳不是 0 的幣別。"""
        rows = await self._get('/api/v3/wallet/spot/accounts')
        out = {}
        for r in rows:
            bal, locked = to_decimal(r.get('balance') or '0'), to_decimal(r.get('locked') or '0')
            if bal or locked:
                out[r['currency']] = {'balance': bal, 'locked': locked}
        return out

    async def open_orders(self, market: str) -> list[dict]:
        return await self._get('/api/v3/wallet/spot/orders/open', {'market': market})


class MaxTradingClient(MaxPrivateClient):
    """會下單的版本（真單第二階段）。只有真單執行器（live/executor.py）使用；唯讀流程一律用 MaxPrivateClient。
    沒有提領方法。POST／DELETE 的參數放 body，簽章內容同樣是參數＋nonce＋path。"""

    def __repr__(self) -> str:
        return 'MaxTradingClient(<keys hidden>)'

    async def _send(self, method: str, path: str, params: dict):
        q = {'nonce': self._nonce(), **params}
        payload, signature = sign(self._secret, path, q)
        headers = {'X-MAX-ACCESSKEY': self._key, 'X-MAX-PAYLOAD': payload, 'X-MAX-SIGNATURE': signature,
                   'Content-Type': 'application/json'}
        async with self._session.request(method, f'{self._base}{path}', data=json.dumps(q), headers=headers,
                                         timeout=aiohttp.ClientTimeout(total=20)) as resp:
            text = await resp.text()
            if resp.status not in (200, 201):
                raise MaxApiError(resp.status, text, path)
            return loads(text)

    async def create_order(self, market: str, side: str, volume: Decimal, *, ord_type: str, client_oid: str,
                           price: Decimal | None = None) -> dict:
        """送出訂單。成功只代表交易所已受理，實際成交要用 order(client_oid) 查。"""
        if side not in ('buy', 'sell') or ord_type not in ('limit', 'ioc_limit', 'market'):
            raise ValueError('不支援的 side 或 ord_type')
        if len(client_oid) > 36:
            raise ValueError('client_oid 最長 36 字元')
        params = {'market': market, 'side': side, 'volume': str(volume), 'ord_type': ord_type, 'client_oid': client_oid}
        if price is not None:
            params['price'] = str(price)
        return await self._send('POST', '/api/v3/wallet/spot/order', params)

    async def order(self, client_oid: str) -> dict:
        return await self._get('/api/v3/order', {'client_oid': client_oid})

    async def cancel(self, client_oid: str) -> dict:
        return await self._send('DELETE', '/api/v3/order', {'client_oid': client_oid})
