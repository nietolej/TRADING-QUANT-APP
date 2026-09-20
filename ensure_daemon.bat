@echo off
rem Garantiza que el Trading Daemon (puerto 8001) este corriendo antes de levantar el servidor web.
rem Los bots viven SOLO en el daemon: sin el, la web no muestra ni opera bots.
rem Se lanza sin auto-recarga a proposito: reiniciarlo con cada guardado de codigo reiniciaria los bots en vivo.
rem Para aplicar cambios de codigo del motor de ejecucion, reinicia su ventana (Ctrl+C y start_bot_daemon.bat).
cd /d "%~dp0"

netstat -ano | findstr /R /C:":8001 .*LISTENING" >nul
if %errorlevel%==0 (
    echo [daemon] Trading Daemon ya esta corriendo en el puerto 8001.
    exit /b 0
)

echo [daemon] Trading Daemon apagado: levantandolo en una ventana aparte...
start "Trading Daemon Core (Puerto 8001)" cmd /k "%~dp0start_bot_daemon.bat"

for /L %%i in (1,1,25) do (
    powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:8001/health -TimeoutSec 1; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
    if not errorlevel 1 (
        echo [daemon] Listo.
        exit /b 0
    )
    timeout /t 1 /nobreak >nul
)
echo [daemon] ADVERTENCIA: el daemon no respondio en 25 s; revisa su ventana.
exit /b 0
