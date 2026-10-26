"""比例再平衡模擬帳戶：每天 01:30～08:00 之間執行（排程 01:35，失敗每小時重試到 07:35；同一天只成交一次），用 MAX 當下盤口模擬成交，不下真單。
執行時不推送；每天 08:30 由 --report 推送當天凌晨的結果（凌晨沒執行到也會說明）。

    .venv\\Scripts\\python.exe scripts/paper_rebalance.py --dry-run      # 只印出，不寫入
    .venv\\Scripts\\python.exe scripts/paper_rebalance.py                # 執行今天（同一天只會執行一次）
    .venv\\Scripts\\python.exe scripts/paper_rebalance.py --report       # 推送今天凌晨的結果到 Discord
    .venv\\Scripts\\python.exe scripts/paper_rebalance.py --status       # 看帳戶走勢與交易紀錄

規則：rebal-eth50-band10（ETH 目標 50%，偏離 10 個百分點調回）；初始資金 6,000 台幣（使用者決定）。
狀態在 state/paper_rebalance.db；暫停：建立 state/paper_rebalance.paused 檔案。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import aiohttp  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from crypto_robot.agents.strategy.rebalance import RebalanceParams  # noqa: E402
from crypto_robot.health import attempts_text, parse_runs  # noqa: E402
from crypto_robot.core.timeutil import TAIPEI, taipei_date  # noqa: E402
from crypto_robot.exchange.max.public import MaxPublicClient  # noqa: E402
from crypto_robot.notify.discord import send  # noqa: E402
from crypto_robot.lab.multi_variants import SHADOW, by_name  # noqa: E402
from crypto_robot.paper import multi_account  # noqa: E402
from crypto_robot.paper.rebalance_account import PaperStore, day_report, in_window, run_day  # noqa: E402

MARKET = 'ethtwd'
PARAMS = RebalanceParams(Decimal('0.5'), Decimal('0.10'))
INITIAL = Decimal(6000)
TAKER_FEE = Decimal('0.0016')
DB = ROOT / 'state' / 'paper_rebalance.db'
PAUSE_FILE = ROOT / 'state' / 'paper_rebalance.paused'
LOG_FILE = ROOT / 'logs' / 'paper_rebalance.log'
# 影子帳戶：和主帳戶同一次執行、同樣資金，只記錄、不影響主帳戶（第五輪研究，DESIGN.md 6.3）
SHADOW_DB = ROOT / 'state' / 'shadow_rebal3.db'
SHADOW_LABEL = '三資產（ETH 50%／USDT 37.5%／台幣 12.5%）'
SHADOW_MARKETS = {'eth': 'ethtwd', 'usdt': 'usdttwd'}
ACTION_LABEL = {'init': '建倉', 'buy': '買進（跌深）', 'sell': '賣出（漲多）', 'hold': '不動作', 'too-small': '不動作（差額太小）'}


def money(x: Decimal) -> str:
    return f'{x:,.0f}'


def message(r: dict) -> str:
    p: RebalanceParams = r['params']
    init = r['initial']
    lines = [f'🧪 模擬再平衡 {r["date"]} 凌晨結果（不是真單）', f'動作：{ACTION_LABEL[r["action"]]}']
    for t in r['trades']:
        lines.append(f'　{"買" if t["side"] == "buy" else "賣"} {t["qty"]} ETH，均價 {money(t["avg_price"])}，'
                     f'手續費 {t["fee"]:.1f} 元，調整後 ETH 比例 {t["weight_after"]:.1%}')
    lines += [
        f'ETH 比例 {r["weight"]:.1%}（目標 {p.target:.0%}，±{p.band * 100:.0f} 個百分點觸發）；ETH 價格 {money(r["price"])}',
        f'權益 {money(r["equity"])} 元（{r["equity"] / init - 1:+.2%}）'
        f'｜對照：不再平衡 {money(r["static_equity"])}、全部 ETH {money(r["hodl_equity"])}',
    ]
    lines += [f'⚠️ {n}' for n in r['note']]
    return '\n'.join(lines)


def last_log_lines(n: int = 6) -> list[str]:
    """排程紀錄最後幾行，給「凌晨沒執行到」時判斷原因；網址一律遮掉。"""
    if not LOG_FILE.exists():
        return ['（找不到 logs/paper_rebalance.log：排程可能沒有執行）']
    lines = LOG_FILE.read_text(encoding='utf-8', errors='replace').splitlines()[-n:]
    return [re.sub(r'https?://\S+', '<網址>', ln) for ln in lines]


def shadow_line(r: dict | None, main_since: Decimal | None = None) -> str:
    """08:30 報告裡影子帳戶的一行。main_since：主帳戶從影子帳戶開始那天起的報酬（兩邊起始日不同，用同期比較）。"""
    if r is None:
        return f'👥 影子・{SHADOW_LABEL}：今天沒有紀錄'
    w = r['weights']
    since = r['equity'] / r['initial'] - 1
    diff = '' if main_since is None else f'，同期兩資產 {main_since:+.2%}'
    act = {'init': '建倉', 'rebalance': '再平衡', 'hold': '不動作', 'too-small': '不動作（差額太小）'}[r['action']]
    return (f'👥 影子・{SHADOW_LABEL}：{act}；權益 {money(r["equity"])}（{since:+.2%}{diff}）；'
            f'ETH {w["eth"]:.0%}／USDT {w["usdt"]:.0%}／台幣 {w["twd"]:.0%}')


def main_since(store: PaperStore, start_day, equity_now: Decimal) -> Decimal | None:
    """主帳戶從 start_day（影子帳戶開始日）起的報酬：以那天（或之後第一天）的權益為基準。"""
    base = next((Decimal(h['equity']) for h in store.history() if h['date'] >= start_day.isoformat()), None)
    return None if base is None else equity_now / base - 1


def shadow_start():
    """影子帳戶的開始日（台北日期）；還沒建立就回傳 None。"""
    if not SHADOW_DB.exists():
        return None
    st = multi_account.MultiPaperStore(SHADOW_DB)
    try:
        acct = st.account()
    finally:
        st.close()
    return None if acct is None else taipei_date(acct['started_at'])


def shadow_report(day) -> dict | None:
    if not SHADOW_DB.exists():
        return None
    st = multi_account.MultiPaperStore(SHADOW_DB)
    try:
        return multi_account.day_report(st, day)
    finally:
        st.close()


def morning_text(store: PaperStore) -> str:
    today = datetime.now(TAIPEI).date()
    r = day_report(store, today)
    log = LOG_FILE.read_text(encoding='utf-8', errors='replace').splitlines() if LOG_FILE.exists() else []
    retries = attempts_text(parse_runs(log), today)
    if r is not None:
        sh = shadow_report(today)
        since = None if sh is None else main_since(store, shadow_start(), r['equity'])
        return (message(r) + (f'\n🟡 重試後才成功：{retries}' if retries else '')
                + '\n' + shadow_line(sh, since))
    if PAUSE_FILE.exists():
        return f'⏸️ 模擬再平衡 {today}：已暫停（state/paper_rebalance.paused），凌晨沒有執行'
    head = f'🔴 模擬再平衡 {today}：01:30～08:00 都沒有成功' + (f'（{retries}）' if retries else '（沒有執行紀錄）')
    return '\n'.join([head, '排程紀錄最後幾行：'] + [f'　{ln}' for ln in last_log_lines()])


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


async def run(store: PaperStore, dry_run: bool, ignore_window: bool) -> int:
    if PAUSE_FILE.exists():
        print('已暫停（state/paper_rebalance.paused 存在），不執行')
        return 0
    async with aiohttp.ClientSession() as session:
        client = MaxPublicClient(session)
        now = await client.server_time_ms()
        if not in_window(now) and not ignore_window:
            print('現在不在 01:30～08:00 交易時段，不執行（測試可加 --ignore-window）')
            return 0
        markets = await client.markets()
        needed = {MARKET, *SHADOW_MARKETS.values()}
        for m in needed:
            if not markets[m].active:
                raise RuntimeError(f'{m} 目前不是 active 狀態')
        books = {m: await client.order_book(m, 50) for m in needed}
    r = run_day(store, books[MARKET], markets[MARKET], PARAMS, TAKER_FEE, now, INITIAL, write=not dry_run)
    if r['status'] == 'already':
        print(f'{r["date"]} 已經執行過，不重複執行')
    else:
        print(message(r))
    # 影子帳戶失敗不影響主帳戶；回傳 1 讓下一小時的重試再做一次（已完成的帳戶會略過）
    try:
        shadow_store = multi_account.MultiPaperStore(SHADOW_DB)
        try:
            sr = multi_account.run_day(shadow_store, {a: books[m] for a, m in SHADOW_MARKETS.items()},
                                       {a: markets[m] for a, m in SHADOW_MARKETS.items()}, by_name(SHADOW).params,
                                       TAKER_FEE, now, INITIAL, write=not dry_run)
        finally:
            shadow_store.close()
    except Exception as exc:       # noqa: BLE001
        print(f'⚠️ 影子帳戶執行失敗：{type(exc).__name__}: {exc}')
        return 1
    print('影子帳戶今天已經執行過' if sr['status'] == 'already' else shadow_line(sr))
    return 0


async def report(store: PaperStore, no_send: bool) -> int:
    load_dotenv(ROOT / '.env')
    text = morning_text(store)
    print(text)
    if not no_send:
        async with aiohttp.ClientSession() as session:
            await send(session, os.getenv('PAPER_WEBHOOK_URL') or os.getenv('DISCORD_WEBHOOK_URL'), text)
    return 0


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='只印出，不寫入')
    ap.add_argument('--ignore-window', action='store_true', help='測試用：時段外也執行')
    ap.add_argument('--report', action='store_true', help='推送今天凌晨的結果（排程 08:30）')
    ap.add_argument('--no-send', action='store_true', help='搭配 --report：只印出，不推送')
    ap.add_argument('--status', action='store_true', help='看帳戶走勢與交易紀錄')
    args = ap.parse_args()
    store = PaperStore(DB)
    try:
        if args.status:
            status(store)
            code = 0
        elif args.report:
            code = asyncio.run(report(store, args.no_send))
        else:
            code = asyncio.run(run(store, args.dry_run, args.ignore_window))
    finally:
        store.close()
    sys.exit(code)


if __name__ == '__main__':
    main()
