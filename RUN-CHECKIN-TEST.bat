@echo off
rem Check-in TEST COPY: this worktree's Hub on 127.0.0.1:5699 with FAKE patients.
rem   CHECKIN_FIXTURE=1  -> the engine reads checkin\fixtures (ZZTEST people), never Optomate.
rem   The engine is in dry run (no CHECKIN_* gate keys in its .env) -> nothing is written.
rem   The live Hub on 5680 is untouched.
set CEC_HUB_PORT=5699
set CEC_HUB_HOST=127.0.0.1
set CHECKIN_FIXTURE=1
set CEC_HUB_INTEGRATIONS=%~dp0config\integrations.checkin-test.json
cd /d "%~dp0"
echo.
echo   Staff page: http://127.0.0.1:5699/#/checkin
echo   iPad page:  http://127.0.0.1:5699/checkin/ipad#zztest-ipad-key-not-for-real-use
echo.
python app.py
