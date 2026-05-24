@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

set "APP_VERSION=3.0.37"

echo.
echo ============================================================
echo  FH6 Radio Tool v%APP_VERSION% - PyInstaller EXE Portable Release Builder
echo ============================================================
echo.
echo This script builds a Windows portable EXE package using PyInstaller. For v3.0.37, build_pyinstaller_release.bat is the recommended alias.
echo Output:
echo   dist_release\FH6_Radio_Tool_v%APP_VERSION%_nexus_exe_portable.zip
echo.
echo Notes:
echo   - Fmod Bank Tools is NOT bundled. Players still select their own exe.
echo   - Run this on Windows, not WSL/Linux.
echo   - The first build can take several minutes.
echo   - The output ZIP is checked to avoid nested archives such as base_library.zip.
echo.

if not exist "fh6_radio_tool\app.py" (
  echo [ERROR] Please run this script from the FH6 Radio Tool project root.
  pause
  exit /b 1
)

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [1/5] .venv not found. Running setup_env.bat first ...
  call setup_env.bat
  if errorlevel 1 (
    echo [ERROR] setup_env.bat failed.
    pause
    exit /b 1
  )
)

if not exist "%PY%" (
  echo [ERROR] Python venv still not found: %PY%
  pause
  exit /b 1
)

echo [2/5] Installing / updating EXE build dependencies ...
"%PY%" -m pip install -U pip
"%PY%" -m pip install -U -r requirements.txt
"%PY%" -m pip install -U pyinstaller pyinstaller-hooks-contrib
if errorlevel 1 (
  echo [ERROR] Failed to install build dependencies.
  pause
  exit /b 1
)

echo [3/5] Building EXE portable package ...
"%PY%" scripts\build_exe_release.py
if errorlevel 1 (
  echo.
  echo [ERROR] Nexus-safe EXE package build failed.
  echo Common fixes:
  echo   1. Close any running FH6RadioTool.exe.
  echo   2. Put the project in a short local path, e.g. C:\FH6Build.
  echo   3. If antivirus blocks the build, add the project folder to trusted locations.
  pause
  exit /b 1
)

echo [4/5] Build completed.
echo.
echo [5/5] Result:
echo   dist_release\FH6_Radio_Tool_v%APP_VERSION%_nexus_exe_portable.zip
echo.
echo Extract that ZIP and double-click FH6RadioTool.exe to test.
echo.
pause
