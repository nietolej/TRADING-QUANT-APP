@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
set DEV_MODE=1

echo Actualizando codigo desde GitHub (git checkout main + git pull origin main)...
git checkout main
git pull origin main
if errorlevel 1 (
    echo.
    echo ADVERTENCIA: git pull fallo ^(revisa el mensaje de arriba - probablemente tienes
    echo cambios locales sin commitear que chocan con los nuevos^). La app va a arrancar
    echo igual, pero puede estar corriendo codigo desactualizado.
    echo.
    pause
)

call ensure_daemon.bat
echo Iniciando en MODO DESARROLLADOR (Auto-recarga activada)...
if exist ".\venv\Scripts\python.exe" (
    .\venv\Scripts\python.exe run_server.py
) else (
    python run_server.py
)
