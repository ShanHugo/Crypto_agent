"""數值工具：價格、數量、金額一律用 Decimal（DESIGN.md 5.1）。"""
from __future__ import annotations

import json
from decimal import ROUND_DOWN, Decimal


def to_decimal(value) -> Decimal:
    """API 回傳值轉 Decimal。拒絕 float：float 進來代表上游已經失真。"""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        raise TypeError(f'不接受 float：{value!r}，請從字串建立 Decimal')
    if isinstance(value, (int, str)):
        return Decimal(value)
    raise TypeError(f'無法轉成 Decimal：{value!r}')


def loads(text: str):
    """解析 JSON，小數直接變 Decimal，過程中不經過 float。"""
    return json.loads(text, parse_float=Decimal)


def amount_to_precision(amount: Decimal, places: int) -> Decimal:
    """數量無條件捨去到交易所精度，避免賣出超過持有量。"""
    return amount.quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN)

