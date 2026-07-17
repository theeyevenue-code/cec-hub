' Runs HUB-WATCHDOG.ps1 with no window at all — same trick as "CEC Hub.vbs".
' The Scheduled Task points here rather than straight at powershell.exe, so the
' 5-minutely check never flashes a console at whoever is on the front desk.
Dim shell, here
Set shell = CreateObject("WScript.Shell")
here = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
shell.CurrentDirectory = here
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & here & "HUB-WATCHDOG.ps1""", 0, False
