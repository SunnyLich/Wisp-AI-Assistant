@echo off
REM Wisp - double-click to start.
REM Sets up or repairs the local environment as needed (missing, wrong Python
REM version, or half-installed), then launches.
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "WANT=3.12.13"
if exist ".python-version" ( set /p WANT=<.python-version )
for /f "tokens=1,2 delims=." %%a in ("!WANT!") do set "WANT_MM=%%a.%%b"

REM (Re)build the venv if missing or built on the wrong Python version.
set "REBUILD=0"
if not exist ".venv\Scripts\python.exe" (
  set "REBUILD=1"
) else (
  for /f %%v in ('".venv\Scripts\python.exe" -c "import sys;print(str(sys.version_info[0])+chr(46)+str(sys.version_info[1]))" 2^>nul') do set "HAVE=%%v"
  if not "!HAVE!"=="!WANT_MM!" (
    echo Existing environment is Python !HAVE!; Wisp needs !WANT_MM! - rebuilding.
    rmdir /s /q .venv
    set "REBUILD=1"
  )
)

if "!REBUILD!"=="1" (
  set "PYTHON="
  where py >nul 2>nul && ( py -!WANT_MM! --version >nul 2>nul && set "PYTHON=py -!WANT_MM!" )
  if not defined PYTHON ( where python >nul 2>nul && set "PYTHON=python" )
  if not defined PYTHON (
    echo ERROR: No Python found. Install Python !WANT! from python.org, then try again.
    pause
    exit /b 1
  )
  echo Setting up Wisp with !PYTHON! ...
  !PYTHON! -m venv .venv
  if errorlevel 1 ( echo Failed to create .venv & pause & exit /b 1 )
)

REM Ensure dependencies are present (covers a half-installed venv).
".venv\Scripts\python.exe" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
  echo Installing dependencies (this takes a minute)...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 ( echo Dependency install failed. & pause & exit /b 1 )
  echo Setup complete - starting Wisp.
)

".venv\Scripts\python.exe" main.py
