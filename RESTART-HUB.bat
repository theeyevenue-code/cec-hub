@echo off
REM ============================================================
REM   Restart the CEC Hub  -  safe one-click.
REM
REM   Double-click this if the Hub is acting up, won't load, or
REM   after Mark/Claude has updated it. It stops the Hub cleanly
REM   and the watchdog starts a fresh copy within a few seconds.
REM
REM   Nothing here can hurt anything - it only ever touches the
REM   Hub. Safe to double-click any time.
REM ============================================================
title Restart CEC Hub
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0RESTART-HUB.ps1"
echo.
pause
