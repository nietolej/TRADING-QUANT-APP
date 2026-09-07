@echo off
chcp 65001 >nul
title Trading Quant App - Suite Completa
cd /d "%~dp0"
echo ====================================================
echo   Iniciando Suite Desacoplada de Trading Quant App
echo ====================================================
echo.
echo [1/2] Levantando Trading Daemon Core en background (Puerto 8001)...
start "Trading Daemon Core (Puerto 8001)" cmd /k "start_bot_daemon.bat"

echo [2/2] Esperando inicializacion del Daemon...
timeout /t 2 /nobreak >nul

echo [3/3] Levantando Servidor Web NiceGUI (Puerto 8000)...
call start.bat
