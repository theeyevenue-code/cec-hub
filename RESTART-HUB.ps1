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

# 0. Note which process answers on the Hub's port now, and leave a "planned restart" note so
#    the watchdog logs this as planned, not as a crash (11 Nov 2026: every deploy restart was
#    being counted as a failure - "6 restarts in 7 days" with no crash at all).
$oldPids = @(Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue |
             Select-Object -ExpandProperty OwningProcess -Unique)
(Get-Date -Format 'o') | Set-Content (Join-Path $PSScriptRoot 'PLANNED-RESTART.txt') -Encoding utf8

# 1. Stop every running Hub process (only the Python app.py under cec-hub —
#    matched narrowly so nothing else is touched). Handles the rare case of
#    more than one copy running. Also the process that owns the Hub's port, whose command
#    line may be unreadable from this account (11 Nov 2026: one started as administrator).
$hub = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match 'python' -and $_.CommandLine -like '*cec-hub\app.py*' } | Select-Object -ExpandProperty ProcessId)
$targets = @($hub + $oldPids | Where-Object { $_ } | Select-Object -Unique)
$denied = $false
foreach ($id in $targets) {
    try { Stop-Process -Id $id -Force -ErrorAction Stop } catch { $denied = $true }
}
if ($denied) {
    # The Hub runs under the watchdog task's account, which this session can't stop. Ask the watchdog to
    # do it: it owns the Hub (11 Nov 2026 - before this, every update needed Mark as administrator).
    (Get-Date -Format 'o') | Set-Content (Join-Path $PSScriptRoot 'RESTART-REQUESTED.txt') -Encoding utf8
    Write-Host "  Asked the watchdog to restart the Hub." -ForegroundColor Gray
} elseif ($targets.Count) { Write-Host "  Stopped the old Hub." -ForegroundColor Gray }
else { Write-Host "  The Hub was not running." -ForegroundColor Gray }

# 2. Ask the watchdog to bring a fresh one up now (don't wait for its 5-min tick).
Start-ScheduledTask -TaskName 'CEC Hub Watchdog'
Write-Host "  Starting a fresh copy..." -ForegroundColor Gray

# 3. Wait until a NEW copy answers (11 Nov 2026: this used to say "back up" when the old copy,
#    which it had failed to stop, was still answering - the new code never loaded).
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $now = @(Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue |
                 Select-Object -ExpandProperty OwningProcess -Unique)
        $fresh = $now.Count -gt 0 -and -not ($now | Where-Object { $oldPids -contains $_ })
        if ($fresh -and (Invoke-WebRequest -Uri $URL -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) {
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