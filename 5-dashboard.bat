@echo off
chcp 65001 >nul
title Wallex bot - dashboard
rem Builds dashboard\live_main.html from runs\live_main of THIS folder and opens it. Works from the repo root
rem (double-click, or call it from anywhere: it changes to its own folder first). Read-only towards runs:
rem the live bot keeps writing there; the report only reads it and writes nothing inside runs.
rem dashboard\live_main.html is gitignored (not tracked), so rebuilding it never leaves a modified tracked file
rem in this checkout. The frozen copy kept in git is dashboard\snapshot_live_main.html; this file never touches it.
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
rem a worktree next to the main checkout has no .venv of its own: borrow the main checkout's
if not exist "%PY%" if exist "..\wallex-bot\.venv\Scripts\python.exe" set "PY=..\wallex-bot\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
if not exist "runs\live_main\decisions.jsonl" (
  echo No runs\live_main\decisions.jsonl next to this file - nothing to show yet.
  pause
  exit /b 1
)
echo Building dashboard\live_main.html from runs\live_main ...
echo Read-only towards runs: nothing is written, moved or deleted in runs\live_main.
"%PY%" -m wallexbot.report "runs\live_main" --out "dashboard\live_main.html"
if errorlevel 1 (
  echo Could not build the dashboard. runs\live_main was not touched.
  pause
  exit /b 1
)
echo Done: %CD%\dashboard\live_main.html
start "" "dashboard\live_main.html"
