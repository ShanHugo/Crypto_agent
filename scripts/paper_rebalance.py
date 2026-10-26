"""比例再平衡模擬帳戶（主帳戶＋影子帳戶）：每天 01:30～08:00 之間執行（排程 01:35，失敗每小時重試到 07:35；
同一天只成交一次），用 MAX 當下盤口模擬成交，不下真單。執行時不推送；每天 08:30 由 --report 推送當天凌晨的結果。

    .venv\\Scripts\\python.exe scripts/paper_rebalance.py --dry-run      # 只印出，不寫入
    .venv\\Scripts\\python.exe scripts/paper_rebalance.py                # 執行今天（同一天只會執行一次）
    .venv\\Scripts\\python.exe scripts/paper_rebalance.py --report       # 推送今天凌晨的結果到 Discord
    .venv\\Scripts\\python.exe scripts/paper_rebalance.py --status       # 看帳戶走勢與交易紀錄
    .venv\\Scripts\\python.exe scripts/paper_rebalance.py --migrate      # 一次性：舊兩資產帳戶轉成三資產主帳戶

2026-10-26 使用者決定：
- 主帳戶：rebal3-eth50-cashusdt75-band5（ETH 50%、USDT 37.5%、台幣 12.5%，任何一項偏離 5 個百分點全部調回），
  由原本的兩資產帳戶（6,000 元，2026-10-26 建倉）直接轉過來。
- 影子帳戶：原本的兩資產規則 rebal-eth50-band10（ETH 50%、台幣 50%），同樣 6,000 元重新開始，只記錄、拿來比較。
暫停：建立 state/paper_rebalance.paused 檔案（兩個帳戶都停）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
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

from crypto_robot.agents.strategy.rebalance_multi import MultiParams  # noqa: E402
from crypto_robot.core.timeutil import TAIPEI, taipei_date  # noqa: E402
from crypto_robot.exchange.max.public import MaxPublicClient  # noqa: E402
from crypto_robot.health import attempts_text, parse_runs  # noqa: E402
from crypto_robot.lab.multi_variants import by_name  # noqa: E402
from crypto_robot.notify.discord import send  # noqa: E402
from crypto_robot.paper import multi_account  # noqa: E402
from crypto_robot.paper.rebalance_account import in_window  # noqa: E402

INITIAL = Decimal(6000)
TAKER_FEE = Decimal('0.0016')
MARKETS = {'eth': 'ethtwd', 'usdt': 'usdttwd'}
MAIN = {'key': 'main', 'label': '三資產 band5（ETH 50%／USDT 37.5%／台幣 12.5%）',
        'variant': 'rebal3-eth50-cashusdt75-band5', 'params': by_name('rebal3-eth50-cashusdt75-band5').params,
        'db': ROOT / 'state' / 'paper_main.db'}
SHADOW = {'key': 'shadow', 'label': '兩資產 band10（ETH 50%／台幣 50%）',
          'variant': 'rebal-eth50-band10', 'params': MultiParams((('eth', Decimal('0.5')),), Decimal('0.10')),
          'db': ROOT / 'state' / 'shadow_rebal2.db'}
OLD_DB = ROOT / 'state' / 'paper_rebalance.db'      # 2026-10-26 以前的兩資產主帳戶，轉換後只保留當紀錄
PAUSE_FILE = ROOT / 'state' / 'paper_rebalance.paused'
LOG_FILE = ROOT / 'logs' / 'paper_rebalance.log'
ACTION_LABEL = {'init': '建倉', 'rebalance': '再平衡', 'hold': '不動作', 'too-small': '不動作（差額太小）'}
ASSET_LABEL = {'eth': 'ETH', 'usdt': 'USDT', 'twd': '台幣'}


def money(x: Decimal) -> str:
    return f'{x:,.0f}'


def weights_text(r: dict) -> str:
    targets = dict(r['params'].targets) | {'twd': r['params'].cash_target}
    return '／'.join(f'{ASSET_LABEL[a]} {r["weights"].get(a, Decimal(0)):.1%}（目標 {t:.1%}）' for a, t in targets.items())


def message(r: dict, label: str) -> str:
    lines = [f'🧪 模擬再平衡 {r["date"]} 凌晨結果（不是真單）・{label}', f'動作：{ACTION_LABEL[r["action"]]}']
    for t in r['trades']:
        lines.append(f'　{"買" if t["side"] == "buy" else "賣"} {t["qty"]} {ASSET_LABEL[t["asset"]]}，'
                     f'均價 {t["avg_price"]:,.{3 if t["asset"] == "usdt" else 0}f}，手續費 {t["fee"]:.1f} 元')
    p = r['prices']
    lines += [f'比例：{weights_text(r)}；偏離 {r["params"].band * 100:.0f} 個百分點觸發',
              f'價格：ETH {money(p["eth"])}' + (f'、USDT/TWD {p["usdt"]:.3f}' if 'usdt' in p else ''),
              f'權益 {money(r["equity"])} 元（{r["equity"] / r["initial"] - 1:+.2%}）']
    lines += [f'⚠️ {n}' for n in r['note']]
    return '\n'.join(lines)


def open_store(acc: dict) -> multi_account.MultiPaperStore | None:
    return multi_account.MultiPaperStore(acc['db']) if acc['db'].exists() else None


def report_of(acc: dict, day) -> tuple[dict | None, object]:
    """(那天的結果, 帳戶開始日)。"""
    st = open_store(acc)
    if st is None:
        return None, None
    try:
        a = st.account()
        return multi_account.day_report(st, day), (taipei_date(a['started_at']) if a else None)
    finally:
        st.close()


def since(acc: dict, start_day, equity_now: Decimal) -> Decimal | None:
    """帳戶從 start_day 起的報酬：以那天（或之後第一天）的權益為基準。兩個帳戶開始日不同，用同期比較。"""
    st = open_store(acc)
    if st is None:
        return None
    try:
        base = next((Decimal(h['equity']) for h in st.history() if h['date'] >= start_day.isoformat()), None)
    finally:
        st.close()
    return None if base is None else equity_now / base - 1


def shadow_line(sr: dict | None, main_since: Decimal | None) -> str:
    if sr is None:
        return f'👥 影子・{SHADOW["label"]}：今天沒有紀錄'
    s = sr['equity'] / sr['initial'] - 1
    diff = '' if main_since is None else f'，同期主帳戶 {main_since:+.2%}'
    return (f'👥 影子・{SHADOW["label"]}：{ACTION_LABEL[sr["action"]]}；權益 {money(sr["equity"])}（{s:+.2%}{diff}）；'
            f'ETH {sr["weights"]["eth"]:.0%}')


def last_log_lines(n: int = 6) -> list[str]:
    """排程紀錄最後幾行，給「凌晨沒執行到」時判斷原因；網址一律遮掉。"""
    if not LOG_FILE.exists():
        return ['（找不到 logs/paper_rebalance.log：排程可能沒有執行）']
    lines = LOG_FILE.read_text(encoding='utf-8', errors='replace').splitlines()[-n:]
    return [re.sub(r'https?://\S+', '<網址>', ln) for ln in lines]


def morning_text() -> str:
    today = datetime.now(TAIPEI).date()
    r, _ = report_of(MAIN, today)
    log = LOG_FILE.read_text(encoding='utf-8', errors='replace').splitlines() if LOG_FILE.exists() else []
    retries = attempts_text(parse_runs(log), today)
    if r is not None:
        sr, sh_start = report_of(SHADOW, today)
        main_since = since(MAIN, sh_start, r['equity']) if sr is not None and sh_start else None
        return (message(r, MAIN['label']) + (f'\n🟡 重試後才成功：{retries}' if retries else '')
                + '\n' + shadow_line(sr, main_since))
    if PAUSE_FILE.exists():
        return f'⏸️ 模擬再平衡 {today}：已暫停（state/paper_rebalance.paused），凌晨沒有執行'
    head = f'🔴 模擬再平衡 {today}：01:30～08:00 都沒有成功' + (f'（{retries}）' if retries else '（沒有執行紀錄）')
    return '\n'.join([head, '排程紀錄最後幾行：'] + [f'　{ln}' for ln in last_log_lines()])


def status() -> None:
    for acc in (MAIN, SHADOW):
        print(f'== {acc["label"]}（{acc["variant"]}）==')
        st = open_store(acc)
        if st is None or st.account() is None:
            print('還沒有任何紀錄\n')
            continue
        try:
            for h in st.history():
                w = {k: Decimal(v) for k, v in json.loads(h['weights']).items()}
                print(f'{h["date"]}  權益 {Decimal(h["equity"]):>7,.0f}  '
                      + '／'.join(f'{ASSET_LABEL[a]} {w.get(a, 0):.1%}' for a in ('eth', 'usdt', 'twd') if a in w)
                      + f'  {ACTION_LABEL.get(h["action"], h["action"])}')
            print('交易紀錄')
            for t in st.trade_rows():
                print(f'{t["date"]}  {t["side"]:<4} {t["reason"]:<9} {t["qty"]} {ASSET_LABEL[t["asset"]]}  均價 {Decimal(t["avg_price"]):,.3f}'
                      f'  金額 {Decimal(t["gross"]):,.0f}  手續費 {Decimal(t["fee"]):.2f}')
        finally:
            st.close()
        print()


def migrate() -> int:
    """一次性：舊兩資產主帳戶（state/paper_rebalance.db）轉成三資產主帳戶（state/paper_main.db）。"""
    from crypto_robot.paper.rebalance_account import PaperStore
    st = open_store(MAIN)
    if st is not None:
        done = st.account() is not None
        st.close()
        if done:
            print('主帳戶已經轉換過，不重複轉換')
            return 0
    old, new = PaperStore(OLD_DB), multi_account.MultiPaperStore(MAIN['db'])
    try:
        n = multi_account.import_two_asset(old, new, MAIN['params'])
    finally:
        old.close()
        new.close()
    print(f'已把舊帳戶的 {n} 天紀錄轉到 {MAIN["db"].name}；下一次執行會因 USDT 比例 0% 而調整到目標比例')
    return 0


async def run(dry_run: bool, ignore_window: bool) -> int:
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
        for m in MARKETS.values():
            if not markets[m].active:
                raise RuntimeError(f'{m} 目前不是 active 狀態')
        books = {m: await client.order_book(m, 50) for m in MARKETS.values()}
    code = 0
    for acc in (MAIN, SHADOW):
        # 一個帳戶失敗不影響另一個；回傳 1 讓下一小時的重試再做一次（已完成的帳戶會略過）
        try:
            st = multi_account.MultiPaperStore(acc['db'])
            try:
                assets = [a for a, _ in acc['params'].targets]
                r = multi_account.run_day(st, {a: books[MARKETS[a]] for a in assets}, {a: markets[MARKETS[a]] for a in assets},
                                          acc['params'], TAKER_FEE, now, INITIAL, write=not dry_run)
            finally:
                st.close()
        except Exception as exc:       # noqa: BLE001
            print(f'⚠️ {acc["label"]} 執行失敗：{type(exc).__name__}: {exc}')
            code = 1
            continue
        print(f'{acc["label"]}：{r["date"]} 已經執行過' if r['status'] == 'already' else message(r, acc['label']))
    return code


async def report(no_send: bool) -> int:
    load_dotenv(ROOT / '.env')
    text = morning_text()
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
    ap.add_argument('--migrate', action='store_true', help='一次性：舊兩資產帳戶轉成三資產主帳戶')
    args = ap.parse_args()
    if args.status:
        status()
        code = 0
    elif args.migrate:
        code = migrate()
    elif args.report:
        code = asyncio.run(report(args.no_send))
    else:
        code = asyncio.run(run(args.dry_run, args.ignore_window))
    sys.exit(code)


if __name__ == '__main__':
    main()
