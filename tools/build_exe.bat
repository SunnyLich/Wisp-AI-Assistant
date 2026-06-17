@echo off
REM Windows wrapper that launches the PowerShell executable build script.
setlocal

set "SCRIPT_DIR=%~dp0"

rem Run the build directly so all progress (pip, PyInstaller) streams live to this
rem window. No redirect, so %ERRORLEVEL% reflects the real build result.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%build_exe.ps1" -Clean %*
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo Build finished successfully.
) else (
    echo Build failed with exit code %EXIT_CODE%.
    echo Scroll up to see what went wrong.
)
echo.
pause

endlocal
