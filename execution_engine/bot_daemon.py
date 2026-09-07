"""
Trading Daemon Core - Motor de Ejecución Autónomo (24/7).
Proceso headless aislado para ejecución de estrategias cuantitativas en vivo.
Expone una API REST local ligera en http://127.0.0.1:8001 para telemetría y control.
"""

import os
import sys
import time
import logging
import psutil
from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from execution_engine.bot_manager import bot_manager, BotManager
from execution_engine.security_manager import (
    is_real_trading_enabled,
    set_real_trading_enabled,
    load_security_config
)
from notifications.telegram_bot import TelegramNotifier

logger = logging.getLogger("TradingDaemon")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Daemon:%(process)d] %(name)s: %(message)s"
)

START_TIME = time.time()
PID = os.getpid()

app = FastAPI(
    title="Trading Bot Daemon Core",
    description="Headless 24/7 Quantitative Trading Execution Engine",
    version="1.0.0"
)

# Permitir CORS únicamente para peticiones locales del dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8001",
        "http://127.0.0.1:8001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

notifier = TelegramNotifier()


# ── Modelos Pydantic ──

class CreateBotRequest(BaseModel):
    strategy_yaml_path: str
    initial_balance: float = 1.0
    currency: str = "BTC"
    custom_parameters: Optional[Dict[str, Any]] = None
    use_testnet: bool = False
    custom_timeframe: Optional[str] = None
    custom_symbol: Optional[str] = None
    name: Optional[str] = None
    auto_start: bool = False


class ResetBotRequest(BaseModel):
    new_initial_balance: Optional[float] = None


class ClearUnexecutedRequest(BaseModel):
    bot_id: Optional[str] = None


# ── Endpoints de Salud y Telemetría del Sistema ──

@app.get("/health")
def health_check():
    """Chequeo de salud ultraligero para monitorización y watchdog."""
    return {
        "status": "online",
        "service": "trading-daemon-core",
        "pid": PID,
        "uptime_seconds": round(time.time() - START_TIME, 1)
    }


@app.get("/api/status")
def get_system_status():
    """Retorna telemetría completa del sistema, uso de recursos y estado de los bots."""
    try:
        proc = psutil.Process(PID)
        mem_info = proc.memory_info()
        mem_mb = round(mem_info.rss / (1024 * 1024), 2)
        cpu_pct = proc.cpu_percent(interval=None)
    except Exception:
        mem_mb = 0.0
        cpu_pct = 0.0

    all_bots = bot_manager.get_all_bots()
    running_bots = sum(1 for b in all_bots if b.is_running)

    return {
        "status": "online",
        "pid": PID,
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "started_at": datetime.fromtimestamp(START_TIME).isoformat(),
        "memory_mb": mem_mb,
        "cpu_percent": cpu_pct,
        "total_bots": len(all_bots),
        "running_bots": running_bots,
        "real_trading_enabled": is_real_trading_enabled(),
        "security_config": load_security_config()
    }


# ── Endpoints de Bots ──

@app.get("/api/bots")
def list_bots():
    """Retorna la lista de todos los bots serializados con su estado actual."""
    bots = bot_manager.get_all_bots()
    return [b.to_dict() for b in bots]


@app.get("/api/bots/{bot_id}")
def get_bot_detail(bot_id: str):
    """Retorna los datos detallados de un bot específico."""
    bot = bot_manager.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot '{bot_id}' no encontrado.")
    return bot.to_dict()


@app.post("/api/bots")
def create_bot(req: CreateBotRequest):
    """Crea un nuevo bot de trading en el daemon."""
    try:
        new_bot = bot_manager.create_bot(
            strategy_yaml_path=req.strategy_yaml_path,
            initial_balance=req.initial_balance,
            currency=req.currency,
            custom_parameters=req.custom_parameters,
            use_testnet=req.use_testnet,
            timeframe=req.custom_timeframe,
            symbol=req.custom_symbol,
            name=req.name,
        )
        if req.auto_start and not new_bot.is_running:
            new_bot.start()
        logger.info("Bot creado exitosamente en daemon: %s (ID: %s)", new_bot.name, new_bot.bot_id)
        return new_bot.to_dict()
    except Exception as e:
        logger.error("Error al crear bot en daemon: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/bots/{bot_id}")
def delete_bot(bot_id: str):
    """Elimina un bot registrado en el daemon."""
    success = bot_manager.delete_bot(bot_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"No se pudo eliminar el bot '{bot_id}'.")
    return {"status": "deleted", "bot_id": bot_id}


