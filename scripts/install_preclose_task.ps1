<#
.SYNOPSIS
    Register a Windows Scheduled Task that runs preclose_brief.py at 15:30 ET
    on weekdays (30 min before US market close at 16:00 ET).

.PARAMETER Time
    Daily run time, your LOCAL (Montreal/ET) time. Default 15:30.
.PARAMETER WebhookUrl
    Discord webhook URL.

.EXAMPLE
    .\scripts\install_preclose_task.ps1 -WebhookUrl "https://discord.com/api/webhooks/..."
#>
[CmdletBinding()]
param(
    [string] $Time = "15:30",
    [string] $TaskName = "QuantAdvisor-PreClose",
    [Parameter(Mandatory=$true)]
    [string] $WebhookUrl
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$PythonExe   = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Script      = Join-Path $ProjectRoot "preclose_brief.py"
$LogDir      = Join-Path $ProjectRoot "logs"

if (-not (Test-Path $PythonExe)) { throw "venv python not found at $PythonExe" }
if (-not (Test-Path $Script)) { throw "preclose_brief.py not found at $Script" }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Wrapper = Join-Path $ProjectRoot "scripts\_run_preclose.cmd"
@"
@echo off
setlocal
set "DISCORD_WEBHOOK_URL=$WebhookUrl"
set "PYTHONIOENCODING=utf-8"
cd /d "$ProjectRoot"
"$PythonExe" "$Script" 1>> "$LogDir\preclose.out.log" 2>> "$LogDir\preclose.err.log"
exit /b %ERRORLEVEL%
"@ | Set-Content -Path $Wrapper -Encoding ASCII

Write-Host "Wrapper written: $Wrapper"

$Action  = New-ScheduledTaskAction -Execute $Wrapper
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $Time
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
    -Description "Pre-close trading brief (guru consensus + regime) at $Time ET weekdays." | Out-Null

Write-Host ""
Write-Host "Registered: $TaskName" -ForegroundColor Green
Write-Host "  Time:  $Time ET on Mon-Fri (30 min before US close)"
Write-Host "  Logs:  $LogDir\preclose.{out,err}.log"
Write-Host ""
Write-Host "Test now: Start-ScheduledTask -TaskName $TaskName"
