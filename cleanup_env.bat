@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ============================================================
echo  FH6 Radio Tool - Uninstall / Cleanup
echo ============================================================
echo.
echo Project folder:
echo   %CD%
echo.
echo This script can remove:
echo   .venv
echo   Python cache folders
echo   output / work / backup folders, optional
echo.

set /p CONFIRM=Continue cleanup? Type Y then press Enter: 
if /I not "%CONFIRM%"=="Y" (
  echo.
  echo Cancelled.
  pause
  exit /b 0
)

echo.
echo [1/3] Close possible Python processes started from this folder...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root=(Resolve-Path '.').Path; Get-CimInstance Win32_Process -Filter \"name='python.exe' or name='pythonw.exe'\" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($root) } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; Write-Host ('Stopped PID ' + $_.ProcessId) } catch {} }" 2>nul

echo.
echo [2/3] Remove virtual environment and Python caches...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='SilentlyContinue'; if(Test-Path '.venv'){Remove-Item -LiteralPath '.venv' -Recurse -Force}; Get-ChildItem -Path . -Directory -Recurse -Force -Filter '__pycache__' | Remove-Item -Recurse -Force; Get-ChildItem -Path . -File -Recurse -Force -Filter '*.pyc' | Remove-Item -Force"

if exist ".venv" (
  echo.
  echo [WARN] .venv still exists.
  echo Close all tool windows and Python processes, then run this script again.
) else (
  echo [OK] .venv removed or not present.
)

echo.
set /p CLEAN_DATA=Also remove output, work and backup folders? Type Y then press Enter: 
if /I "%CLEAN_DATA%"=="Y" (
  echo.
  echo [3/3] Remove output / work / backup...
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='SilentlyContinue'; foreach($p in @('output','work','backup')){ if(Test-Path $p){Remove-Item -LiteralPath $p -Recurse -Force} }"
) else (
  echo.
  echo [3/3] Keep output / work / backup.
)

echo.
echo ============================================================
echo  Cleanup finished.
echo ============================================================
echo.
pause
