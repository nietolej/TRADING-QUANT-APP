@echo off
chcp 65001 >nul
title Trading Quant App - Suite Completa
cd /d "%~dp0"
echo ====================================================
echo   Iniciando Suite Desacoplada de Trading Quant App
echo ====================================================
echo.
echo [1/2] Verificando/levantando el Trading Daemon Core (Puerto 8001)...
call ensure_daemon.bat

echo [2/2] Levantando Servidor Web NiceGUI (Puerto 8000)...
call start.bat
