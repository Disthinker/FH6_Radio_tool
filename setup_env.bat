@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ============================================================
echo  FH6 Radio Tool v2.6.2 - Environment Setup
echo ============================================================
echo.
echo This script will create a local virtual environment: .venv
echo Dependencies will be installed into .venv only.
echo Your global Python environment will not be modified.
echo.
echo Fmod Bank Tools one-click automation uses pywinauto on Windows.
echo pywinauto is installed automatically through requirements.txt.
echo.
echo PyPI mirrors will be tried in this order:
echo   1. Tsinghua mirror
echo   2. Aliyun mirror
echo   3. Official PyPI
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m venv .venv
) else (
  python -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo [ERROR] Failed to create .venv.
  echo Please install Python 3.10+ first, then run this script again.
  echo.
  pause
  exit /b 1
)

set "PY_EXE=.venv\Scripts\python.exe"
set "PIP_EXE=.venv\Scripts\python.exe -m pip"

set "MIRROR_TUNA=https://pypi.tuna.tsinghua.edu.cn/simple"
set "MIRROR_ALI=https://mirrors.aliyun.com/pypi/simple"
set "MIRROR_PYPI=https://pypi.org/simple"

echo.
echo [1/3] Upgrading pip...
%PIP_EXE% install --upgrade pip -i %MIRROR_TUNA% --trusted-host pypi.tuna.tsinghua.edu.cn
if errorlevel 1 (
  echo [WARN] Tsinghua mirror failed. Trying Aliyun mirror...
  %PIP_EXE% install --upgrade pip -i %MIRROR_ALI% --trusted-host mirrors.aliyun.com
)
if errorlevel 1 (
  echo [WARN] Aliyun mirror failed. Trying official PyPI...
  %PIP_EXE% install --upgrade pip -i %MIRROR_PYPI%
)
if errorlevel 1 (
  echo.
  echo [ERROR] Failed to upgrade pip.
  echo Please check your network, Python installation, or proxy settings.
  echo.
  pause
  exit /b 1
)

echo.
echo [2/3] Installing dependencies...
%PIP_EXE% install -r requirements.txt -i %MIRROR_TUNA% --trusted-host pypi.tuna.tsinghua.edu.cn
if errorlevel 1 (
  echo [WARN] Tsinghua mirror failed. Trying Aliyun mirror...
  %PIP_EXE% install -r requirements.txt -i %MIRROR_ALI% --trusted-host mirrors.aliyun.com
)
if errorlevel 1 (
  echo [WARN] Aliyun mirror failed. Trying official PyPI...
  %PIP_EXE% install -r requirements.txt -i %MIRROR_PYPI%
)
if errorlevel 1 (
  echo.
  echo [ERROR] Failed to install dependencies.
  echo You can retry later, or manually run:
  echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

echo.
echo [3/3] Quick import check...
%PY_EXE% -c "import PySide6; print('PySide6 OK')"
if errorlevel 1 (
  echo.
  echo [ERROR] PySide6 import check failed.
  echo Please run this setup script again, or check your Python environment.
  echo.
  pause
  exit /b 1
)
%PY_EXE% -c "import platform, importlib; print('pywinauto check:', 'SKIP non-Windows' if platform.system()!='Windows' else ('OK' if importlib.import_module('pywinauto') else 'OK'))"
if errorlevel 1 (
  echo.
  echo [WARN] pywinauto import check failed. The main tool can still run,
  echo but Fmod GUI auto-click may require manual installation:
  echo   .venv\Scripts\python.exe -m pip install pywinauto
  echo.
)

echo.
echo ============================================================
echo  Setup finished. Run run_tool.bat to start FH6 Radio Tool.
echo ============================================================
echo.
pause
