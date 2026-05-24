@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0\.."

echo ============================================================
echo  Optional: install PyMusicLooper for better loop candidates
echo ============================================================
echo.

echo This script will try to install PyMusicLooper into the current Python environment.
echo If you use the bundled .venv, run setup_env.bat first.
echo.

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m pip install pymusiclooper
) else (
  python -m pip install pymusiclooper
)

echo.
echo Done. Restart FH6 Radio Tool v2.6.2 after installation.
pause
