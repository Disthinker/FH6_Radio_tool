@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ============================================================
echo  FH6 Radio Tool - Nexus-safe Nuitka Onefile Release Builder
echo ============================================================
echo.
echo Output:
echo   dist_release\FH6_Radio_Tool_v*_nexus_nuitka_onefile.zip
echo.
echo Notes:
echo   - Run this on native Windows, not WSL/Linux.
echo   - Fmod Bank Tools is NOT bundled. Players select their own exe.
echo   - The final ZIP contains no loose .ico, source folder, or nested archive.
echo   - First Nuitka build can take a long time.
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

echo [2/5] Installing / updating Nuitka build dependencies ...
"%PY%" -m pip install -U pip
"%PY%" -m pip install -U -r requirements.txt
"%PY%" -m pip install -U nuitka ordered-set zstandard pywinauto pywin32 comtypes six
if errorlevel 1 (
  echo [ERROR] Failed to install build dependencies.
  pause
  exit /b 1
)

echo [3/5] Building Nuitka onefile EXE package ...
"%PY%" scripts\build_nuitka_release.py
if errorlevel 1 (
  echo.
  echo [ERROR] Nuitka package build failed.
  echo Common fixes:
  echo   1. Run on native Windows, not WSL/Linux.
  echo   2. Use a short local path, e.g. C:\FH6Build.
  echo   3. Close any running FH6RadioTool.exe.
  echo   4. If antivirus blocks the build, add the project folder to trusted locations.
  echo   5. Make sure Microsoft C++ Build Tools or a supported compiler is installed.
  echo   6. If Nuitka remains unstable in your VM, run build_pyinstaller_release.bat instead.
  pause
  exit /b 1
)

echo [4/5] Build completed.
echo.
echo [5/5] Result:
echo   dist_release\FH6_Radio_Tool_v*_nexus_nuitka_onefile.zip
echo.
echo Extract that ZIP and double-click FH6RadioTool.exe to test.
echo.
pause
