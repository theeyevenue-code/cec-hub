# CEC Hub watchdog — starts the Hub if it isn't already serving on port 5680.
#
# Safe to run as often as you like: if the Hub is already up it does nothing and
# writes nothing. Only a real restart gets logged (watchdog.log), so the log
# stays short and every line means "the Hub had gone down".
#
# Installed as a Scheduled Task by INSTALL-WATCHDOG.bat (at logon + every 5 min),
# which is what makes the Hub self-healing rather than "dead until someone
# notices". Start it by hand any time with:  powershell -File HUB-WATCHDOG.ps1

$PORT = 5680
$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$log  = Join-Path $here 'watchdog.log'

function Write-Log($msg) {
    try { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg" | Add-Content -Path $log -Encoding utf8 } catch {}
}

# Already serving? Then there is nothing to do — stay silent.
$listening = Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue
if ($listening) { exit 0 }

# Find pythonw.exe. The user's LOCALAPPDATA is the normal spot; the C:\Users\*
# sweep is the fallback for when this runs without the user's environment.
$py = $null
$paths = @()
if ($env:LOCALAPPDATA) { $paths += "$env:LOCALAPPDATA\Programs\Python\Python3*\pythonw.exe" }
$paths += 'C:\Users\*\AppData\Local\Programs\Python\Python3*\pythonw.exe'
$paths += 'C:\Program Files\Python3*\pythonw.exe'
foreach ($p in $paths) {
    $hit = Get-ChildItem $p -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($hit) { $py = $hit.FullName; break }
}
if (-not $py) {
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source }
}
if (-not $py) { Write-Log "Hub is DOWN but pythonw.exe could not be found - not started."; exit 1 }

try {
    Start-Process -FilePath $py -ArgumentList 'app.py' -WorkingDirectory $here -WindowStyle Hidden
    Start-Sleep -Seconds 4
    $now = Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue
    if ($now) { Write-Log "Hub was down - restarted OK (pid $($now.OwningProcess | Select-Object -First 1))." }
    else      { Write-Log "Hub was down - tried to start it but port $PORT still not listening." }
} catch {
    Write-Log "Hub was down - start failed: $($_.Exception.Message)"
    exit 1
}
