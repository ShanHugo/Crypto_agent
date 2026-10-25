---
name: paper-status
description: 看比例再平衡模擬帳戶的現況：帳戶走勢、交易紀錄、今天凌晨有沒有執行、排程狀態。使用者問「模擬帳戶怎樣」「今天有沒有跑」「帳戶現在多少」時使用。只讀不寫。
---

只讀，不修改任何檔案、不推送。一次跑完下面的指令（Git Bash，在專案根目錄）：

```bash
export PYTHONIOENCODING=utf-8
.venv/Scripts/python.exe scripts/paper_rebalance.py --status
.venv/Scripts/python.exe scripts/paper_rebalance.py --report --no-send
tail -n 12 logs/paper_rebalance.log logs/paper_report.log 2>/dev/null
```

排程狀態用 PowerShell 工具查：

```powershell
Get-ScheduledTask -TaskPath '\crypto_robot\' | ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; '{0} state={1} next={2} last={3} result={4}' -f $_.TaskName, $_.State, $i.NextRunTime, $i.LastRunTime, $i.LastTaskResult }
```

回報（繁體中文，簡短）：
- 今天凌晨有沒有執行、做了什麼（建倉／買／賣／不動作）。
- 目前權益與報酬，和兩個對照（不再平衡、全部 ETH）比較。
- 排程是否正常（`LastTaskResult` 0 = 成功；267011 = 還沒跑過）；缺 `paper_report` 就提醒使用者以系統管理員重跑 `scripts\register_tasks.ps1`。
- 有 ⚠️ 備註（漏跑、深度不足、差額太小）要轉述。
