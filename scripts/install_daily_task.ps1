<#
.SYNOPSIS
    Register a Windows Scheduled Task that runs daily_brief.py every weekday.

.PARAMETER Time
    Daily run time in 24h format, your local time. Default: 17:30 (5:30 PM)
    — runs after US market close (16:00 ET) + buffer for data settling.
    If you're in MT/PT, set this to ~14:30/15:30 to align with US close.

.PARAMETER TaskName
    Scheduled Task name. Default: "QuantAdvisor-DailyBrief".

.PARAMETER WebhookUrl
    Discord webhook URL. Stored in the task's environment.
    Get one: Discord > Channel Settings > Integrations > Webhooks > New.

.EXAMPLE
    .\scripts\install_daily_task.ps1 -WebhookUrl "https://discord.com/api/webhooks/..."

.EXAMPLE
    .\scripts\install_daily_task.ps1 -Time "16:30" -WebhookUrl $env:DISCORD_WEBHOOK_URL

.NOTES
    Requires admin if scheduling for LOCAL SYSTEM. By default runs as the current user
    (no admin needed). The task wakes the computer if asleep.

    To remove:  Unregister-ScheduledTask -TaskName "QuantAdvisor-DailyBrief"
    To run now: Start-ScheduledTask -TaskName "QuantAdvisor-DailyBrief"
    To inspect: Get-ScheduledTask -TaskName "QuantAdvisor-DailyBrief" | fl *
#>

[CmdletBinding()]
param(
    [string] $Time = "17:30",
    [string] $TaskName = "QuantAdvisor-DailyBrief",
    [Parameter(Mandatory=$true)]
    [string] $WebhookUrl
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$PythonExe   = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Script      = Join-Path $ProjectRoot "daily_brief.py"
$LogDir      = Join-Path $ProjectRoot "logs"

if (-not (Test-Path $PythonExe)) {
    throw "venv python not found at $PythonExe — run setup first"
}
if (-not (Test-Path $Script)) {
    throw "daily_brief.py not found at $Script"
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Wrapper batch file — easier than embedding env vars in the task action
$Wrapper = Join-Path $ProjectRoot "scripts\_run_daily_brief.cmd"
@"
@echo off
setlocal
set "DISCORD_WEBHOOK_URL=$WebhookUrl"
set "PYTHONIOENCODING=utf-8"
cd /d "$ProjectRoot"
"$PythonExe" "$Script" 1>> "$LogDir\daily_brief.out.log" 2>> "$LogDir\daily_brief.err.log"
exit /b %ERRORLEVEL%
"@ | Set-Content -Path $Wrapper -Encoding ASCII

Write-Host "Wrapper written: $Wrapper"

$Action  = New-ScheduledTaskAction -Execute $Wrapper
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $Time
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -WakeToRun `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

# Remove existing if present (so re-running this script updates the task)
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed previous task $TaskName"
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Daily semi-sector + AI infra brief; Discord push at $Time on weekdays." | Out-Null

Write-Host ""
Write-Host "Registered: $TaskName" -ForegroundColor Green
Write-Host "  Time:     $Time on Mon-Fri"
Write-Host "  Runs as:  $env:USERNAME"
Write-Host "  Logs:     $LogDir\daily_brief.{out,err}.log"
Write-Host ""
Write-Host "Test it now:" -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName $TaskName"
Write-Host "  Get-Content $LogDir\daily_brief.out.log -Tail 30"
