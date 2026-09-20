@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
set DEV_MODE=1
call ensure_daemon.bat
echo Iniciando en MODO DESARROLLADOR (Auto-recarga activada)...
if exist ".\venv\Scripts\python.exe" (
    .\venv\Scripts\python.exe run_server.py
) else (
    python run_server.py
)
