@echo off
echo Iniciando Servidor Trading Quant (FastAPI + NiceGUI)...
start http://127.0.0.1:8000
.\venv\Scripts\uvicorn.exe api.main:app --reload --port 8000
pause
