"""
Tests para validar la selección de rangos de fechas, persistencia en disco (Parquet)
y exportación a CSV en los módulos de Derivados y Opciones Binance.
"""
import os
import pytest
import pandas as pd
from data_layer.data_sources.binance_derivatives_provider import BinanceDerivativesProvider
from data_layer.data_sources.binance_options_provider import BinanceOptionsProvider


@pytest.fixture
def deriv_provider():
    return BinanceDerivativesProvider()


@pytest.fixture
def options_provider():
    return BinanceOptionsProvider()


def test_derivatives_date_range_and_klines(deriv_provider):
    """Prueba que el dashboard de derivados descargue klines OHLCV y respete el rango de fechas."""
    data = deriv_provider.get_aggregated_derivatives_dashboard(
        symbol="BTCUSDT",
        period="1h",
        start_date_str="2026-08-01",
        end_date_str="2026-09-01"
    )

    assert "klines" in data
    assert "summary" in data
    assert "local_path" in data
    assert isinstance(data["klines"], list)

    # Verificar que el archivo parquet se haya creado en disco local
    local_path = data["local_path"]
    assert os.path.exists(local_path), f"El archivo Parquet {local_path} debe existir."

    # Segunda llamada debe cargar desde caché local
    cached_data = deriv_provider.get_aggregated_derivatives_dashboard(
        symbol="BTCUSDT",
        period="1h",
        start_date_str="2026-08-01",
        end_date_str="2026-09-01"
    )
    assert cached_data.get("from_cache") is True
    assert len(cached_data["klines"]) == len(data["klines"])


def test_derivatives_export_csv(deriv_provider, tmp_path):
    """Prueba la exportación de métricas de derivados a CSV."""
    data = deriv_provider.get_aggregated_derivatives_dashboard(
        symbol="ETHUSDT",
        period="1h",
        start_date_str="2026-08-15",
        end_date_str="2026-09-01"
    )

    csv_file = tmp_path / "test_deriv_export.csv"
    ok = deriv_provider.export_dashboard_to_csv(data, str(csv_file))
    assert ok is True
    assert csv_file.exists()

    df = pd.read_csv(csv_file)
    assert "datetime" in df.columns
    assert "open" in df.columns
    assert "high" in df.columns
    assert "low" in df.columns
    assert "close" in df.columns
    assert "volume" in df.columns
    assert len(df) > 0


def test_options_export_csv(options_provider, tmp_path):
    """Prueba la exportación de matriz de opciones vanilla a CSV."""
    matrix = options_provider.get_options_chain_matrix(underlying_asset="BTC")
    csv_file = tmp_path / "test_options_export.csv"

    ok = options_provider.export_chain_to_csv(matrix, str(csv_file))
    assert ok is True
    assert csv_file.exists()

    df = pd.read_csv(csv_file)
    assert "Strike" in df.columns
    assert "Call_MarkPrice" in df.columns
    assert "Put_MarkPrice" in df.columns
    assert len(df) > 0
