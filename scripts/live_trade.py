"""真單（第二階段）：預覽今天會下什麼單，或看真單紀錄。真正的下單由每天 01:35 的 paper_rebalance.py 呼叫。

    .venv\\Scripts\\python.exe scripts/live_trade.py --plan      # 用真實餘額＋當下盤口，列出「如果現在執行」會下的單（不下單）
    .venv\\Scripts\\python.exe scripts/live_trade.py --status    # 真單每天的紀錄與訂單

--plan 只用唯讀的 MaxPrivateClient，結構上不可能下單。
開啟真單：.env 設 LIVE_TRADING=1 與 LIVE_CONFIRM=I_UNDERSTAND；階段上限 LIVE_CAPITAL_CAP（預設 9000）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import aiohttp  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

from crypto_robot.core.timeutil import TAIPEI  # noqa: E402
from crypto_robot.exchange.max.private import MaxPrivateClient  # noqa: E402
from crypto_robot.exchange.max.public import MaxPublicClient  # noqa: E402
from crypto_robot.lab.multi_variants import by_name  # noqa: E402
from crypto_robot.live import executor, readonly  # noqa: E402
from crypto_robot.paper.rebalance_account import mid_price  # noqa: E402

LIVE_DIR = ROOT / 'state' / 'live'
LIVE_DB = LIVE_DIR / 'live.db'
RECORDS = LIVE_DIR / 'balances.jsonl'
MARKETS = {'eth': 'ethtwd', 'usdt': 'usdttwd'}
VARIANT = 'rebal3-eth50-cashusdt75-band5'        # 和主模擬帳戶同一套規則


def config(env: dict) -> executor.LiveConfig:
    return executor.LiveConfig(params=by_name(VARIANT).params, markets=MARKETS,
                               capital_cap=Decimal((env.get('LIVE_CAPITAL_CAP') or '9000').strip()))


def enabled(env: dict) -> bool:
    return (env.get('LIVE_TRADING') or '').strip() == '1' and (env.get('LIVE_CONFIRM') or '').strip() == 'I_UNDERSTAND'


async def plan() -> int:
    env = dotenv_values(ROOT / '.env')
    cfg = config(env)
    async with aiohttp.ClientSession() as session:
        pub = MaxPublicClient(session)
        markets_all = await pub.markets()
        markets = {a: markets_all[m] for a, m in MARKETS.items()}
        prices = {a: mid_price(await pub.order_book(m, 20)) for a, m in MARKETS.items()}
        online = await readonly.system_online(session)
        client = MaxPrivateClient(session, (env.get('MAX_API_KEY') or '').strip(), (env.get('MAX_API_SECRET') or '').strip())
        client.sync_clock(await pub.server_time_ms())
        bal = await client.balances()
    get = lambda c, k: bal.get(c, {}).get(k, Decimal(0))        # noqa: E731
    holdings = {a: get(a, 'balance') for a in MARKETS}
    cash, locked = get('twd', 'balance'), get('twd', 'locked') + sum(get(a, 'locked') for a in MARKETS)
    from crypto_robot.agents.strategy.rebalance_multi import weights
    w = weights(holdings, cash, prices)
    eq = cash + sum(holdings[a] * prices[a] for a in MARKETS)
    print(f'真單開關：{"開啟" if enabled(env) else "關閉（只會預覽）"}；階段上限 {cfg.capital_cap:,.0f} 元；MAX 系統狀態 {"online" if online else "不是 online"}')
    print(f'真實權益 {eq:,.0f} 元：' + '／'.join(f'{a.upper()} {w.get(a, 0):.1%}' for a in ('eth', 'usdt', 'twd'))
          + f'（目標 ETH 50%／USDT 37.5%／TWD 12.5%，偏離 {cfg.params.band * 100:.0f} 個百分點觸發）')
    today = datetime.now(TAIPEI).date().isoformat()
    reason = executor.check_guards(holdings, cash, locked, prices, readonly.previous_prices(readonly.load(RECORDS, 30), today), cfg, online)
    if reason:
        print(f'⛔ 今天不會下單：{reason}')
        return 0
    orders, notes, reason = executor.plan_orders(holdings, cash, prices, markets, cfg)
    if reason:
        print(f'⛔ 今天不會下單：{reason}')
        return 0
    if not orders:
        print('不需要調整' + (f'（{"；".join(notes)}）' if notes else ''))
        return 0
    print('如果現在執行，會依序送出（ioc_limit，價格超過上限就不成交）：')
    for o in orders:
        print(f'  {"賣" if o.side == "sell" else "買"} {o.volume} {o.asset.upper()}，{"價格下限" if o.side == "sell" else "價格上限"} {o.price}，'
              f'約 {o.value:,.0f} 元')
    for n in notes:
        print(f'  ⚠️ {n}')
    print('這是預覽，沒有下單。買單會在賣單成交後依實際台幣餘額重算。')
    return 0


def status() -> int:
    if not LIVE_DB.exists():
        print('還沒有真單紀錄')
        return 0
    st = executor.LiveStore(LIVE_DB)
    try:
        for r in st.runs(30):
            print(f'{r["date"]}  {r["status"]:<10} 權益 {r["equity_before"] or "-"} → {r["equity_after"] or "-"}  {r["note"]}')
        print('\n訂單')
        for o in st.orders():
            print(f'{o["date"]}  {o["side"]:<4} {o["volume"]} {o["asset"].upper()} @ {o["price"]}  {o["status"]}  '
                  f'成交 {o["executed"] or 0} 均價 {o["avg_price"] or "-"}  {o["error"] or ""}')
    finally:
        st.close()
    return 0


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--plan', action='store_true', help='預覽今天會下的單（不下單）')
    g.add_argument('--status', action='store_true', help='真單紀錄與訂單')
    args = ap.parse_args()
    sys.exit(asyncio.run(plan()) if args.plan else status())


if __name__ == '__main__':
    main()
