@echo off
rem Second copy of the Hub on port 5699 for testing a branch — the live Hub on 5680 is untouched.
set CEC_HUB_PORT=5699
set CEC_HUB_HOST=127.0.0.1
cd /d "%~dp0"
python app.py
