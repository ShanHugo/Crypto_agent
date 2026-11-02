"""Agent 健康檢查：看每個排程「有沒有達到預期」，不只看有沒有報錯（做法同 Stock_Robot）。

等級：ok（正常）、yellow（有狀況但已自行處理或只需留意）、red（需要人工）。
函式都是純函式，時間與資料由呼叫端傳入，方便測試；讀檔的部分在 scripts/console_data.py。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

LEVELS = {'ok': 0, 'yellow': 1, 'red': 2}
PAPER_AT = (1, 40)      # 01:35 排程，留 5 分鐘（之後每小時重試到 07:35）
WINDOW_END = (8, 0)    # 使用者允許自動交易的時段結束
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


def failed_attempts(runs: list[dict], day: date) -> list[dict]:
    """某一天結束代碼不是 0 的執行（時段內每小時重試，可能有好幾次）。"""
    return [r for r in runs if r['date'] == day and r['exit'] not in (0, None)]


def attempts_text(runs: list[dict], day: date) -> str:
    """'01:35、02:35 執行失敗（最後一個錯誤）'；沒有失敗就回傳空字串。"""
    fails = failed_attempts(runs, day)
    if not fails:
        return ''
    times = '、'.join(f'{r["time"][0]:02d}:{r["time"][1]:02d}' if r['time'] else '?' for r in fails)
    reason = next((ln.strip() for ln in reversed(fails[-1]['lines']) if ln.strip()), '沒有錯誤訊息')
    return f'{times} 執行失敗（{WEBHOOK.sub("<webhook>/", reason)[:120]}）'


def check_paper(now: datetime, snapshots: list[dict], runs: list[dict], paused: bool) -> list[Check]:
    """模擬再平衡（01:35 起每小時重試到 07:35）。snapshots：[{date, action, note}]，由舊到新。"""
    if not snapshots:
        return [Check('模擬帳戶', 'yellow', '還沒有建立', '01:30～08:00 之間執行 scripts/paper_rebalance.py 會建倉')]
    out = []
    want = expected_date(now, PAPER_AT)
    dates = {date.fromisoformat(s['date']): s for s in snapshots}
    start = min(dates)
    retrying = want not in dates and now.date() == want and (now.hour, now.minute) < WINDOW_END
    fails = attempts_text(runs, want)
    if paused:
        out.append(Check('今天凌晨執行', 'yellow', '已暫停（state/paper_rebalance.paused）', '要恢復就刪除這個檔案'))
    elif want in dates:
        done = f'{want} 已執行：{dates[want]["action"]}'
        out.append(Check('今天凌晨執行', 'yellow', f'重試後成功：{done}；之前 {fails}', '偶爾發生不用處理；常發生就把紀錄交給開發者')
                   if fails else Check('今天凌晨執行', 'ok', done))
    elif retrying:
        out.append(Check('今天凌晨執行', 'yellow', f'{want} 尚未成功，時段內每小時會重試到 07:35' + (f'；{fails}' if fails else ''),
                         '不用處理，等下一次重試'))
    elif want >= start:
        out.append(Check('今天凌晨執行', 'red', f'{want} 整個 01:30～08:00 都沒有成功' + (f'：{fails}' if fails else '（沒有執行紀錄）'),
                         '有錯誤訊息就交給開發者；沒有執行紀錄就確認電腦沒有關機或睡眠、排程 paper_rebalance 已啟用'))
    week = [want - timedelta(days=i) for i in range(7)]
    missed = [d for d in week if start <= d < want and d not in dates]
    if missed:
        out.append(Check('最近 7 天', 'yellow', f'漏跑 {len(missed)} 天：{", ".join(d.isoformat() for d in sorted(missed))}',
                         '漏跑那天只是少一次檢查，帳戶不會出錯；常發生就檢查電腦睡眠設定'))
    else:
        out.append(Check('最近 7 天', 'ok', '沒有漏跑'))
    if runs and runs[-1]['exit'] not in (0, None):
        tail = ' / '.join(runs[-1]['lines'][-2:])
        level = 'red' if want not in dates and not retrying else 'yellow'
        out.append(Check('上次排程', level, f'結束代碼 {runs[-1]["exit"]}：{tail}', '看 logs/paper_rebalance.log 的錯誤訊息'))
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


WEEKLY_SINCE = date(2026, 11, 2)     # 第一次每週事實檢討（週一）


def check_weekly(now: datetime, done: set[date]) -> list[Check]:
    """每週一 08:30 報告之後的每週事實檢討；done：已有 reports/weekly_YYYYMMDD.md 的日期。"""
    last = expected_date(now, REPORT_AT)
    monday = last - timedelta(days=last.weekday())
    if monday < WEEKLY_SINCE:
        return [Check('每週檢討', 'ok', f'第一次在 {WEEKLY_SINCE} 08:30 報告之後')]
    if monday not in done:
        return [Check('每週檢討', 'red', f'{monday}（週一）沒有產生每週檢討',
                      '看 logs/paper_report.log；可以手動執行 scripts/weekly_review.py --date ' + monday.isoformat())]
    return [Check('每週檢討', 'ok', f'{monday} 已推送（reports/weekly_{monday:%Y%m%d}.md）')]


KRONOS_SINCE = date(2026, 11, 3)     # 第一筆 Kronos 預測（00:00）


def check_kronos(now: datetime, days: set[date]) -> list[Check]:
    """00:00 的 Kronos 多空預測（只記錄）；days：kronos_ls.jsonl 裡有預測的台北日期。08:00 前還可能補跑，所以過了 08:00 才算漏。
    只是前瞻紀錄、不影響交易：漏一兩天 🟡，連續 3 天以上沒有才 🔴（排程大概壞了）。"""
    day = expected_date(now, WINDOW_END)
    if day < KRONOS_SINCE:
        return [Check('Kronos 預測', 'ok', f'第一筆在 {KRONOS_SINCE} 00:00')]
    if day in days:
        return [Check('Kronos 預測', 'ok', f'{day} 00:00 已記錄')]
    miss = 0
    while day - timedelta(days=miss) >= KRONOS_SINCE and day - timedelta(days=miss) not in days:
        miss += 1
    return [Check('Kronos 預測', 'red' if miss >= 3 else 'yellow', f'{day} 00:00 沒有預測（連續 {miss} 天沒有）',
                  '看 logs/kronos_forecast.log 與排程 kronos_forecast；只記錄，不影響交易')]


def check_live(now: datetime, records: list[dict], keys_set: bool, ref_ip_set: bool) -> list[Check]:
    """真實帳戶（唯讀，真單第一階段）：凌晨有讀到餘額、金鑰可用、公開 IP 沒變。records：state/live/balances.jsonl，由舊到新。"""
    if not keys_set:
        return [Check('金鑰', 'ok', '.env 沒有 MAX 金鑰，不讀真實帳戶')]
    out = [Check('白名單 IP', 'ok', '已記錄') if ref_ip_set else
           Check('白名單 IP', 'yellow', '沒有白名單 IP 紀錄', '執行 scripts/live_check.py --accept-ip')]
    if not records:
        return out + [Check('真實餘額', 'ok', '還沒讀過；下一個凌晨 01:35 會讀')]
    want = expected_date(now, PAPER_AT)
    scheduled = [r for r in records if r['source'] == 'scheduled']
    if not scheduled:
        out.append(Check('今天凌晨讀取', 'ok', '排程還沒讀過；下一個凌晨 01:35 開始每天讀'))
    elif not any(r['date'] == want.isoformat() for r in scheduled):
        out.append(Check('今天凌晨讀取', 'yellow', f'{want} 凌晨沒有讀到真實餘額', '看模擬再平衡那一項；排程沒跑就讀不到'))
    last = records[-1]
    if last['ip_changed']:
        out.append(Check('公開 IP', 'red', '公開 IP 和白名單不同（被擋時金鑰會失效）',
                         '到 MAX 的 API 金鑰頁面把白名單改成新的 IP，再執行 scripts/live_check.py --accept-ip'))
    else:
        out.append(Check('公開 IP', 'ok', '和白名單相同'))
    if not last['ok']:
        out.append(Check('真實餘額', 'red', f'{last["date"]} {last["time"]} 讀取失敗：{last["error"]}',
                         '401／403／2001 多半是金鑰錯誤或 IP 不在白名單（IP 變了時實際是 403）；到 MAX 的 API 金鑰頁面確認'))
    else:
        out.append(Check('真實餘額', 'ok', f'{last["date"]} {last["time"]} 估值 {float(last["equity"]):,.0f} 元'))
    return out


def check_live_trade(now: datetime, enabled: bool, run: dict | None, orders: list[dict]) -> list[Check]:
    """真單（第二階段）。run／orders：expected_date 那一天的紀錄（state/live/live.db）。"""
    if not enabled:
        return [Check('真單開關', 'ok', '未開啟（.env 的 LIVE_TRADING）')]
    want = expected_date(now, PAPER_AT)
    out = [Check('真單開關', 'ok', '開啟')]
    if run is None:
        if now.date() == want and (now.hour, now.minute) < WINDOW_END:
            return out + [Check('今天的真單', 'yellow', f'{want} 尚未執行，時段內每小時會重試', '不用處理')]
        return out + [Check('今天的真單', 'red', f'{want} 沒有執行紀錄', '看模擬再平衡那一項與 logs/paper_rebalance.log')]
    st = run['status']
    if st == 'blocked':
        out.append(Check('今天的真單', 'red', f'安全檢查擋下，沒有下單：{run["note"]}',
                         '依原因處理；不是已知狀況就交給開發者。擋下時帳戶不會動。'))
    elif st == 'running':
        out.append(Check('今天的真單', 'red', '送單到一半沒有跑完', '執行 scripts/live_trade.py --status，交給開發者對帳'))
    elif st == 'reconciled':
        out.append(Check('今天的真單', 'yellow', '重試時發現已送過單，只對帳、沒有送新單', '看 live_trade.py --status 確認成交'))
    else:
        out.append(Check('今天的真單', 'ok', '不動作' if st == 'hold' else f'完成，{len(orders)} 筆訂單'))
    bad = [o for o in orders if o['status'] in ('rejected', 'unknown')]
    if bad:
        out.append(Check('訂單', 'red', '；'.join(f'{o["side"]} {o["asset"].upper()} {o["status"]}：{o["error"] or ""}' for o in bad),
                         'unknown 代表送出後查不到結果：到 MAX 網頁確認有沒有成交，再交給開發者'))
    return out


def check_backup(now: datetime, last: dict | None, configured: bool) -> list[Check]:
    """state/backup/last.json：每個目的地都成功、3 天內有備份。"""
    if not configured:
        return [Check('備份', 'yellow', '.env 沒有 BACKUP_DIRS', '在 .env 設定備份資料夾')]
    if last is None:
        return [Check('備份', 'yellow', '還沒有備份紀錄', '每天 08:30 報告推送後會自動備份；也可以執行 scripts/backup.py')]
    bad = [d for d in last['destinations'] if not d['ok']]
    age = (now - datetime.fromisoformat(last['time'])).days
    if bad:
        return [Check('備份', 'red', f'{last["time"][:16]} 備份失敗：' + '；'.join(f'{d["dir"]}：{d["error"]}' for d in bad),
                      '多半是還沒登入 Windows，雲端硬碟沒有掛上：登入後執行 scripts/backup.py 補一次')]
    if age > 3:
        return [Check('備份', 'red', f'最後一次備份是 {last["time"][:10]}（{age} 天前）', '確認 08:30 報告排程有在跑；或執行 scripts/backup.py')]
    d = last['destinations'][0]
    return [Check('備份', 'ok', f'{last["time"][:16]}：{d["file"]}（{d["size"] / 1024:,.0f} KB）')]


SECRET_NAME = re.compile(r'api|key|secret|金鑰|密碼', re.I)


def stray_secret_files(names: list[str]) -> list[str]:
    """專案資料夾最上層、名稱像金鑰的文字檔（.env 以外）。"""
    return sorted(n for n in names if n != '.env' and not n.endswith('.example') and n.lower().endswith(('.txt', '.json', '.csv', '.md'))
                  and SECRET_NAME.search(n) and n not in ('README.md', 'CLAUDE.md'))


def check_system(env_tracked: bool, log_texts: dict[str, str], holdout: dict | None,
                 last_bar: datetime | None, now: datetime, stray: list[str] | None = None) -> list[Check]:
    """系統與安全。log_texts：紀錄檔名 → 內容；last_bar：K 線快取最後一根（台北時間）。"""
    out = [Check('.env 沒進 git', 'red', '.env 被 git 追蹤，webhook 會外流（repo 是公開的）', 'git rm --cached .env 後 commit')
           if env_tracked else Check('.env 沒進 git', 'ok', '')]
    if stray:
        out.append(Check('專案資料夾沒有金鑰檔', 'red', f'{"、".join(stray)} 看起來像金鑰或密碼檔',
                         '金鑰只放 .env；確認 .env 有了之後刪除這個檔案（repo 是公開的，絕不能 commit）'))
    else:
        out.append(Check('專案資料夾沒有金鑰檔', 'ok', ''))
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
