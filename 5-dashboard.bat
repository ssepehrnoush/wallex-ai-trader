@echo off
chcp 65001 >nul
title Wallex bot - dashboard
rem Builds dashboard\live_main.html from runs\live_main and opens it. Read-only towards runs: nothing is written there.
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
echo Building the dashboard of runs\live_main ...
"%PY%" -m wallexbot.report "runs\live_main" --out "dashboard\live_main.html"
if errorlevel 1 (
  echo Could not build the dashboard.
  pause
  exit /b 1
)
start "" "dashboard\live_main.html"
