@echo off
setlocal
cd /d "%~dp0"

rem This launcher intentionally delegates all validation and removal to Wisp's
rem shared uninstaller. It must not contain its own deletion commands.
if exist "%~dp0Wisp.exe" (
    start "" "%~dp0Wisp.exe" -m runtime.workers.uninstall_wisp
    exit /b 0
)

if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" -m runtime.workers.uninstall_wisp
    exit /b 0
)

if exist "%~dp0.venv-build\Scripts\pythonw.exe" (
    start "" "%~dp0.venv-build\Scripts\pythonw.exe" -m runtime.workers.uninstall_wisp
    exit /b 0
)

echo ERROR: Wisp.exe or a Wisp Python environment was not found beside this file.
echo Keep Uninstall Wisp.bat in the Wisp folder, then try again.
pause
exit /b 1
