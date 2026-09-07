"""
Test Suite para la Fase 1: Desacoplamiento de Procesos (Trading Daemon vs. Web GUI).
Valida:
1. Concurrencia en SQLite con modo WAL (lecturas y escrituras simultáneas sin bloqueos).
2. Endpoints de la API del Trading Daemon (FastAPI TestClient).
3. Cliente de abstracción DaemonClient y proxies BotProxy.
"""

import os
import time
import threading
import pytest
from datetime import datetime
from fastapi.testclient import TestClient

from data_layer.storage import SessionLocal, PaperTrade, init_db, engine
from execution_engine.bot_daemon import app
from execution_engine.daemon_client import DaemonClient, BotProxy, PositionProxy


@pytest.fixture(scope="module")
def setup_database():
    """Inicializa la base de datos con modo WAL."""
    init_db()
    yield


def test_sqlite_wal_mode_concurrency(setup_database):
    """
    Verifica que múltiples hilos puedan escribir y leer de SQLite concurrentemente
    sin generar excepciones de 'database is locked', gracias a WAL mode.
    """
    write_errors = []
    read_errors = []
    num_writes = 30
    num_reads = 60

    def writer_worker(worker_id: int):
        for i in range(num_writes):
            try:
                db = SessionLocal()
                trade = PaperTrade(
                    session_id=f"test_session_{worker_id}",
                    symbol="BTC/USDT",
                    strategy_name="test_strat",
                    side="long",
                    entry_time=datetime.now(),
                    entry_price=60000.0 + i,
                    pnl=10.0 * i,
                    reason="TEST_WAL"
                )
                db.add(trade)
                db.commit()
                db.close()
                time.sleep(0.005)
            except Exception as e:
                write_errors.append(f"Writer {worker_id} error: {e}")

    def reader_worker(worker_id: int):
        for _ in range(num_reads):
            try:
                db = SessionLocal()
                count = db.query(PaperTrade).filter(PaperTrade.symbol == "BTC/USDT").count()
                assert count >= 0
                db.close()
                time.sleep(0.003)
            except Exception as e:
                read_errors.append(f"Reader {worker_id} error: {e}")

    threads = []
    # 3 hilos de escritura y 4 hilos de lectura simultáneos
    for i in range(3):
        threads.append(threading.Thread(target=writer_worker, args=(i,)))
    for i in range(4):
        threads.append(threading.Thread(target=reader_worker, args=(i,)))

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Limpiar registros creados por el test
    db_clean = SessionLocal()
    db_clean.query(PaperTrade).filter(PaperTrade.reason == "TEST_WAL").delete()
    db_clean.commit()
    db_clean.close()

    assert len(write_errors) == 0, f"Errores de escritura detectados: {write_errors}"
    assert len(read_errors) == 0, f"Errores de lectura detectados: {read_errors}"


def test_trading_daemon_api_endpoints():
    """Valida los endpoints REST fundamentales del Trading Daemon."""
    client = TestClient(app)

    # 1. Health check
    res_health = client.get("/health")
    assert res_health.status_code == 200
    data_health = res_health.json()
    assert data_health.get("status") == "online"
    assert data_health.get("service") == "trading-daemon-core"
    assert "pid" in data_health

    # 2. Status completo del sistema
    res_status = client.get("/api/status")
    assert res_status.status_code == 200
    data_status = res_status.json()
    assert data_status.get("status") == "online"
    assert "memory_mb" in data_status
    assert "total_bots" in data_status
    assert "running_bots" in data_status
    assert "security_config" in data_status

    # 3. Listado de bots
    res_bots = client.get("/api/bots")
    assert res_bots.status_code == 200
    assert isinstance(res_bots.json(), list)

    # 4. Resumen de portafolio
    res_summary = client.get("/api/portfolio_summary")
    assert res_summary.status_code == 200
    data_summary = res_summary.json()
    assert "total_bots" in data_summary
    assert "balance_display" in data_summary
    assert "pnl_display" in data_summary

    # 5. Órdenes no ejecutadas
    res_unexec = client.get("/api/unexecuted_orders")
    assert res_unexec.status_code == 200
    assert isinstance(res_unexec.json(), list)


def test_bot_proxy_interface():
    """Valida que BotProxy exponga los atributos y métodos requeridos por la Web GUI."""
    mock_client = DaemonClient()
    mock_data = {
        "bot_id": "test_bot_99",
        "name": "Bot Test 99",
        "strategy_yaml_path": "config/strategies/test.yaml",
        "symbol": "ETH/USDT",
        "timeframe": "15m",
        "initial_balance": 5.0,
        "current_balance": 5.25,
        "currency": "ETH",
        "use_testnet": True,
        "status": "STOPPED",
        "status_message": "Detenido",
        "is_running": False,
        "custom_parameters": {"fast_ema": 9, "slow_ema": 21},
        "order_types": {"entry": "MARKET", "exit": "LIMIT"},
        "trade_history": [],
        "log_lines": ["Log 1", "Log 2"],
        "unexecuted_orders": [],
        "stats": {"total_pnl": 0.25, "wins": 1},
        "position": {
            "side": "long",
            "entry_price": 3000.0,
            "quantity": 1.5,
            "entry_timestamp": "2026-09-06T10:00:00",
            "sl_price": 2900.0,
            "tp_price": 3200.0
        }
    }

    proxy = BotProxy(mock_data, mock_client)

    assert proxy.bot_id == "test_bot_99"
    assert proxy.name == "Bot Test 99"
    assert proxy.symbol == "ETH/USDT"
    assert proxy.timeframe == "15m"
    assert proxy.initial_balance == 5.0
    assert proxy.current_balance == 5.25
    assert proxy.currency == "ETH"
    assert proxy.is_running is False
    assert proxy.position is not None
    assert proxy.position.side == "long"
    assert proxy.position.entry_price == 3000.0
    assert proxy.position.quantity == 1.5
    assert proxy.position.sl_price == 2900.0
    assert proxy.position.tp_price == 3200.0
    assert proxy.custom_parameters["fast_ema"] == 9
