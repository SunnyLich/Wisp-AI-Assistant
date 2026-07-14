@echo off
setlocal
set "WISP_EXE=%~1"
if "%WISP_EXE%"=="" set "WISP_EXE=%~dp0Wisp.exe"

if not exist "%WISP_EXE%" (
  echo Wisp.exe was not found.
  echo.
  echo Copy this CMD and test_released_speech.ps1 beside the released Wisp.exe,
  echo or drag Wisp.exe onto this CMD file.
  echo.
  pause
  exit /b 2
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0test_released_speech.ps1" -WispExe "%WISP_EXE%" -NoPause
set "RESULT=%ERRORLEVEL%"
echo.
echo Diagnostic exit code: %RESULT%
pause
exit /b %RESULT%
