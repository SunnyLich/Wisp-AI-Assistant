@echo off
REM Wisp - double-click to start.
REM The first time, this installs everything Wisp needs (a local .venv built
REM from requirements.txt). After that, it just launches the app.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo First run - setting up Wisp ^(this takes a minute^)...

  REM Use Python 3.12 (pinned in .python-version).
  set "PYTHON="
  where py >nul 2>nul && ( py -3.12 --version >nul 2>nul && set "PYTHON=py -3.12" )
  if not defined PYTHON ( where python >nul 2>nul && set "PYTHON=python" )
  if not defined PYTHON (
    echo ERROR: No Python found. Install Python 3.12.13 from python.org
    echo        ^(check "Add python.exe to PATH"^), then double-click again.
    pause
    exit /b 1
  )

  !PYTHON! -m venv .venv
  if errorlevel 1 ( echo Failed to create .venv & pause & exit /b 1 )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 ( echo Dependency install failed. & pause & exit /b 1 )
  echo Setup complete - starting Wisp.
)

".venv\Scripts\python.exe" main.py
