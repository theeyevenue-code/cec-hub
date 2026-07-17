@echo off
REM Double-click this ONCE on the machine that runs the Hub.
REM
REM It adds a Scheduled Task ("CEC Hub Watchdog") that checks every 5 minutes
REM and restarts the Hub if it has stopped. Without it, a Hub that dies mid-day
REM stays dead until someone notices, because the Startup shortcut only runs
REM at logon. No admin needed.
setlocal
set "DIR=%~dp0"
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"

echo Installing the CEC Hub Watchdog (checks every 5 minutes)...
schtasks /Create /TN "CEC Hub Watchdog" /TR "wscript.exe \"%DIR%\HUB-WATCHDOG.vbs\"" /SC MINUTE /MO 5 /F
if errorlevel 1 (
    echo.
    echo That did not work. Try right-clicking this file and choosing
    echo "Run as administrator".
) else (
    echo.
    echo Done. The Hub will now restart itself within 5 minutes if it stops.
    echo Restarts are logged in watchdog.log next to this file.
)
echo.
pause
