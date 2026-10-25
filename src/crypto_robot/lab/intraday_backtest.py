"""凌晨時段買低賣高回測（5 分 K）。

成交假設（偏保守）：
- 限價單：價格要「穿過」掛價才成交（買：最低價 < 掛價；賣：最高價 > 掛價），只碰到不算。
  開盤就越過掛價時以開盤價成交。每根 K 線最多成交該根成交量的 10%，不足的部分：
  買單在第一次（部分）成交後撤掉剩餘；賣單剩餘部分留到下一根繼續掛。maker 手續費。
- 市價單（停損、07:20 強制平倉）：不限量，價格往不利方向移動 slippage，taker 手續費。
- 同一根 K 線內：先檢查停損，再檢查停利（不知道 K 線內先後順序時，假設對自己不利）；
  進場那根不出場，出場那根不再進場。
- stress=True：限價單也當成市價單計價（價格加滑價、taker 手續費），量的上限不變。

一晚的資料（70 根交易 K 線＋07:20 那根）不完整就跳過那晚，和即時系統「資料有缺就暫停」一致。
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from decimal import Decimal

from crypto_robot.agents.strategy.intraday import (EXIT_AT, MAX_ROUND_TRIPS, STOP_PCT, in_trading_window,
                                                   taipei_date, taipei_hm, trend_ok)
from crypto_robot.core.models import Bar
from crypto_robot.lab.backtest import CostParams, Result, Trade

ZERO = Decimal(0)
ONE = Decimal(1)
MAKER_FEE = Decimal('0.0008')
VOLUME_CAP = Decimal('0.1')
MIN_ORDER_TWD = Decimal(250)
TARGET_NOTIONAL = Decimal(100_000)
BARS_PER_NIGHT = 70
STEP_MS = 5 * 60_000


@dataclass
class IntradayResult:
    result: Result                 # 每根交易 K 線收盤時的權益、每筆完成的交易
    hold: Result                   # 基準：同樣可交易的夜晚，01:30 開盤買、07:20 開盤賣，不計成本
    fills: list[Decimal] = field(default_factory=list)   # 每筆交易的買入金額（台幣，含手續費）
    nights: int = 0                # 有交易資格的夜晚
    skipped: int = 0               # 資料不完整而跳過
    filtered: int = 0              # 不接刀開關擋掉


def group_nights(bars: list[Bar]) -> dict:
    """台北日期 → (交易 K 線索引, 07:20 那根索引)。"""
    nights: dict = {}
    for i, b in enumerate(bars):
        if in_trading_window(b.ts):
            nights.setdefault(taipei_date(b.ts), [[], None])[0].append(i)
        elif taipei_hm(b.ts) == EXIT_AT:
            nights.setdefault(taipei_date(b.ts), [[], None])[1] = i
    return nights


def _complete(bars: list[Bar], idx: list[int], exit_i: int | None) -> bool:
    if exit_i is None or len(idx) != BARS_PER_NIGHT:
        return False
    seq = idx + [exit_i]
    return all(bars[b].ts - bars[a].ts == STEP_MS for a, b in zip(seq, seq[1:]))


def run_nights(bars: list[Bar], daily: list[Bar], start_ms: int, end_ms: int, rule, use_filter: bool,
               cp: CostParams, *, stress: bool = False, initial: Decimal = TARGET_NOTIONAL) -> IntradayResult:
    """bars：5 分 K（要含 start_ms 之前至少 rule.lookback 根）；daily：日 K（不接刀開關用，要含之前 20 天）。"""
    out = IntradayResult(Result(initial), Result(initial))
    res = out.result
    closes = [b.close for b in bars]
    daily_dates = [taipei_date(d.ts) for d in daily]
    daily_closes = [d.close for d in daily]
    equity = initial
    hold_equity = initial
    buy_fee = sell_fee = MAKER_FEE if not stress else cp.taker_fee
    limit_slip = cp.slippage if stress else ZERO

    for night, (idx, exit_i) in sorted(group_nights(bars).items()):
        first_ts = bars[idx[0]].ts if idx else None
        if first_ts is None or not start_ms <= first_ts < end_ms:
            continue
        if not _complete(bars, idx, exit_i) or idx[0] < rule.lookback:
            out.skipped += 1
            continue
        if use_filter and not trend_ok(daily_closes[:bisect.bisect_left(daily_dates, night)]):
            out.filtered += 1
            continue
        out.nights += 1
        hold_equity *= bars[exit_i].open / bars[idx[0]].open
        out.hold.equity.append((bars[exit_i].ts, hold_equity))

        ref = bars[idx[0]].open
        cash, qty = equity, ZERO
        pos: dict | None = None
        round_trips, stopped = 0, False
        entry_level = rule.entry_level(ref, closes[max(0, idx[0] - rule.lookback):idx[0]])
        exit_level = None

        def close_trade(b: Bar, reason: str):
            nonlocal pos, round_trips
            res.trades.append(Trade(pos['ts'], b.ts, pos['price'], pos['proceeds'] / pos['qty'], pos['qty'],
                                    pos['proceeds'] / pos['cost'] - ONE, reason))
            out.fills.append(pos['cost'])
            pos, round_trips = None, round_trips + 1

        def sell(b: Bar, px: Decimal, amount: Decimal, fee_rate: Decimal, slip: Decimal, reason: str):
            nonlocal cash, qty
            fill = px * (ONE - slip)
            gross = amount * fill
            fee = gross * fee_rate
            cash += gross - fee
            qty -= amount
            res.costs += amount * (px - fill) + fee
            pos['proceeds'] += gross - fee
            if qty == 0:
                close_trade(b, reason)

        for i in idx:
            b = bars[i]
            if pos is not None and i > pos['bar']:
                stop = pos['price'] * (ONE - STOP_PCT)
                if b.open <= stop or b.low <= stop:
                    sell(b, min(b.open, stop), qty, cp.taker_fee, cp.slippage, 'stop')
                    stopped = True
                elif exit_level is not None and b.high > exit_level:
                    amount = min(qty, VOLUME_CAP * b.volume)
                    if amount > 0:
                        sell(b, max(exit_level, b.open), amount, sell_fee, limit_slip, 'take')
            elif pos is None and not stopped and round_trips < MAX_ROUND_TRIPS and entry_level is not None \
                    and b.low < entry_level:
                px = min(entry_level, b.open) * (ONE + limit_slip)
                amount = min(min(cash, TARGET_NOTIONAL) / (px * (ONE + buy_fee)), VOLUME_CAP * b.volume)
                if amount * px >= MIN_ORDER_TWD:
                    gross = amount * px
                    fee = gross * buy_fee
                    cash -= gross + fee
                    qty = amount
                    res.costs += amount * (px - min(entry_level, b.open)) + fee
                    pos = {'ts': b.ts, 'bar': i, 'price': px, 'qty': amount, 'cost': gross + fee, 'proceeds': ZERO}
            hist = closes[max(0, i + 1 - rule.lookback):i + 1]
            if pos is not None:
                exit_level = rule.exit_level(pos['price'], hist)
                res.bars_in_position += 1
            else:
                entry_level = rule.entry_level(ref, hist)
            res.equity.append((b.ts, cash + qty * b.close))

        if pos is not None:
            sell(bars[exit_i], bars[exit_i].open, qty, cp.taker_fee, cp.slippage, 'close')
        equity = cash
        res.equity.append((bars[exit_i].ts, equity))
    return out


def avg_fill(out: IntradayResult) -> Decimal:
    return sum(out.fills) / len(out.fills) if out.fills else ZERO
