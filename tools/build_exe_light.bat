@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "ROOT_DIR=%SCRIPT_DIR%.."
set "LOG_DIR=%ROOT_DIR%\build_logs"
set "LOG_FILE=%LOG_DIR%\build_exe_light.log"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo Writing light build log to:
echo   %LOG_FILE%
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%build_exe.ps1" -Clean -Lite %* > "%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

type "%LOG_FILE%"

echo.
if "%EXIT_CODE%"=="0" (
    echo Light build finished successfully.
) else (
    echo Light build failed with exit code %EXIT_CODE%.
    echo Check the log above:
    echo   %LOG_FILE%
)
echo.
pause

endlocal
