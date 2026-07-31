# RESTART-HUB.ps1 — safe one-click restart of the CEC Hub.
#
# Stops the running Hub cleanly, then lets the "CEC Hub Watchdog" scheduled
# task start a FRESH copy (so the new code loads, and the new process is owned
# by Task Scheduler — never a child of whatever launched this). Waits until the
# Hub answers again, then says so in plain words.
#
# Run by double-clicking RESTART-HUB.bat (which calls this). Safe to run any
# time: if the Hub is fine it just cycles it; if it's wedged, this clears it.

$ErrorActionPreference = 'SilentlyContinue'
$PORT = 5680
$URL  = "http://127.0.0.1:$PORT/"

Write-Host ""
Write-Host "  Restarting the CEC Hub..." -ForegroundColor Cyan
Write-Host ""

# 1. Stop every running Hub process (only the Python app.py under cec-hub —
#    matched narrowly so nothing else is touched). Handles the rare case of
#    more than one copy running.
$hub = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match 'python' -and $_.CommandLine -like '*cec-hub\app.py*' }
if ($hub) {
    $hub | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Write-Host "  Stopped the old Hub." -ForegroundColor Gray
} else {
    Write-Host "  The Hub was not running." -ForegroundColor Gray
}

# 2. Ask the watchdog to bring a fresh one up now (don't wait for its 5-min tick).
Start-ScheduledTask -TaskName 'CEC Hub Watchdog'
Write-Host "  Starting a fresh copy..." -ForegroundColor Gray

# 3. Wait until it answers.
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        if ((Invoke-WebRequest -Uri $URL -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) {
            $ok = $true; break
        }
    } catch {}
    Start-Sleep -Seconds 2
}

Write-Host ""
if ($ok) {
    Write-Host "  The CEC Hub is back up." -ForegroundColor Green
    Write-Host "  Refresh the Hub in your browser. You can close this window." -ForegroundColor Green
} else {
    Write-Host "  It hasn't answered yet." -ForegroundColor Yellow
    Write-Host "  Give it another minute (the watchdog checks every 5), then" -ForegroundColor Yellow
    Write-Host "  refresh your browser. If it stays down, tell Mark." -ForegroundColor Yellow
}
Write-Host ""