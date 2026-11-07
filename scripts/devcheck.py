"""開發用一鍵檢查（DESIGN 6.21）：不用 AI，把健康燈號、測試、排程、git 整理成短摘要。

只讀：不改檔、不下單、不推送。全部正常結束碼 0，有 🟡／🔴 結束碼 1。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICON = {'yellow': '🟡', 'red': '🔴'}
OK_RESULTS = {0, 267011, 267009}   # 成功、還沒跑過、執行中
ALREADY_RUNNING = 2147946720       # 0x800710E0：已經在跑，拒絕再啟動（常駐的 futures_paper）
MAX_LINES = 12
TASKS_PS = ("Get-ScheduledTask -TaskPath '\\crypto_robot\\' | ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; "
            "'{0}|{1}|{2}' -f $_.TaskName, $_.State, $i.LastTaskResult }")


def last_line(text: str) -> str:
    return next((ln.strip() for ln in reversed(text.splitlines()) if ln.strip()), '沒有輸出')


def health_problems(agents: list[dict]) -> list[tuple[str, str]]:
    return [(c['level'], f"{a['key']}：{c['name']}，{c['detail']}")
            for a in agents for c in a['checks'] if c['level'] != 'ok']


def test_result(code: int, out: str) -> tuple[str, list[tuple[str, str]]]:
    summary = last_line(out).strip('= ').split(' in ')[0]
    if code == 0:
        return summary, []
    fails = [ln.split(' - ')[0] for ln in out.splitlines() if ln.startswith(('FAILED', 'ERROR'))][:5]
    return summary, [('red', '測試沒過：' + ('；'.join(fails) or summary))]


def task_problems(lines: list[str]) -> list[tuple[str, str]]:
    out = []
    for ln in lines:
        parts = ln.strip().split('|')
        if len(parts) != 3:
            continue
        name, state, res = parts
        code = int(res) if res.strip().lstrip('-').isdigit() else None
        if state == 'Disabled':
            out.append(('yellow', f'排程 {name} 已停用'))
        elif code in OK_RESULTS or (code == ALREADY_RUNNING and state == 'Running'):
            continue
        else:
            out.append(('red', f'排程 {name} 上次結果 {res}（狀態 {state}）'))
    return out


def git_problems(status: str, ahead: int | None) -> list[tuple[str, str]]:
    out = []
    dirty = [ln[3:] for ln in status.splitlines() if ln.strip()]
    if dirty:
        out.append(('yellow', f'git 有 {len(dirty)} 個檔案沒 commit：' + '、'.join(dirty[:5])))
    if ahead is None:
        out.append(('yellow', 'git 查不到遠端分支，不知道有沒有 push'))
    elif ahead:
        out.append(('yellow', f'git 有 {ahead} 個 commit 沒 push'))
    return out


def summarize(now: datetime, agents_text: str, tests: str, tasks: int, problems: list[tuple[str, str]]) -> list[str]:
    reds = sum(p[0] == 'red' for p in problems)
    lines = [f'devcheck {now:%Y-%m-%d %H:%M}',
             f'🔴 {reds}  🟡 {len(problems) - reds}  {agents_text}  測試 {tests}  排程 {tasks} 個'
             + ('  全部正常' if not problems else '')]
    ordered = sorted(problems, key=lambda p: p[0] != 'red')
    lines += [f'{ICON[lv]} {text[:160]}' for lv, text in ordered[:MAX_LINES]]
    if len(ordered) > MAX_LINES:
        lines.append(f'…還有 {len(ordered) - MAX_LINES} 項')
    return lines


def run(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, '', str(e)
    return r.returncode, r.stdout.decode('utf-8', 'replace'), r.stderr.decode('utf-8', 'replace')


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    problems: list[tuple[str, str]] = []

    code, out, err = run([sys.executable, 'scripts/console_data.py'], 120)
    if code == 0:
        agents = json.loads(out)['health']['agents']
        problems += health_problems(agents)
        agents_text = f"Agent {sum(a['level'] == 'ok' for a in agents)}/{len(agents)} 正常"
    else:
        problems.append(('red', '健康檢查跑不起來：' + last_line(err)))
        agents_text = 'Agent ?'

    code, out, err = run([sys.executable, '-m', 'pytest', '-q'], 600)
    tests, p = test_result(code, out + err)
    problems += p

    code, out, err = run(['powershell', '-NoProfile', '-Command', TASKS_PS], 60)
    lines = [ln for ln in out.splitlines() if ln.count('|') == 2]
    problems += task_problems(lines) if lines else [('yellow', '查不到排程：' + last_line(err))]

    _, status, _ = run(['git', 'status', '--porcelain'], 30)
    code, ahead, _ = run(['git', 'rev-list', '--count', '@{u}..HEAD'], 30)
    problems += git_problems(status, int(ahead.strip()) if code == 0 and ahead.strip().isdigit() else None)

    print('\n'.join(summarize(datetime.now(), agents_text, tests, len(lines), problems)))
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()
