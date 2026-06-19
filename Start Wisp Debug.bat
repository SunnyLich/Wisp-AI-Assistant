@echo off
REM Wisp debug launcher - keeps timestamped runtime logs under build_logs.
setlocal
cd /d "%~dp0"
set "WISP_RUNTIME_LOG_MODE=debug"
call "Start Wisp.bat"
