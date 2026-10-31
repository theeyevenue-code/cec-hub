@echo off
rem Send guide TEST COPY: this Hub on 127.0.0.1:5699 with FAKE patients.
rem   CHECKIN_FIXTURE=1 -> patients come from the agent's checkin\fixtures (ZZTEST people),
rem   never Optomate, and the guide helper never touches Gmail (a pretend draft id comes back).
rem   Its send history is a separate TEST file. The live Hub on 5680 is untouched.
set CEC_HUB_PORT=5699
set CEC_HUB_HOST=127.0.0.1
set CHECKIN_FIXTURE=1
set CEC_HUB_INTEGRATIONS=%~dp0config\integrations.sendguide-test.json
cd /d "%~dp0"
echo.
echo   Send guide test page: http://127.0.0.1:5699/#/sendguide
echo.
python app.py
