"""給 Stock_Robot 操作台「虛擬貨幣」分頁用：把模擬帳戶、考卷結果、Agent 健康、排程紀錄印成一份 JSON。

    .venv\\Scripts\\python.exe scripts/console_data.py

只讀：不連網、不寫入任何檔案、不推送；資料庫不存在也不會建立。輸出不含 webhook 網址。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from dotenv import dotenv_values  # noqa: E402

from crypto_robot import health as hc  # noqa: E402
from crypto_robot.core.timeutil import TAIPEI  # noqa: E402

# 2026-10-26 起：主帳戶是三資產 band5（由舊兩資產帳戶轉換），影子是兩資產 band10（scripts/paper_rebalance.py 的 MAIN／SHADOW）
ACCOUNTS = {
    'main': {'db': ROOT / 'state' / 'paper_main.db', 'label': '三資產 band5（ETH 50%／USDT 37.5%／台幣 12.5%）',
             'variant': 'rebal3-eth50-cashusdt75-band5'},
    'shadow': {'db': ROOT / 'state' / 'shadow_rebal2.db', 'label': '兩資產 band10（ETH 50%／台幣 50%）',
               'variant': 'rebal-eth50-band10'},
}
PAUSE_FILE = ROOT / 'state' / 'paper_rebalance.paused'
HOLDOUT = ROOT / 'state' / 'research' / 'holdout.json'
BARS = ROOT / 'data' / 'bars.db'
LIVE_RECORDS = ROOT / 'state' / 'live' / 'balances.jsonl'   # 真實帳戶（唯讀）紀錄，含公開 IP，只在本機
LIVE_IP = ROOT / 'state' / 'live' / 'public_ip.json'
LIVE_DB = ROOT / 'state' / 'live' / 'live.db'
BACKUP_STATUS = ROOT / 'state' / 'backup' / 'last.json'
REPORTS = ROOT / 'reports'
LOGS = {'paper_rebalance': ROOT / 'logs' / 'paper_rebalance.log', 'paper_report': ROOT / 'logs' / 'paper_report.log',
        'kronos_forecast': ROOT / 'logs' / 'kronos_forecast.log', 'xsec_forward': ROOT / 'logs' / 'xsec_forward.log',
        'futures_paper': ROOT / 'logs' / 'futures_paper.log'}
FUTURES = ROOT / 'state' / 'futures'


def num(v):
    """資料庫存的是 Decimal 字串；JSON 只用來顯示，轉成 float。"""
    return float(Decimal(v)) if v not in (None, '') else None


def read_lines(path: Path) -> list[str]:
    """紀錄檔大多是 UTF-8（Python 輸出），但 cmd 寫的舊日期行可能是 Big5（cp950）：逐行解碼。"""
    if not path.exists():
        return []
    out = []
    for raw in path.read_bytes().splitlines():
        try:
            out.append(raw.decode('utf-8'))
        except UnicodeDecodeError:
            out.append(raw.decode('cp950', errors='replace'))
    return out


def env_tracked() -> bool:
    try:
        out = subprocess.run(['git', 'ls-files', '.env'], cwd=ROOT, capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return bool(out.stdout.strip())
    except OSError:
        return False


def last_bar() -> datetime | None:
    if not BARS.exists():
        return None
    import sqlite3
    db = sqlite3.connect(f'file:{BARS.as_posix()}?mode=ro', uri=True)
    try:
        ts = db.execute("SELECT MAX(ts) FROM bars WHERE market='ethtwd' AND period_min=60").fetchone()[0]
    finally:
        db.close()
    return datetime.fromtimestamp(ts / 1000, TAIPEI).replace(tzinfo=None) if ts else None


def account_data(key: str) -> dict | None:
    """一個模擬帳戶的帳戶資訊、每日快照、交易；還沒建立就回傳 None。只讀，資料庫不存在也不會建立。"""
    spec = ACCOUNTS[key]
    if not spec['db'].exists():
        return None
    from crypto_robot.paper.multi_account import MultiPaperStore
    store = MultiPaperStore(spec['db'])
    try:
        acct = store.account()
        hist = store.history()
        trades = store.trade_rows()
    finally:
        store.close()
    if acct is None:
        return None
    js = lambda t: {k: float(Decimal(v)) for k, v in json.loads(t).items()}     # noqa: E731
    p = acct['params']
    hm = lambda ts: datetime.fromtimestamp(ts / 1000, TAIPEI).strftime('%H:%M')  # noqa: E731
    return {
        'label': spec['label'], 'variant': spec['variant'], 'initial': float(acct['initial']),
        'targets': {a: float(t) for a, t in p.targets} | {'twd': float(p.cash_target)}, 'band': float(p.band),
        'started_at': datetime.fromtimestamp(acct['started_at'] / 1000, TAIPEI).isoformat(timespec='minutes'),
        'curve': [{'date': h['date'], 'time': hm(h['ts']), 'equity': num(h['equity']), 'weights': js(h['weights']),
                   'prices': js(h['prices']), 'action': h['action'], 'note': h['note']} for h in hist],
        'trades': [{'date': t['date'], 'time': hm(t['ts']), 'asset': t['asset'], 'side': t['side'], 'reason': t['reason'],
                    'qty': num(t['qty']), 'price': num(t['avg_price']), 'amount': num(t['gross']), 'fee': num(t['fee'])}
                   for t in trades],
    }


def weekly_files() -> dict:
    """每週事實檢討：日期 → 檔案（reports/weekly_YYYYMMDD.md）。"""
    out = {}
    for p in sorted(REPORTS.glob('weekly_*.md')):
        try:
            out[datetime.strptime(p.stem[7:], '%Y%m%d').date()] = p
        except ValueError:
            continue
    return out


def weekly_latest() -> dict | None:
    """最新一份每週檢討；還沒有就用目前資料即時算一份（標成預覽，不存檔）。"""
    files = weekly_files()
    if files:
        day = max(files)
        return {'date': day.isoformat(), 'preview': False, 'text': files[day].read_text(encoding='utf-8').strip()}
    import weekly_review as wr       # scripts/weekly_review.py：只讀資料庫，不連網
    from crypto_robot import weekly
    today = datetime.now(TAIPEI).date()
    return {'date': today.isoformat(), 'preview': True, 'text': weekly.text(wr.build(today))}


def forecast_data(now: datetime) -> dict:
    """前瞻紀錄（DESIGN 6.7，只記錄）：Kronos 每日多空＋營運者判斷、幣安資金費率做多。只讀紀錄檔與本機 K 線，不連網、不補記。"""
    import weekly_review as wr
    from crypto_robot.forecast import funding_long as fl, kronos_ls as kl
    d = ROOT / 'state' / 'forecast'
    fc, jd = kl.read(d / 'kronos_ls.jsonl'), kl.read(d / 'kronos_judge.jsonl')
    calls = {j['ts']: j for j in jd}
    day = lambda ts: datetime.fromtimestamp(ts / 1000, TAIPEI).strftime('%Y-%m-%d %H:%M')   # noqa: E731
    now_ms = int(now.replace(tzinfo=TAIPEI).timestamp() * 1000)
    return {
        'today': kl.morning_line(fc, jd, now_ms),
        'can_judge': kl.open_forecast(fc, jd, now_ms)[0] is not None,
        'kronos': [{'at': day(f['ts']), 'dir': f['dir'], 'up': f['up'], 'samples': f['samples'], 'mean': f['mean'],
                    'entry': f['entry'], 'call': calls[f['ts']]['call'] if f['ts'] in calls else None,
                    'reason': calls[f['ts']].get('reason', '') if f['ts'] in calls else ''} for f in fc[-14:]],
        'kronos_text': wr.kronos_section(),
        'funding': [{'at': day(r['ts']), 'entry': r['entry'], 'exit': r['exit'], 'funding': r['funding'], 'net': r['net']}
                    for r in fl.read_log(d / 'funding_long.jsonl')[-20:]],
        'funding_text': wr.funding_section(False),
    }


def futures_data() -> dict:
    """gbm 前瞻模擬（DESIGN 6.16）：只讀 state/futures，檔案不存在也不會建立。"""
    from crypto_robot.futures_agent import paper
    read = lambda p: json.loads(p.read_text(encoding='utf-8')) if p.exists() else None     # noqa: E731
    beat, target = read(FUTURES / 'heartbeat.json'), read(FUTURES / 'targets.json')
    out = {'beat': beat, 'target': target, 'text': paper.text(paper.summary([], [], [], [], paper.Account('main'))), 'recent': []}
    if (FUTURES / 'futures.db').exists():
        led = paper.Ledger(FUTURES / 'futures.db', readonly=True)
        try:
            trades = led.trades('main')
            out['text'] = paper.text(paper.summary(led.equity_rows('main'), led.equity_rows('reference'), trades, led.events('main'), led.load('main')))
            out['recent'] = [{'at': datetime.fromtimestamp(t['ts'] / 1000, TAIPEI).strftime('%Y-%m-%d %H:%M'), 'sym': t['sym'], 'qty': num(t['qty']),
                              'price': num(t['price']), 'fee': num(t['fee']), 'reason': t['reason']} for t in trades[-30:]]
        finally:
            led.close()
    return out


def kronos_days() -> set:
    """kronos_ls.jsonl 裡有預測的台北日期（給健康檢查）。"""
    from crypto_robot.forecast import kronos_ls as kl
    return {datetime.fromtimestamp(f['ts'] / 1000, TAIPEI).date() for f in kl.read(ROOT / 'state' / 'forecast' / 'kronos_ls.jsonl')}


def snaps(acc: dict | None) -> list[dict]:
    return [{'date': c['date'], 'action': c['action'], 'note': c['note']} for c in acc['curve']] if acc else []


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    now = datetime.now(TAIPEI).replace(tzinfo=None)
    main_acc, shadow = account_data('main'), account_data('shadow')
    logs = {k: read_lines(p) for k, p in LOGS.items()}
    holdout = json.loads(HOLDOUT.read_text(encoding='utf-8')) if HOLDOUT.exists() else None
    env = dotenv_values(ROOT / '.env') if (ROOT / '.env').exists() else {}
    webhook_set = bool((env.get('PAPER_WEBHOOK_URL') or env.get('DISCORD_WEBHOOK_URL') or '').strip())
    from crypto_robot.live import readonly
    live_records = readonly.load(LIVE_RECORDS, 30)
    keys_set = bool((env.get('MAX_API_KEY') or '').strip() and (env.get('MAX_API_SECRET') or '').strip())
    ref = readonly.ref_ip(LIVE_IP)
    live_on = (env.get('LIVE_TRADING') or '').strip() == '1' and (env.get('LIVE_CONFIRM') or '').strip() == 'I_UNDERSTAND'
    want = hc.expected_date(now, hc.PAPER_AT)
    live_runs, live_orders, run_want, orders_want = [], [], None, []
    if LIVE_DB.exists():
        from crypto_robot.live.executor import LiveStore
        ls = LiveStore(LIVE_DB)
        try:
            live_runs, live_orders, run_want, orders_want = ls.runs(14), ls.orders()[-30:], ls.run(want), ls.orders(want)
        finally:
            ls.close()
    start = datetime.fromisoformat(main_acc['started_at']).date() if main_acc else None
    futures = futures_data()
    runs = hc.parse_runs(logs['paper_rebalance'])
    shadow_agent = (hc.agent('shadow', '影子帳戶：兩資產', hc.check_paper(now, snaps(shadow), runs, PAUSE_FILE.exists()))
                    if shadow else
                    hc.agent('shadow', '影子帳戶：兩資產', [hc.Check('影子帳戶', 'ok', '下一個凌晨 01:35 和主帳戶一起建立')]))
    agents = [
        hc.agent('paper_rebalance', '模擬再平衡（01:35）', hc.check_paper(now, snaps(main_acc), runs, PAUSE_FILE.exists())),
        hc.agent('paper_report', '早上報告（08:30）', hc.check_report(now, hc.parse_runs(logs['paper_report']), webhook_set, start)
                 + hc.check_weekly(now, set(weekly_files()))),
        hc.agent('system', '系統與安全', hc.check_system(env_tracked(), {k: '\n'.join(v) for k, v in logs.items()},
                                                    holdout, last_bar(), now,
                                                    hc.stray_secret_files([p.name for p in ROOT.iterdir() if p.is_file()]))
                 + hc.check_backup(now, json.loads(BACKUP_STATUS.read_text(encoding='utf-8')) if BACKUP_STATUS.exists() else None,
                                   bool((env.get('BACKUP_DIRS') or '').strip()))),
        shadow_agent,
        hc.agent('live_trade', '真單', hc.check_live_trade(now, live_on, run_want, orders_want)),
        hc.agent('live', '真實帳戶（唯讀）', hc.check_live(now, live_records, keys_set, ref is not None)),
        hc.agent('kronos_forecast', 'Kronos 預測（00:00，只記錄）', hc.check_kronos(now, kronos_days())),
        hc.agent('futures_paper', 'gbm 前瞻模擬（只模擬）', hc.check_futures_paper(now, futures['beat'], (futures['target'] or {}).get('week'))),
    ]
    out = {
        'generated_at': now.isoformat(timespec='seconds'),
        'paused': PAUSE_FILE.exists(),
        'paused_info': PAUSE_FILE.read_text(encoding='utf-8').strip() if PAUSE_FILE.exists() else None,
        'main': main_acc,
        'shadow': shadow,
        'live_trade': {'enabled': live_on, 'cap': (env.get('LIVE_CAPITAL_CAP') or '9000').strip(), 'runs': live_runs, 'orders': live_orders},
        'live': {'keys_set': keys_set, 'ref_ip': ref, 'latest': live_records[-1] if live_records else None,
                 'history': [{k: r.get(k) for k in ('date', 'time', 'source', 'ok', 'equity')} for r in live_records[-14:]]},
        'holdout': None if holdout is None else {k: holdout.get(k) for k in
                                                 ('variant', 'period', 'passed', 'rebalances', 'stats', 'static', 'hodl', 'halves', 'tested_at')},
        'weekly': weekly_latest(),
        'forecast': forecast_data(now),
        'futures': futures,
        'health': {'agents': agents, 'overall': hc.overall(agents)},
        'logs': {k: [hc.WEBHOOK.sub('<webhook>/', ln) for ln in v[-80:]] for k, v in logs.items()},
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == '__main__':
    main()
