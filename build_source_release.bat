@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" call setup_env.bat
"%PY%" scripts\build_portable_release.py
pause
