@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0\.."
set "PY_EXE=python"
if exist ".venv\Scripts\python.exe" set "PY_EXE=.venv\Scripts\python.exe"
echo.
echo Installing pywinauto for Fmod Bank Tools GUI auto-click...
%PY_EXE% -m pip install pywinauto -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
if errorlevel 1 (
  echo [WARN] Tsinghua mirror failed. Trying official PyPI...
  %PY_EXE% -m pip install pywinauto
)
echo.
echo Done. If the install failed, please rerun setup_env.bat or check network/proxy settings.
pause
