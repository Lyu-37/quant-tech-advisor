<#
.SYNOPSIS
    Register a Windows Scheduled Task that runs evaluate_predictions.py every
    Friday evening — the system grades its own signals weekly and pushes the
    excess-return report to Discord. This is the accountability loop: without
    it, every threshold is vibes.

.PARAMETER Time
    Run time, local (Montreal/ET). Default 18:30 Friday (after the daily brief).
.PARAMETER WebhookUrl
    Discord webhook URL.

.EXAMPLE
    .\scripts\install_weekly_eval_task.ps1 -WebhookUrl $env:DISCORD_WEBHOOK_URL
#>
[CmdletBinding()]
param(
    [string] $Time = "18:30",
    [string] $TaskName = "QuantAdvisor-WeeklyEval",
    [Parameter(Mandatory=$true)]
    [string] $WebhookUrl
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$PythonExe   = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Script      = Join-Path $ProjectRoot "scripts\evaluate_predictions.py"
$LogDir      = Join-Path $ProjectRoot "logs"

if (-not (Test-Path $PythonExe)) { throw "venv python not found at $PythonExe" }
if (-not (Test-Path $Script)) { throw "evaluate_predictions.py not found" }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Wrapper = Join-Path $ProjectRoot "scripts\_run_weekly_eval.cmd"
@"
@echo off
setlocal
set "DISCORD_WEBHOOK_URL=$WebhookUrl"
set "PYTHONIOENCODING=utf-8"
cd /d "$ProjectRoot"
"$PythonExe" "$Script" --discord 1>> "$LogDir\weekly_eval.out.log" 2>> "$LogDir\weekly_eval.err.log"
exit /b %ERRORLEVEL%
"@ | Set-Content -Path $Wrapper -Encoding ASCII

Write-Host "Wrapper written: $Wrapper"

$Action  = New-ScheduledTaskAction -Execute $Wrapper
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At $Time
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -WakeToRun -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed previous task $TaskName"
}

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Principal $Principal `
    -Description "Weekly signal-vs-realized excess-return report (Friday $Time)." | Out-Null

Write-Host ""
Write-Host "Registered: $TaskName (Friday $Time)" -ForegroundColor Green
Write-Host "Test now: Start-ScheduledTask -TaskName $TaskName"
