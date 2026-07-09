@echo off
echo Starting CEC Hub...
echo.
echo Keep this black window open while you use the Hub.
echo Close it when you are finished for the day.
echo.
cd /d "%~dp0"
start "" http://localhost:5680
python app.py
pause
