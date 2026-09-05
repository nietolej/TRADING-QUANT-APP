"""
Tests unitarios para el proveedor de Opciones y el motor TWAP de ejecución algorítmica.
"""
import pytest
from unittest.mock import patch, MagicMock
from data_layer.data_sources.binance_options_provider import BinanceOptionsProvider
from execution_engine.algo_execution_engine import AlgoExecutionTask, AlgoExecutionEngine


def test_parse_option_symbol():
    parsed_call = BinanceOptionsProvider.parse_option_symbol("BTC-260925-145000-C")
    assert parsed_call is not None
    assert parsed_call["underlying"] == "BTC"
    assert parsed_call["expiry_code"] == "260925"
    assert parsed_call["strike"] == 145000.0
    assert parsed_call["option_type"] == "CALL"

    parsed_put = BinanceOptionsProvider.parse_option_symbol("ETH-261230-2800-P")
    assert parsed_put is not None
    assert parsed_put["underlying"] == "ETH"
    assert parsed_put["strike"] == 2800.0
    assert parsed_put["option_type"] == "PUT"

    invalid = BinanceOptionsProvider.parse_option_symbol("INVALID_SYMBOL")
    assert invalid is None


def test_algo_execution_task_metrics():
    task = AlgoExecutionTask(
        task_id="TEST_1",
        algo_type="TWAP",
        symbol="BTCUSDT",
        side="BUY",
        total_quantity=0.1,
        duration_minutes=5.0,
        num_slices=5
    )
    assert task.progress_pct == 0.0

    task.arrival_price = 80000.0
    task.executed_quantity = 0.05
    task.executed_vwap = 80040.0
    assert task.progress_pct == 50.0
    # Slippage: (80040 - 80000)/80000 * 10000 = 5 bps
    assert task.slippage_bps == pytest.approx(5.0)

    task.cancel()
    assert task.status == "CANCELLED"
