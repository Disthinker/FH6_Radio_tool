@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo [FH6 Radio Tool] Uninstall / cleanup
echo Project folder:
echo   %CD%
echo.
echo This will remove:
echo   .venv
echo   Python cache folders
echo.
set /p CONFIRM=Continue? Type Y then press Enter: 
if /I not "%CONFIRM%"=="Y" (
  echo Cancelled.
  pause
  exit /b 0
)

echo.
echo [1/3] Closing possible Python processes started from this folder...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root=(Resolve-Path '.').Path; Get-CimInstance Win32_Process -Filter \"name='python.exe' or name='pythonw.exe'\" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($root) } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; Write-Host ('Stopped PID ' + $_.ProcessId) } catch {} }" 2>nul

echo.
echo [2/3] Removing virtual environment and caches...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='SilentlyContinue'; if(Test-Path '.venv'){Remove-Item -LiteralPath '.venv' -Recurse -Force}; Get-ChildItem -Path . -Directory -Recurse -Force -Filter '__pycache__' | Remove-Item -Recurse -Force; Get-ChildItem -Path . -File -Recurse -Force -Filter '*.pyc' | Remove-Item -Force"

if exist ".venv" (
  echo [WARN] .venv still exists. Close all running tool windows and run this script again.
) else (
  echo [OK] .venv removed or not present.
)

echo.
set /p CLEAN_DATA=Also remove output, work and backup folders? Type Y then press Enter: 
if /I "%CLEAN_DATA%"=="Y" (
  echo [3/3] Removing output/work/backup...
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='SilentlyContinue'; foreach($p in @('output','work','backup')){ if(Test-Path $p){Remove-Item -LiteralPath $p -Recurse -Force} }"
) else (
  echo [3/3] Keeping output/work/backup.
)

echo.
echo Cleanup finished.
echo If .venv was not removed, restart Windows or close Python/Qt processes, then run this script again.
pause
