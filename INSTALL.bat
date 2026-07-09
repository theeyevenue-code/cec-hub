@echo off
echo ============================================
echo  Concord Eyecare - CEC Hub - Install
echo ============================================
echo.
cd /d "%~dp0"
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Something went wrong. Is Python installed?
    echo Ask Mark to install Python from python.org first.
) else (
    echo.
    echo Done. You can now double-click START.bat to open the Hub.
)
echo.
pause
