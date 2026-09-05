import os
import unittest
from unittest.mock import patch, MagicMock

from execution_engine.binance_client import (
    get_binance_credentials,
    save_binance_credentials,
    verify_binance_credentials,
    BinanceTestnetClient,
)


class TestApiCredentialsManager(unittest.TestCase):

    def test_get_binance_credentials(self):
        with patch.dict(os.environ, {
            "BINANCE_TESTNET_API_KEY": "test_k_123",
            "BINANCE_TESTNET_SECRET_KEY": "test_s_123",
            "BINANCE_REAL_API_KEY": "real_k_456",
            "BINANCE_REAL_SECRET_KEY": "real_s_456",
            "BINANCE_TESTNET": "true"
        }):
            creds = get_binance_credentials()
            self.assertEqual(creds["testnet_api_key"], "test_k_123")
            self.assertEqual(creds["testnet_secret_key"], "test_s_123")
            self.assertEqual(creds["real_api_key"], "real_k_456")
            self.assertEqual(creds["real_secret_key"], "real_s_456")
            self.assertEqual(creds["default_network"], "testnet")
            self.assertTrue(creds["has_testnet"])
            self.assertTrue(creds["has_real"])

    @patch("dotenv.set_key")
    def test_save_binance_credentials(self, mock_set_key):
        ok, err = save_binance_credentials(
            testnet_key="new_test_key",
            testnet_secret="new_test_sec",
            real_key="new_real_key",
            real_secret="new_real_sec",
            default_network="mainnet"
        )
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(os.environ.get("BINANCE_TESTNET_API_KEY"), "new_test_key")
        self.assertEqual(os.environ.get("BINANCE_REAL_API_KEY"), "new_real_key")
        self.assertEqual(os.environ.get("BINANCE_TESTNET"), "false")
        # Retrocompatibilidad
        self.assertEqual(os.environ.get("BINANCE_API_KEY"), "new_real_key")

    @patch("execution_engine.binance_client.Client")
    def test_verify_binance_credentials_success(self, mock_client_cls):
        mock_instance = MagicMock()
        mock_instance.futures_ping.return_value = {}
        mock_instance.futures_account.return_value = {
            "totalWalletBalance": "1500.50",
            "availableBalance": "1200.00",
            "canTrade": True
        }
        mock_client_cls.return_value = mock_instance

        res = verify_binance_credentials(use_testnet=True, api_key="valid_key", api_secret="valid_sec")
        self.assertTrue(res["success"])
        self.assertEqual(res["wallet_balance"], 1500.50)
        self.assertEqual(res["available_balance"], 1200.00)
        self.assertTrue(res["can_trade"])
        self.assertIsNone(res["error"])


if __name__ == '__main__':
    unittest.main()
