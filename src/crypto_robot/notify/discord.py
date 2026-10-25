"""Discord webhook 推送（DESIGN.md 5.6）：發送失敗不影響交易流程，只寫日誌。"""
from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger(__name__)
MAX_LEN = 1900   # Discord 單則上限 2000 字


async def send(session: aiohttp.ClientSession, webhook_url: str | None, text: str) -> bool:
    if not webhook_url:
        log.info('沒有設定 webhook，不推送')
        return False
    ok = True
    for i in range(0, len(text), MAX_LEN):
        try:
            async with session.post(webhook_url, json={'content': text[i:i + MAX_LEN]},
                                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status >= 300:
                    log.warning('Discord 回應 %s', resp.status)
                    ok = False
        except (aiohttp.ClientError, TimeoutError) as exc:
            log.warning('Discord 推送失敗：%s', type(exc).__name__)   # 不印出網址（等同密碼）
            ok = False
    return ok
