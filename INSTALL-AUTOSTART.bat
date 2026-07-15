@echo off
REM Double-click once (no admin needed). Sets the CEC Hub to start by itself,
REM hidden, every time this PC logs in - same pattern as the referral tool's
REM "CEC Referral Tool.vbs". To undo: delete "CEC Hub.vbs" from the folder
REM that opens when you press Win+R and type: shell:startup
set VBS=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\CEC Hub.vbs
> "%VBS%" echo Set s = CreateObject("WScript.Shell")
>> "%VBS%" echo s.Run """%~dp0RUN-HUB-HIDDEN.bat""", 0, False
if errorlevel 1 (
    echo Something went wrong - ask Claude.
) else (
    echo Done. The Hub will start automatically from the next login onwards.
)
echo.
pause
