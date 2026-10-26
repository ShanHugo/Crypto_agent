"""Agent 健康檢查：看每個排程「有沒有達到預期」，不只看有沒有報錯（做法同 Stock_Robot）。

等級：ok（正常）、yellow（有狀況但已自行處理或只需留意）、red（需要人工）。
函式都是純函式，時間與資料由呼叫端傳入，方便測試；讀檔的部分在 scripts/console_data.py。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

LEVELS = {'ok': 0, 'yellow': 1, 'red': 2}
PAPER_AT = (1, 40)      # 01:35 排程，留 5 分鐘
REPORT_AT = (8, 35)     # 08:30 排程，留 5 分鐘
WEBHOOK = re.compile(r'discord(?:app)?\.com/api/webhooks/', re.I)
LOG_DATE = re.compile(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})')
LOG_TIME = re.compile(r'(?<![\d/])(\d{1,2}):(\d{2}):\d{2}')


@dataclass(frozen=True)
class Check:
    name: str
    level: str
    detail: str
    hint: str = ''


def agent(key: str, label: str, checks: list[Check]) -> dict:
    level = max((c.level for c in checks), key=LEVELS.get, default='ok')
    return {'key': key, 'label': label, 'level': level, 'checks': [asdict(c) for c in checks]}


def overall(agents: list[dict]) -> str:
    return max((a['level'] for a in agents), key=LEVELS.get, default='ok')


def expected_date(now: datetime, at: tuple[int, int]) -> date:
    """排程時間過了就看今天，還沒到就看昨天。now 是台北時間。"""
    return now.date() if (now.hour, now.minute) >= at else now.date() - timedelta(days=1)


def parse_runs(lines: list[str]) -> list[dict]:
    """解析 run_scheduled.cmd 寫的紀錄：'===== 日期 時間' 開頭、'----- exit N' 結尾。time 是 (時, 分)。"""
    runs: list[dict] = []
    cur: dict | None = None
    for ln in lines:
        if ln.startswith('====='):
            m, t = LOG_DATE.search(ln), LOG_TIME.search(ln)
            cur = {'date': date(int(m[1]), int(m[2]), int(m[3])) if m else None,
                   'time': (int(t[1]), int(t[2])) if t else None, 'exit': None, 'lines': []}
            runs.append(cur)
        elif ln.startswith('----- exit') and cur is not None:
            code = ln.split()[-1]
            cur['exit'] = int(code) if code.lstrip('-').isdigit() else None
        elif cur is not None:
            cur['lines'].append(ln)
    return runs


def check_paper(now: datetime, snapshots: list[dict], runs: list[dict], paused: bool) -> list[Check]:
    """模擬再平衡（01:35）。snapshots：[{date, action, note}]，由舊到新。"""
    if not snapshots:
        return [Check('模擬帳戶', 'yellow', '還沒有建立', '01:30～07:30 之間執行 scripts/paper_rebalance.py 會建倉')]
    out = []
    want = expected_date(now, PAPER_AT)
    dates = {date.fromisoformat(s['date']): s for s in snapshots}
    start = min(dates)
    if paused:
        out.append(Check('今天凌晨執行', 'yellow', '已暫停（state/paper_rebalance.paused）', '要恢復就刪除這個檔案'))
    elif want in dates:
        s = dates[want]
        out.append(Check('今天凌晨執行', 'ok', f'{want} 已執行：{s["action"]}'))
    elif want >= start:
        out.append(Check('今天凌晨執行', 'red', f'沒有 {want} 的執行紀錄',
                         '看 logs/paper_rebalance.log；確認電腦 01:35 沒有關機或睡眠、排程 paper_rebalance 已啟用'))
    week = [want - timedelta(days=i) for i in range(7)]
    missed = [d for d in week if start <= d < want and d not in dates]
    if missed:
        out.append(Check('最近 7 天', 'yellow', f'漏跑 {len(missed)} 天：{", ".join(d.isoformat() for d in sorted(missed))}',
                         '漏跑那天只是少一次檢查，帳戶不會出錯；常發生就檢查電腦睡眠設定'))
    else:
        out.append(Check('最近 7 天', 'ok', '沒有漏跑'))
    if runs and runs[-1]['exit'] not in (0, None):
        tail = ' / '.join(runs[-1]['lines'][-2:])
        out.append(Check('上次排程', 'red', f'結束代碼 {runs[-1]["exit"]}：{tail}', '看 logs/paper_rebalance.log 的錯誤訊息'))
    elif runs:
        out.append(Check('上次排程', 'ok', '正常結束'))
    note = snapshots[-1].get('note')
    if note:
        out.append(Check('最新紀錄備註', 'yellow', note))
    return out


def check_report(now: datetime, runs: list[dict], webhook_set: bool, account_start: date | None) -> list[Check]:
    """早上報告（08:30）。"""
    out = [Check('Discord', 'ok', '已設定 webhook') if webhook_set
           else Check('Discord', 'red', '.env 沒有 DISCORD_WEBHOOK_URL 或 PAPER_WEBHOOK_URL', '在 Crypto_Robot/.env 填入 webhook')]
    want = expected_date(now, REPORT_AT)
    # 只算排程時間附近以後的執行（手動測試、例如 --no-send 預覽，不算推送）
    done = [r for r in runs if r['date'] == want and (r['time'] is None or r['time'] >= (REPORT_AT[0], REPORT_AT[1] - 10))]
    if account_start is None or want < account_start:
        out.append(Check('今天的報告', 'ok', '帳戶建立後才開始推送'))
    elif not done:
        out.append(Check('今天的報告', 'red', f'{want} 08:30 沒有執行',
                         '排程 paper_report 可能還沒建立：以系統管理員身分重跑 scripts\\register_tasks.ps1'))
    elif done[-1]['exit'] not in (0, None):
        out.append(Check('今天的報告', 'red', f'{want} 執行失敗（結束代碼 {done[-1]["exit"]}）', '看 logs/paper_report.log'))
    else:
        out.append(Check('今天的報告', 'ok', f'{want} 已推送'))
    return out


def check_system(env_tracked: bool, log_texts: dict[str, str], holdout: dict | None,
                 last_bar: datetime | None, now: datetime) -> list[Check]:
    """系統與安全。log_texts：紀錄檔名 → 內容；last_bar：K 線快取最後一根（台北時間）。"""
    out = [Check('.env 沒進 git', 'red', '.env 被 git 追蹤，webhook 會外流（repo 是公開的）', 'git rm --cached .env 後 commit')
           if env_tracked else Check('.env 沒進 git', 'ok', '')]
    leaked = [name for name, text in log_texts.items() if WEBHOOK.search(text)]
    out.append(Check('紀錄檔沒有 webhook', 'red', f'{", ".join(leaked)} 出現 webhook 網址', '刪除該行，並到 Discord 重新產生 webhook')
               if leaked else Check('紀錄檔沒有 webhook', 'ok', ''))
    if holdout:
        out.append(Check('考卷期', 'ok', f'{holdout.get("variant")}：{"通過" if holdout.get("passed") else "未通過"}（只考一次，已用掉）'))
    if last_bar is None:
        out.append(Check('K 線快取', 'ok', '沒有快取（只有研究需要）'))
    else:
        age = (now - last_bar).days
        out.append(Check('K 線快取', 'ok', f'最後一根 {last_bar:%Y-%m-%d %H:%M}（{age} 天前；只有研究需要，模擬帳戶用即時盤口）'))
    return out
