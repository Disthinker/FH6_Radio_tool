@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ============================================================
echo  FH6 Radio Tool - Launcher
echo ============================================================
echo.

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m fh6_radio_tool.app
) else (
  echo [WARN] .venv was not found.
  echo Trying system Python...
  python -m fh6_radio_tool.app
)

if errorlevel 1 (
  echo.
  echo [ERROR] Failed to start the tool.
  echo Please run the setup batch file first.
  echo.
)

pause
