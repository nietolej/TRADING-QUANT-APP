@echo off
chcp 65001 >nul
title Trading Daemon Core (Puerto 8001)
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
if exist ".\venv\Scripts\python.exe" (
    .\venv\Scripts\python.exe run_bot_daemon.py
) else (
    python run_bot_daemon.py
)
pause
