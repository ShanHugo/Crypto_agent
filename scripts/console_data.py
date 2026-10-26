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

DB = ROOT / 'state' / 'paper_rebalance.db'
SHADOW_DB = ROOT / 'state' / 'shadow_rebal3.db'
SHADOW_LABEL = '三資產（ETH 50%／USDT 37.5%／台幣 12.5%）'
SHADOW_VARIANT = 'rebal3-eth50-cashusdt75-band10'
PAUSE_FILE = ROOT / 'state' / 'paper_rebalance.paused'
HOLDOUT = ROOT / 'state' / 'research' / 'holdout.json'
BARS = ROOT / 'data' / 'bars.db'
LOGS = {'paper_rebalance': ROOT / 'logs' / 'paper_rebalance.log', 'paper_report': ROOT / 'logs' / 'paper_report.log'}


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


def paper_data() -> dict:
    if not DB.exists():
        return {'account': None, 'curve': [], 'trades': []}
    from crypto_robot.paper.rebalance_account import PaperStore
    store = PaperStore(DB)
    try:
        acct = store.account()
        hist = store.history()
        trades = store.trade_rows()
    finally:
        store.close()
    account = None if acct is None else {
        'initial': float(acct.initial), 'target': float(acct.params.target), 'band': float(acct.params.band),
        'started_at': datetime.fromtimestamp(acct.started_at / 1000, TAIPEI).isoformat(timespec='minutes'),
        'cash': float(acct.cash), 'eth': float(acct.eth)}
    curve = [{'date': h['date'], 'time': datetime.fromtimestamp(h['ts'] / 1000, TAIPEI).strftime('%H:%M'),
              'price': num(h['price']), 'equity': num(h['equity']), 'weight': num(h['weight']),
              'static': num(h['static_equity']), 'hodl': num(h['hodl_equity']), 'action': h['action'], 'note': h['note']}
             for h in hist]
    trade_list = [{'date': t['date'], 'time': datetime.fromtimestamp(t['ts'] / 1000, TAIPEI).strftime('%H:%M'),
                   'side': t['side'], 'reason': t['reason'], 'qty': num(t['qty']), 'price': num(t['avg_price']),
                   'amount': num(t['gross']), 'fee': num(t['fee']), 'weight_before': num(t['weight_before']),
                   'weight_after': num(t['weight_after'])} for t in trades]
    return {'account': account, 'curve': curve, 'trades': trade_list}


def shadow_data() -> dict | None:
    """影子帳戶（第五輪三資產再平衡）；還沒建立就回傳 None。"""
    if not SHADOW_DB.exists():
        return None
    from crypto_robot.paper.multi_account import MultiPaperStore
    store = MultiPaperStore(SHADOW_DB)
    try:
        acct = store.account()
        hist = store.history()
        trades = store.trade_rows()
    finally:
        store.close()
    if acct is None:
        return None
    js = lambda t: {k: float(Decimal(v)) for k, v in json.loads(t).items()}     # noqa: E731
    return {
        'label': SHADOW_LABEL, 'variant': SHADOW_VARIANT, 'initial': float(acct['initial']),
        'started_at': datetime.fromtimestamp(acct['started_at'] / 1000, TAIPEI).isoformat(timespec='minutes'),
        'curve': [{'date': h['date'], 'time': datetime.fromtimestamp(h['ts'] / 1000, TAIPEI).strftime('%H:%M'),
                   'equity': num(h['equity']), 'weights': js(h['weights']), 'prices': js(h['prices']),
                   'action': h['action'], 'note': h['note']} for h in hist],
        'trades': [{'date': t['date'], 'time': datetime.fromtimestamp(t['ts'] / 1000, TAIPEI).strftime('%H:%M'),
                    'asset': t['asset'], 'side': t['side'], 'reason': t['reason'], 'qty': num(t['qty']),
                    'price': num(t['avg_price']), 'amount': num(t['gross']), 'fee': num(t['fee'])} for t in trades],
    }


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    now = datetime.now(TAIPEI).replace(tzinfo=None)
    paper = paper_data()
    logs = {k: read_lines(p) for k, p in LOGS.items()}
    holdout = json.loads(HOLDOUT.read_text(encoding='utf-8')) if HOLDOUT.exists() else None
    env = dotenv_values(ROOT / '.env') if (ROOT / '.env').exists() else {}
    webhook_set = bool((env.get('PAPER_WEBHOOK_URL') or env.get('DISCORD_WEBHOOK_URL') or '').strip())
    snaps = [{'date': c['date'], 'action': c['action'], 'note': c['note']} for c in paper['curve']]
    start = datetime.fromisoformat(paper['account']['started_at']).date() if paper['account'] else None
    shadow = shadow_data()
    shadow_agent = (hc.agent('shadow', '影子帳戶：三資產', hc.check_paper(
        now, [{'date': c['date'], 'action': c['action'], 'note': c['note']} for c in shadow['curve']],
        hc.parse_runs(logs['paper_rebalance']), PAUSE_FILE.exists())) if shadow else
        hc.agent('shadow', '影子帳戶：三資產', [hc.Check('影子帳戶', 'ok', '下一個凌晨 01:35 和主帳戶一起建立')]))
    agents = [
        hc.agent('paper_rebalance', '模擬再平衡（01:35）', hc.check_paper(now, snaps, hc.parse_runs(logs['paper_rebalance']),
                                                                    PAUSE_FILE.exists())),
        hc.agent('paper_report', '早上報告（08:30）', hc.check_report(now, hc.parse_runs(logs['paper_report']), webhook_set, start)),
        hc.agent('system', '系統與安全', hc.check_system(env_tracked(), {k: '\n'.join(v) for k, v in logs.items()},
                                                    holdout, last_bar(), now)),
        shadow_agent,
    ]
    out = {
        'generated_at': now.isoformat(timespec='seconds'),
        'paused': PAUSE_FILE.exists(),
        'paused_info': PAUSE_FILE.read_text(encoding='utf-8').strip() if PAUSE_FILE.exists() else None,
        **paper,
        'shadow': shadow,
        'holdout': None if holdout is None else {k: holdout.get(k) for k in
                                                 ('variant', 'period', 'passed', 'rebalances', 'stats', 'static', 'hodl', 'halves', 'tested_at')},
        'health': {'agents': agents, 'overall': hc.overall(agents)},
        'logs': {k: [hc.WEBHOOK.sub('<webhook>/', ln) for ln in v[-80:]] for k, v in logs.items()},
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == '__main__':
    main()