@app.post("/api/bots/{bot_id}/start")
def start_bot(bot_id: str):
    """Inicia la ejecución de un bot de trading."""
    bot = bot_manager.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot '{bot_id}' no encontrado.")
    
    if bot.is_running:
        return {"status": "already_running", "bot_id": bot_id}

    bot.start()
    bot_manager.save_state_to_disk()
    logger.info("Bot iniciado: %s (ID: %s)", bot.name, bot_id)
    return {"status": "started", "bot_id": bot_id}


@app.post("/api/bots/{bot_id}/stop")
def stop_bot(bot_id: str):
    """Detiene la ejecución de un bot de trading."""
    bot = bot_manager.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot '{bot_id}' no encontrado.")

    if not bot.is_running:
        return {"status": "already_stopped", "bot_id": bot_id}

    bot.stop()
    bot_manager.save_state_to_disk()
    logger.info("Bot detenido: %s (ID: %s)", bot.name, bot_id)
    return {"status": "stopped", "bot_id": bot_id}


@app.post("/api/bots/{bot_id}/reset")
def reset_bot(bot_id: str, req: ResetBotRequest):
    """Resetea el balance, estadísticas y estado de un bot."""
    bot = bot_manager.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot '{bot_id}' no encontrado.")

    bot.reset(new_initial_balance=req.new_initial_balance)
    bot_manager.save_state_to_disk()
    logger.info("Bot reseteado: %s (ID: %s)", bot.name, bot_id)
    return {"status": "reset", "bot_id": bot_id}


class UpdateBotConfigRequest(BaseModel):
    name: Optional[str] = None
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    initial_balance: Optional[float] = None
    currency: Optional[str] = None
    use_testnet: Optional[bool] = None
    custom_parameters: Optional[Dict[str, Any]] = None
    strategy_yaml_path: Optional[str] = None
    order_types: Optional[Dict[str, Any]] = None


@app.patch("/api/bots/{bot_id}")
def update_bot_configuration(bot_id: str, req: UpdateBotConfigRequest):
    """Actualiza la configuración o parámetros en caliente de un bot."""
    bot = bot_manager.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot '{bot_id}' no encontrado.")

    bot.update_configuration(
        name=req.name,
        symbol=req.symbol,
        timeframe=req.timeframe,
        initial_balance=req.initial_balance,
        currency=req.currency,
        use_testnet=req.use_testnet,
        custom_parameters=req.custom_parameters,
        strategy_yaml_path=req.strategy_yaml_path,
        order_types=req.order_types
    )
    bot_manager.save_state_to_disk()
    logger.info("Configuración de bot actualizada: %s (ID: %s)", bot.name, bot_id)
    return bot.to_dict()


@app.post("/api/bots/start_all")
def start_all_bots():
    """Inicia todos los bots registrados que estén detenidos."""
    bot_manager.start_all()
    return {"status": "all_started"}


@app.post("/api/bots/stop_all")
def stop_all_bots():
    """Detiene todos los bots que estén corriendo."""
    bot_manager.stop_all()
    return {"status": "all_stopped"}


# ── Endpoints de Portafolio y Órdenes ──

@app.get("/api/portfolio_summary")
def get_portfolio_summary():
    """Retorna las métricas agregadas del portafolio."""
    return bot_manager.get_portfolio_summary()


@app.get("/api/unexecuted_orders")
def get_unexecuted_orders(bot_id: str = "all"):
    """Retorna el historial de órdenes no ejecutadas o rechazadas."""
    return bot_manager.get_unexecuted_orders(bot_id=bot_id)


@app.post("/api/clear_unexecuted_orders")
def clear_unexecuted_orders(req: ClearUnexecutedRequest):
    """Limpia el registro de órdenes no ejecutadas."""
    bot_manager.clear_unexecuted_orders(bot_id=req.bot_id)
    return {"status": "cleared", "target": req.bot_id or "all"}


# ── Parada de Emergencia (Kill Switch) ──

@app.post("/api/emergency_kill")
def emergency_kill_switch():
    """
    BOTÓN DE PÁNICO CUANTITATIVO:
    1. Detiene inmediatamente todos los bots en ejecución.
    2. Cierra el candado de trading real (Modo Solo Lectura forzado).
    3. Envía una alerta crítica a Telegram.
    """
    logger.critical("🚨 ¡KILL SWITCH ACTIVADO DESDE LA API! Deteniendo todos los bots.")
    bot_manager.stop_all()
    set_real_trading_enabled(False)
    
    notifier.send_alert(
        title="🚨 KILL SWITCH ACTIVADO",
        details={
            "Origen": "Trading Daemon API",
            "Acción": "Todos los bots detenidos inmediatamente",
            "Candado Real": "Cerrado (Modo Solo Lectura forzado)",
            "Timestamp": datetime.now().isoformat()
        },
        is_critical=True
    )

    return {
        "status": "emergency_killed",
        "message": "Todos los bots detenidos y candado de trading real cerrado."
    }
