# COMPANION-PANEL.ps1 — open the CEC Hub as a slim corner "companion" window.
#
# A test of the pinned-window form factor: a chrome-less browser app window
# (no tabs/address bar) docked to the bottom-right of the main screen, showing
# the Hub to-do list. Minimise/close with the normal window buttons; double-
# click the "CEC Companion" desktop shortcut to reopen it.
#
# NOTE: a browser window can't float ON TOP of Optomate — click into Optomate
# and this tucks behind it (that's the trade-off of the pinned-window choice).
# The always-on-top version would need a small desktop app instead.

$HUB_URL = 'http://192.168.1.62:5680/#/todo'
$W = 400
$H = 720

Add-Type -AssemblyName System.Windows.Forms
$wa = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$X = [Math]::Max($wa.Right  - $W, 0)
$Y = [Math]::Max($wa.Bottom - $H, 0)

# Prefer Chrome, fall back to Edge (both are Chromium and support --app).
$browser = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LocalAppData\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $browser) {
    [System.Windows.Forms.MessageBox]::Show(
        "Couldn't find Chrome or Edge to open the companion window.") | Out-Null
    exit 1
}

# A dedicated profile folder keeps this as its own standalone window (so it
# minimises/closes independently of your normal browsing).
$profile = Join-Path $env:LocalAppData 'CEC-Companion'

# Start-Process (detached) so this script returns immediately — piping a GUI
# app to Out-Null would block until the browser is closed.
Start-Process -FilePath $browser -ArgumentList @(
    "--app=$HUB_URL", "--window-size=$W,$H",
    "--window-position=$X,$Y", "--user-data-dir=$profile")
