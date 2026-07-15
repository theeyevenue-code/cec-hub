@echo off
REM Starts the CEC Hub with no visible window (used by the Startup shortcut).
REM To stop it: Task Manager -> pythonw.exe -> End task, or just restart the PC.
cd /d "%~dp0"
start "" "%LocalAppData%\Programs\Python\Python312\pythonw.exe" app.py
