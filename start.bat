@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".\venv\Scripts\python.exe" (
    .\venv\Scripts\python.exe run_server.py
) else (
    python run_server.py
)
