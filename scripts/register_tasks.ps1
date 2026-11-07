# 建立每日排程：01:35 執行比例再平衡模擬帳戶（使用者只允許 01:30～08:00 自動交易，執行時不推送），
# 之後每小時重試到 07:35（同一天只會成交一次，已成功的重試只會略過）；08:30 推送當天凌晨的結果到 Discord。
# 00:00 Kronos 多空預測（只記錄不下單，DESIGN.md 6.7；模型在 ../_vendor/kronos_venv）。
# 08:05 blend 前瞻模擬的每週目標（DESIGN.md 6.18；每天跑，這週寫過就直接結束；不用金鑰、不下單）；futures_paper 是開機啟動的常駐模擬程序。
# 多空比與未平倉量（DESIGN.md 6.19）2026-11-07 起不排程：歷史可從資料站補（6.20），要研究時手動執行 scripts/fetch_positioning.py；舊排程 positioning 由本腳本刪除。
# 以 S4U 方式執行：不論是否登入 Windows 都會跑，且不需要儲存密碼（需以系統管理員身分執行本腳本）。
# 電腦要開著、不能在睡眠；如果 01:35 錯過（例如關機），開機後會補跑，但補跑時若已超過 08:00，程式會自己略過。
# 用法（PowerShell，在專案根目錄）：  powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
# 停用：  powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -Disable
param([switch]$Disable)

$root = Split-Path -Parent $PSScriptRoot
$wrapper = Join-Path $root 'scripts\run_scheduled.cmd'
# RetryHours：第一次之後每小時再執行幾次（01:35 + 6 次 = 07:35 為最後一次，08:00 前結束）
$tasks = @(@{Name = 'paper_rebalance'; At = '01:35'; Script = 'paper_rebalance'; Args = ''; RetryHours = 6},
           @{Name = 'paper_report'; At = '08:30'; Script = 'paper_rebalance'; Args = '--report'; RetryHours = 0},
           @{Name = 'kronos_forecast'; At = '00:00'; Script = 'kronos_forecast'; Args = ''; RetryHours = 0},
           @{Name = 'xsec_forward'; At = '08:05'; Script = 'xsec_forward'; Args = ''; RetryHours = 6})

if ($Disable) {
    foreach ($n in @($tasks.Name) + 'futures_paper') {
        Disable-ScheduledTask -TaskPath '\crypto_robot\' -TaskName $n -ErrorAction SilentlyContinue | Out-Null
    }
    Get-ScheduledTask -TaskPath '\crypto_robot\' -ErrorAction SilentlyContinue | ForEach-Object { '{0}  {1}' -f $_.TaskName, $_.State }
    return
}

Unregister-ScheduledTask -TaskPath '\crypto_robot\' -TaskName 'positioning' -Confirm:$false -ErrorAction SilentlyContinue
if (-not (Test-Path (Join-Path $root '.venv\Scripts\python.exe'))) { throw '找不到 .venv，請先建立虛擬環境並安裝 requirements.lock' }
if (-not (Test-Path (Join-Path $root '.env'))) { Write-Warning '找不到 .env：沒有 DISCORD_WEBHOOK_URL 時不會推送' }

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
foreach ($t in $tasks) {
    $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "' + $wrapper + '" ' + $t.Name + ' ' + $t.Script + ' ' + $t.Args) -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Daily -At $t.At
    if ($t.RetryHours -gt 0) {
        $rep = New-ScheduledTaskTrigger -Once -At $t.At -RepetitionInterval (New-TimeSpan -Hours 1) `
            -RepetitionDuration (New-TimeSpan -Hours $t.RetryHours -Minutes 1)
        $trigger.Repetition = $rep.Repetition
    }
    Register-ScheduledTask -TaskPath '\crypto_robot\' -TaskName $t.Name -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description 'crypto_robot' -Force | Out-Null
}
# 常駐模擬程序：開機就跑、當掉 1 分鐘後重啟，不設執行時間上限（不碰真錢，不用金鑰）
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "' + $wrapper + '" futures_paper futures_paper') -WorkingDirectory $root
$forever = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskPath '\crypto_robot\' -TaskName 'futures_paper' -Action $action -Trigger (New-ScheduledTaskTrigger -AtStartup) -Settings $forever `
    -Principal $principal -Description 'crypto_robot' -Force | Out-Null
Start-ScheduledTask -TaskPath '\crypto_robot\' -TaskName 'futures_paper'
Get-ScheduledTask -TaskPath '\crypto_robot\' | ForEach-Object {
    $i = $_ | Get-ScheduledTaskInfo
    '{0}  state={1}  next={2}' -f $_.TaskName, $_.State, $i.NextRunTime
}
