"""
Tests unitarios para el proveedor de datos de Derivados (Binance Futures).
"""
import pytest
from unittest.mock import patch, MagicMock
from data_layer.data_sources.binance_derivatives_provider import BinanceDerivativesProvider


def test_derivatives_provider_init():
    p = BinanceDerivativesProvider()
    assert p.timeout == 8
    assert isinstance(p._cache, dict)


@patch("requests.Session.get")
def test_get_funding_rate_history(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {"fundingTime": 1700000000000, "fundingRate": "0.0001", "markPrice": "80000.0"}
    ]
    mock_get.return_value = mock_response

    p = BinanceDerivativesProvider()
    res = p.get_funding_rate_history("BTCUSDT", limit=1)
    assert len(res) == 1
    assert res[0]["funding_rate"] == 0.0001
    assert res[0]["annualized_apr"] == pytest.approx(0.0001 * 3 * 365 * 100)


@patch("requests.Session.get")
def test_get_open_interest_history(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {"timestamp": 1700000000000, "sumOpenInterest": "10000.5", "sumOpenInterestValue": "800000000.0"}
    ]
    mock_get.return_value = mock_response

    p = BinanceDerivativesProvider()
    res = p.get_open_interest_history("BTCUSDT", period="1h", limit=1)
    assert len(res) == 1
    assert res[0]["sum_open_interest_usd"] == 800000000.0


@patch("requests.Session.get")
def test_get_top_long_short_ratio(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {"timestamp": 1700000000000, "longAccount": "0.6", "shortAccount": "0.4", "longShortRatio": "1.5"}
    ]
    mock_get.return_value = mock_response

    p = BinanceDerivativesProvider()
    res = p.get_top_long_short_account_ratio("BTCUSDT", period="1h", limit=1)
    assert len(res) == 1
    assert res[0]["long_account_pct"] == 60.0
    assert res[0]["long_short_ratio"] == 1.5
