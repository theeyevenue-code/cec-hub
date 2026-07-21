# CEC Hub watchdog — keeps exactly ONE healthy Hub serving on port 5680.
#
# Installed as a Scheduled Task by INSTALL-WATCHDOG.bat (at logon + every 5 min),
# which is what makes the Hub self-healing rather than "dead until someone
# notices". Safe to run as often as you like: if the Hub is healthy this does
# nothing and writes nothing, so every line in watchdog.log means something real.
# Run by hand any time with:  powershell -File HUB-WATCHDOG.ps1
#
# WHY THIS IS NOT JUST A PORT CHECK (rewritten 2026-07-21)
# The original asked "is anything LISTENING on 5680?" and started a new instance
# if not. Two flaws, both hit in production:
#   1. Werkzeug sets SO_REUSEADDR, so on Windows a SECOND instance binds the port
#      happily instead of failing. Nothing pushed back, so every false-negative
#      check permanently added a process. On 21 Jul there were 29 Hub copies
#      running (plus 28 Referral and 25 SightTrack — all three share this design).
#   2. With several processes on one port, connections reach only one of them. If
#      that one is wedged the port still LISTENS, so the watchdog reports healthy
#      while the app refuses connections.
# Also fixed here: the Hub was launched as `pythonw.exe app.py` (relative), so its
# processes were indistinguishable from any other Python app and could only be
# identified by which one happened to hold the port. It now launches with the
# ABSOLUTE script path so the watchdog can find and reap its own duplicates.
#
# Sibling copies of this script — keep changes in step:
#   C:\CEC\CEC-Optomate-Agent\tracker\WATCHDOG.ps1  (SightTrack, 5681)
#   ...\CEC Bots\cec-referral-tool\WATCHDOG.ps1     (Referral, 5678)

$PORT   = 5680
$URL    = "http://127.0.0.1:$PORT/"
$MATCH  = 'cec-hub'      # identifies Hub processes by command line
$LABEL  = 'Hub'
$here   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$log    = Join-Path $here 'watchdog.log'
$marker = Join-Path $here 'HUB-UNHEALTHY.txt'
$appPy  = Join-Path $here 'app.py'

function Write-Log($msg) {
    try { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg" | Add-Content -Path $log -Encoding utf8 } catch {}
}

function Test-Healthy {
    try { return ((Invoke-WebRequest -Uri $URL -UseBasicParsing -TimeoutSec 8).StatusCode -eq 200) }
    catch { return $false }
}

# NOTE: every call site wraps this in @(). PowerShell unwraps a single-element
# array on return and a lone CIM object has no .Count, so `$x.Count -gt 0` would
# silently be $null -gt 0 == $false for exactly ONE process — the commonest case.
function Get-HubProcesses {
    try {
        @(Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" -ErrorAction Stop |
            Where-Object { $_.CommandLine -match $MATCH })
    } catch { @() }
}

if (Test-Healthy) {
    $procs = @(Get-HubProcesses)
    if ($procs.Count -gt 1) {
        $keep = $procs | Sort-Object CreationDate -Descending | Select-Object -First 1
        $procs | Where-Object { $_.ProcessId -ne $keep.ProcessId } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Write-Log "Healthy but $($procs.Count) instances were running - reaped $($procs.Count - 1), kept pid $($keep.ProcessId)."
    }
    if (Test-Path $marker) { Remove-Item $marker -Force -ErrorAction SilentlyContinue }
    exit 0
}

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
if (-not $py) {
    Write-Log "$LABEL is UNHEALTHY but pythonw.exe could not be found - not started."
    "$LABEL is down and pythonw.exe could not be found. $(Get-Date)" | Set-Content $marker -Encoding utf8
    exit 1
}

$stale = @(Get-HubProcesses)
if ($stale.Count -gt 0) {
    $stale | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

try {
    # ABSOLUTE path (see header) so the process is identifiable afterwards.
    Start-Process -FilePath $py -ArgumentList "`"$appPy`"" -WorkingDirectory $here -WindowStyle Hidden
} catch {
    Write-Log "$LABEL unhealthy - start failed: $($_.Exception.Message)"
    "$LABEL could not be started. $(Get-Date)" | Set-Content $marker -Encoding utf8
    exit 1
}

$ok = $false
foreach ($i in 1..6) {
    Start-Sleep -Seconds 3
    if (Test-Healthy) { $ok = $true; break }
}

if ($ok) {
    $now  = @(Get-HubProcesses)
    $note = ''
    if ($stale.Count -gt 0) { $note = " (cleared $($stale.Count) stale instance(s) first)" }
    Write-Log "$LABEL was unhealthy - restarted OK, now $($now.Count) instance(s)$note."
    if (Test-Path $marker) { Remove-Item $marker -Force -ErrorAction SilentlyContinue }
} else {
    Write-Log "$LABEL was unhealthy - RESTART DID NOT TAKE, still not answering on $PORT. NEEDS A HUMAN."
    "$LABEL is DOWN and the watchdog could not restart it.`r`nLast tried: $(Get-Date)`r`nCheck: $URL" |
        Set-Content $marker -Encoding utf8
    exit 1
}
