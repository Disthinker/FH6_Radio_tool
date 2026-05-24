@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ============================================================
echo  FH6 Radio Tool v2.7.13 - Launcher
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [WARN] .venv was not found. Please run setup_env.bat first.
  echo Trying system Python...
  where py >nul 2>nul
  if %errorlevel%==0 (
    py -3 -m fh6_radio_tool.app
  ) else (
    python -m fh6_radio_tool.app
  )
) else (
  ".venv\Scripts\python.exe" -m fh6_radio_tool.app
)

if errorlevel 1 (
  echo.
  echo [ERROR] Failed to start FH6 Radio Tool v2.7.13.
  echo If this is a dependency error, run setup_env.bat first, then retry. Otherwise, please report the traceback above.
  echo.
)

pause
