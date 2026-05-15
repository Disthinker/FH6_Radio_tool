@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo [FH6 Radio Tool] Setup local virtual environment: .venv
echo This installer uses China PyPI mirrors by default.
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m venv .venv
) else (
  python -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Failed to create .venv.
  echo Please install Python 3.10+ first, then run this script again.
  pause
  exit /b 1
)

set PY_EXE=.venv\Scripts\python.exe
set PIP_EXE=.venv\Scripts\python.exe -m pip

set MIRROR_TUNA=https://pypi.tuna.tsinghua.edu.cn/simple
set MIRROR_ALI=https://mirrors.aliyun.com/pypi/simple
set MIRROR_PYPI=https://pypi.org/simple

echo.
echo [1/3] Upgrade pip with Tsinghua mirror...
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
  echo [ERROR] Failed to upgrade pip.
  echo Check your network, Python installation, or proxy settings.
  pause
  exit /b 1
)

echo.
echo [2/3] Install dependencies with Tsinghua mirror...
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
  echo [ERROR] Failed to install dependencies.
  echo You can retry later, or run:
  echo   .venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  pause
  exit /b 1
)

echo.
echo [3/3] Quick import check...
%PY_EXE% -c "import PySide6; print('PySide6 OK')"
if errorlevel 1 (
  echo [WARN] PySide6 import check failed. The tool may not start correctly.
  echo Try running this installer again.
  pause
  exit /b 1
)

echo.
echo [OK] Setup finished.
echo Run the tool with: 启动工具.bat
pause
