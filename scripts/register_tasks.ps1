# 建立每日排程：01:35 執行比例再平衡模擬帳戶（使用者只允許 01:30～07:30 自動交易）。
# 以 S4U 方式執行：不論是否登入 Windows 都會跑，且不需要儲存密碼（需以系統管理員身分執行本腳本）。
# 電腦要開著、不能在睡眠；如果 01:35 錯過（例如關機），開機後會補跑，但補跑時若已超過 07:30，程式會自己略過。
# 用法（PowerShell，在專案根目錄）：  powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
# 停用：  powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -Disable
param([switch]$Disable)

$root = Split-Path -Parent $PSScriptRoot
$wrapper = Join-Path $root 'scripts\run_scheduled.cmd'
$tasks = @(@{Name = 'paper_rebalance'; At = '01:35'})

if ($Disable) {
    foreach ($n in $tasks.Name) {
        Disable-ScheduledTask -TaskPath '\crypto_robot\' -TaskName $n -ErrorAction SilentlyContinue | Out-Null
    }
    Get-ScheduledTask -TaskPath '\crypto_robot\' -ErrorAction SilentlyContinue | ForEach-Object { '{0}  {1}' -f $_.TaskName, $_.State }
    return
}

if (-not (Test-Path (Join-Path $root '.venv\Scripts\python.exe'))) { throw '找不到 .venv，請先建立虛擬環境並安裝 requirements.lock' }
if (-not (Test-Path (Join-Path $root '.env'))) { Write-Warning '找不到 .env：沒有 DISCORD_WEBHOOK_URL 時不會推送' }

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
foreach ($t in $tasks) {
    $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "' + $wrapper + '" ' + $t.Name) -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Daily -At $t.At
    Register-ScheduledTask -TaskPath '\crypto_robot\' -TaskName $t.Name -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description 'crypto_robot' -Force | Out-Null
}
Get-ScheduledTask -TaskPath '\crypto_robot\' | ForEach-Object {
    $i = $_ | Get-ScheduledTaskInfo
    '{0}  state={1}  next={2}' -f $_.TaskName, $_.State, $i.NextRunTime
}
