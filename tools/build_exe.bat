@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "ROOT_DIR=%SCRIPT_DIR%.."
set "LOG_DIR=%ROOT_DIR%\build_logs"
set "LOG_FILE=%LOG_DIR%\build_exe.log"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo Writing build log to:
echo   %LOG_FILE%
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%build_exe.ps1" -Clean %* > "%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

type "%LOG_FILE%"

echo.
if "%EXIT_CODE%"=="0" (
    echo Build finished successfully.
) else (
    echo Build failed with exit code %EXIT_CODE%.
    echo Check the log above:
    echo   %LOG_FILE%
)
echo.
pause

endlocal
