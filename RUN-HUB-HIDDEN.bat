@echo off
REM Starts the CEC Hub with no visible window (used by the Startup shortcut).
REM To stop it: Task Manager -> pythonw.exe -> End task, or just restart the PC.
cd /d "%~dp0"
rem Pass the ABSOLUTE script path. With a bare "app.py" the running process shows
rem only `pythonw.exe app.py` in its command line, which is indistinguishable from
rem every other Python app on this machine — the watchdog then cannot tell its own
rem processes apart and cannot clean up duplicates. (21 Jul 2026: 29 stray Hub
rem copies had accumulated and could only be identified by which one held port 5680.)
start "CEC Hub" "%LocalAppData%\Programs\Python\Python312\pythonw.exe" "%~dp0app.py"
