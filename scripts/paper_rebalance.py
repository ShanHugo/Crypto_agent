"""比例再平衡模擬帳戶：每天 01:30～07:30 之間執行一次（排程 01:35），用 MAX 當下盤口模擬成交，不下真單。

    .venv\\Scripts\\python.exe scripts/paper_rebalance.py --dry-run      # 只印出，不寫入、不推送
    .venv\\Scripts\\python.exe scripts/paper_rebalance.py                # 執行今天（同一天只會執行一次）
    .venv\\Scripts\\python.exe scripts/paper_rebalance.py --status       # 看帳戶走勢與交易紀錄

規則：rebal-eth50-band10（ETH 目標 50%，偏離 10 個百分點調回），第三輪研究；初始資金 6,000 台幣（使用者決定）。
狀態在 state/paper_rebalance.db；暫停：建立 state/paper_rebalance.paused 檔案。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import aiohttp  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from crypto_robot.agents.strategy.rebalance import RebalanceParams  # noqa: E402
from crypto_robot.exchange.max.public import MaxPublicClient  # noqa: E402
from crypto_robot.notify.discord import send  # noqa: E402
from crypto_robot.paper.rebalance_account import PaperStore, in_window, run_day  # noqa: E402

MARKET = 'ethtwd'
PARAMS = RebalanceParams(Decimal('0.5'), Decimal('0.10'))
INITIAL = Decimal(6000)
TAKER_FEE = Decimal('0.0016')
DB = ROOT / 'state' / 'paper_rebalance.db'
PAUSE_FILE = ROOT / 'state' / 'paper_rebalance.paused'
ACTION_LABEL = {'init': '建倉', 'buy': '買進（跌深）', 'sell': '賣出（漲多）', 'hold': '不動作', 'too-small': '不動作（差額太小）'}


def money(x: Decimal) -> str:
    return f'{x:,.0f}'


def message(r: dict) -> str:
    p: RebalanceParams = r['params']
    init = r['initial']
    lines = [f'🧪 模擬再平衡 {r["date"]}（不是真單）', f'動作：{ACTION_LABEL[r["action"]]}']
    for side, _, f, _, wa in r['trades']:
        lines.append(f'　{"買" if side == "buy" else "賣"} {f.qty} ETH，均價 {money(f.avg_price)}，'
                     f'手續費 {f.fee:.1f} 元，調整後 ETH 比例 {wa:.1%}')
    lines += [
        f'ETH 比例 {r["weight"]:.1%}（目標 {p.target:.0%}，±{p.band * 100:.0f} 個百分點觸發）；ETH 價格 {money(r["price"])}',
        f'權益 {money(r["equity"])} 元（{r["equity"] / init - 1:+.2%}）'
        f'｜對照：不再平衡 {money(r["static_equity"])}、全部 ETH {money(r["hodl_equity"])}',
    ]
    lines += [f'⚠️ {n}' for n in r['note']]
    return '\n'.join(lines)


def status(store: PaperStore) -> None:
    rows = store.snapshots()
    if not rows:
        print('還沒有任何紀錄')
        return
    print('日期        ETH價格    權益    ETH比例  不再平衡  全部ETH  動作')
    for d, price, eq, w, st, ho, act in rows:
        print(f'{d}  {Decimal(price):>8,.0f}  {Decimal(eq):>7,.0f}  {Decimal(w):>6.1%}  {Decimal(st):>8,.0f}  '
              f'{Decimal(ho):>7,.0f}  {ACTION_LABEL.get(act, act)}')
    print('\n交易紀錄')
    for d, side, reason, qty, px, gross, fee in store.trades():
        print(f'{d}  {side:<4} {reason:<9} {qty} ETH  均價 {Decimal(px):,.1f}  金額 {Decimal(gross):,.0f}  手續費 {Decimal(fee):.2f}')


async def main_async(args) -> int:
    load_dotenv(ROOT / '.env')
    webhook = os.getenv('PAPER_WEBHOOK_URL') or os.getenv('DISCORD_WEBHOOK_URL')
    store = PaperStore(DB)
    try:
        if args.status:
            status(store)
            return 0
        async with aiohttp.ClientSession() as session:
            try:
                if PAUSE_FILE.exists():
                    print('已暫停（state/paper_rebalance.paused 存在），不執行')
                    return 0
                client = MaxPublicClient(session)
                now = await client.server_time_ms()
                if not in_window(now) and not args.ignore_window:
                    print('現在不在 01:30～07:30 交易時段，不執行（測試可加 --ignore-window）')
                    return 0
                market = (await client.markets())[MARKET]
                if not market.active:
                    raise RuntimeError(f'{MARKET} 目前不是 active 狀態')
                book = await client.order_book(MARKET, 50)
                r = run_day(store, book, market, PARAMS, TAKER_FEE, now, INITIAL, write=not args.dry_run)
                if r['status'] == 'already':
                    print(f'{r["date"]} 已經執行過，不重複執行')
                    return 0
                text = message(r)
                print(text)
                if not args.dry_run and not args.no_send:
                    await send(session, webhook, text)
                return 0
            except Exception as exc:
                text = f'🔴 模擬再平衡執行失敗：{type(exc).__name__}: {exc}'
                print(text, file=sys.stderr)
                if not args.dry_run and not args.no_send:
                    await send(session, webhook, text)
                return 1
    finally:
        store.close()


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='只印出，不寫入、不推送')
    ap.add_argument('--no-send', action='store_true', help='寫入但不推送 Discord')
    ap.add_argument('--ignore-window', action='store_true', help='測試用：時段外也執行')
    ap.add_argument('--status', action='store_true', help='看帳戶走勢與交易紀錄')
    sys.exit(asyncio.run(main_async(ap.parse_args())))


if __name__ == '__main__':
    main()
