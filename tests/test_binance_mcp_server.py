import json
import unittest
from unittest.mock import patch, MagicMock

from api.mcp.binance_mcp_server import (
    mcp,
    binance_test_connection,
    binance_get_account_balance,
    binance_get_positions,
    binance_get_open_orders,
    binance_get_market_price,
    binance_analyze_portfolio_risk,
    binance_place_futures_order,
    binance_cancel_all_orders,
)


class TestBinanceMCPServer(unittest.TestCase):

    def test_tools_registered(self):
        """Verifica que las 8 herramientas estén debidamente registradas en el MCPServer."""
        registered_tools = [t.name for t in mcp._tool_manager.list_tools()]
        expected_tools = [
            "binance_test_connection",
            "binance_get_account_balance",
            "binance_get_positions",
            "binance_get_open_orders",
            "binance_get_market_price",
            "binance_analyze_portfolio_risk",
            "binance_place_futures_order",
            "binance_cancel_all_orders",
        ]
        for tool_name in expected_tools:
            self.assertIn(tool_name, registered_tools)

    @patch("api.mcp.binance_mcp_server.verify_binance_credentials")
    def test_binance_test_connection_success(self, mock_verify):
        mock_verify.return_value = {
            "success": True,
            "latency_ms": 115,
            "wallet_balance": 15000.0,
            "available_balance": 14500.0,
            "can_trade": True,
            "error": None
        }
        res_raw = binance_test_connection(use_testnet=True)
        res = json.loads(res_raw)
        self.assertEqual(res["status"], "CONNECTED")
        self.assertEqual(res["latency_ms"], 115)
        self.assertEqual(res["wallet_balance_usdt"], 15000.0)

    @patch("api.mcp.binance_mcp_server.BinanceTestnetClient")
    def test_binance_get_account_balance(self, mock_client_cls):
        mock_instance = MagicMock()
        mock_instance.get_full_account_info.return_value = {
            "success": True,
            "network": "Binance Futures Testnet",
            "total_wallet_balance": 15250.75,
            "available_balance": 14100.0,
            "total_unrealized_pnl": 150.75,
            "multi_assets_margin": True,
            "assets": [
                {"asset": "USDT", "wallet_balance": "15250.75", "available_balance": "14100.0", "usd_value": 15250.75, "unrealized_pnl": "+150.75"}
            ]
        }
        mock_client_cls.return_value = mock_instance

        res_raw = binance_get_account_balance(use_testnet=True)
        res = json.loads(res_raw)
        self.assertTrue(res["success"])
        self.assertEqual(res["total_wallet_balance_usd"], 15250.75)
        self.assertEqual(len(res["assets"]), 1)

    @patch("api.mcp.binance_mcp_server.BinanceTestnetClient")
    def test_binance_analyze_portfolio_risk(self, mock_client_cls):
        mock_instance = MagicMock()
        mock_instance.get_full_account_info.return_value = {
            "success": True,
            "network": "Binance Futures Testnet",
            "total_wallet_balance": 10000.0,
            "available_balance": 8000.0,
            "total_margin_balance": 10000.0,
            "total_unrealized_pnl": 0.0,
            "positions": [],
            "assets": []
        }
        mock_client_cls.return_value = mock_instance

        res_raw = binance_analyze_portfolio_risk(use_testnet=True)
        res = json.loads(res_raw)
        self.assertTrue(res["success"])
        self.assertIn("health_score", res)
        self.assertIn("var_95_usd", res)

    @patch("api.mcp.binance_mcp_server.BinanceTestnetClient")
    def test_binance_cancel_all_orders(self, mock_client_cls):
        mock_instance = MagicMock()
        mock_instance.cancel_all_futures_orders.return_value = (True, None)
        mock_client_cls.return_value = mock_instance

        res_raw = binance_cancel_all_orders(symbol="BTCUSDT", use_testnet=True)
        res = json.loads(res_raw)
        self.assertTrue(res["success"])
        self.assertEqual(res["symbol"], "BTCUSDT")


if __name__ == "__main__":
    unittest.main()
