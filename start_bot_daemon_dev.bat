@echo off
chcp 65001 >nul
title Trading Daemon Core (Puerto 8001) - DEV MODE
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
set DEV_MODE=1
echo Iniciando Trading Daemon en MODO DESARROLLADOR (Auto-recarga activada)...
if exist ".\venv\Scripts\python.exe" (
    .\venv\Scripts\python.exe run_bot_daemon.py
) else (
    python run_bot_daemon.py
)
pause
