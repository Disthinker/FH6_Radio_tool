@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ============================================================
echo  FH6 Radio Tool - Recommended PyInstaller Release Builder
echo ============================================================
echo.
echo Output:
echo   dist_release\FH6_Radio_Tool_v*_nexus_exe_portable.zip
echo.
echo Notes:
echo   - This is the recommended builder for v3.1.3.
echo   - Run this on native Windows, not WSL/Linux.
echo   - Fmod Bank Tools is NOT bundled. Players select their own exe.
echo   - The final ZIP contains no loose .ico, source folder, or nested archive.
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

echo [2/5] Installing / updating PyInstaller build dependencies ...
"%PY%" -m pip install -U pip
"%PY%" -m pip install -U -r requirements.txt
"%PY%" -m pip install -U pyinstaller pyinstaller-hooks-contrib pywinauto pywin32 comtypes six
if errorlevel 1 (
  echo [ERROR] Failed to install build dependencies.
  pause
  exit /b 1
)

echo [3/5] Building PyInstaller EXE portable package ...
"%PY%" scripts\build_exe_release.py
if errorlevel 1 (
  echo.
  echo [ERROR] PyInstaller package build failed.
  echo Common fixes:
  echo   1. Close any running FH6RadioTool.exe.
  echo   2. Put the project in a short local path, e.g. C:\FH6Build.
  echo   3. If antivirus blocks the build, add the project folder to trusted locations.
  echo   4. If pywinauto reports Typelib different than module, delete .venv and run this script again.
  pause
  exit /b 1
)

echo [4/5] Build completed.
echo.
echo [5/5] Result:
echo   dist_release\FH6_Radio_Tool_v*_nexus_exe_portable.zip
echo.
echo Extract that ZIP and double-click FH6RadioTool.exe to test.
echo.
pause
