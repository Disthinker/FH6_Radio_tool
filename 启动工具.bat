@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m fh6_radio_tool.app
) else (
  echo [WARN] .venv not found. Trying system Python.
  python -m fh6_radio_tool.app
)

if errorlevel 1 (
  echo.
  echo [ERROR] Failed to start. Run 安装环境.bat first.
)
pause
