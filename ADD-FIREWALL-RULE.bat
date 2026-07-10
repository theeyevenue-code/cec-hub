@echo off
REM Right-click this file and choose "Run as administrator" (one time only).
REM It lets other practice PCs open the CEC Hub in their browser
REM (http://Concordeyecare-Server:5680). Same pattern as the referral tool.
echo Adding Windows Firewall rule for the CEC Hub (port 5680)...
netsh advfirewall firewall add rule name="CEC Hub (port 5680)" dir=in action=allow protocol=TCP localport=5680
if errorlevel 1 (
    echo.
    echo That did not work - make sure you right-clicked and chose
    echo "Run as administrator".
) else (
    echo.
    echo Done. Other practice PCs can now reach the Hub.
)
echo.
pause
