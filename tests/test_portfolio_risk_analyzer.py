import unittest
import os
from unittest.mock import patch, MagicMock

from analytics.portfolio_risk_analyzer import PortfolioRiskAnalyzer
from execution_engine.binance_client import BinanceTestnetClient


class TestPortfolioRiskAnalyzer(unittest.TestCase):
    def setUp(self):
        self.sample_account_data = {
            "network": "Binance Futures Testnet",
            "use_testnet": True,
            "total_wallet_balance": 10000.0,
            "total_usd_value": 10000.0,
            "available_balance": 8000.0,
            "total_unrealized_pnl": 250.0,
            "total_margin_balance": 10250.0,
            "assets": [
                {"asset": "USDT", "wallet_balance": 8000.0, "usd_value": 8000.0},
                {"asset": "BTC", "wallet_balance": 0.025, "usd_value": 2000.0}
            ],
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": 0.1,  # LONG 0.1 BTC
                    "entryPrice": 79000.0,
                    "markPrice": 80000.0,
                    "liquidationPrice": 71000.0,
                    "unrealizedProfit": 100.0,
                    "initialMargin": 1600.0,
                    "leverage": 5
                },
                {
                    "symbol": "ETHUSDT",
                    "positionAmt": -1.0,  # SHORT 1.0 ETH
                    "entryPrice": 3000.0,
                    "markPrice": 2850.0,
                    "liquidationPrice": 3600.0,
                    "unrealizedProfit": 150.0,
                    "initialMargin": 400.0,
                    "leverage": 5
                }
            ]
        }

    def test_compute_portfolio_metrics(self):
        metrics = PortfolioRiskAnalyzer.compute_portfolio_metrics(self.sample_account_data)
        
        self.assertEqual(metrics["total_wallet_usd"], 10000.0)
        self.assertEqual(metrics["available_balance_usd"], 8000.0)
        self.assertEqual(metrics["committed_margin_usd"], 2000.0)
        self.assertEqual(metrics["margin_utilization_pct"], 20.0)
        
        # Notional: BTC (0.1 * 80000 = 8000), ETH (1.0 * 2850 = 2850) -> Total = 10850
        self.assertEqual(metrics["total_notional_usd"], 10850.0)
        self.assertAlmostEqual(metrics["effective_leverage"], 1.085, places=3)
        self.assertEqual(metrics["long_notional_usd"], 8000.0)
        self.assertEqual(metrics["short_notional_usd"], 2850.0)
        self.assertEqual(metrics["net_delta_usd"], 5150.0)
        self.assertEqual(metrics["net_bias"], "LONG")
        self.assertEqual(metrics["positions_count"], 2)

        # BTC Liq Distance: (80000 - 71000)/80000 = 11.25%
        # ETH Liq Distance: (3600 - 2850)/2850 = 26.31%
        self.assertAlmostEqual(metrics["min_liq_distance_pct"], 11.25, places=2)
        self.assertEqual(metrics["highest_risk_symbol"], "BTCUSDT")

    def test_compute_var_cvar(self):
        var_metrics = PortfolioRiskAnalyzer.compute_var_cvar(
            positions=self.sample_account_data["positions"],
            total_equity=10000.0
        )
        self.assertGreater(var_metrics["var_95_usd"], 0)
        self.assertGreater(var_metrics["var_99_usd"], var_metrics["var_95_usd"])
        self.assertGreater(var_metrics["cvar_95_usd"], var_metrics["var_95_usd"])
        self.assertIn(var_metrics["risk_category"], ["CONTROLADO", "MODERADO", "ALTO"])

    def test_stress_test(self):
        stress_results = PortfolioRiskAnalyzer.run_stress_test(
            positions=self.sample_account_data["positions"],
            total_equity=10000.0,
            shocks=[-0.10, 0.0, 0.10]
        )
        self.assertEqual(len(stress_results), 3)
        
        # At shock = 0.0%, pnl impact should be 0.0
        zero_shock = next(r for r in stress_results if r["shock_pct"] == 0.0)
        self.assertEqual(zero_shock["pnl_impact_usd"], 0.0)
        self.assertEqual(zero_shock["projected_equity"], 10000.0)

        # At shock = +10%, Long (8000 * 0.1 = +800), Short (2850 * -0.1 = -285) -> Net = +515
        pos_shock = next(r for r in stress_results if r["shock_pct"] == 10.0)
        self.assertEqual(pos_shock["pnl_impact_usd"], 515.0)
        self.assertEqual(pos_shock["projected_equity"], 10515.0)

    def test_ai_interpretation(self):
        metrics = PortfolioRiskAnalyzer.compute_portfolio_metrics(self.sample_account_data)
        var_metrics = PortfolioRiskAnalyzer.compute_var_cvar(self.sample_account_data["positions"], 10000.0)
        stress = PortfolioRiskAnalyzer.run_stress_test(self.sample_account_data["positions"], 10000.0)
        
        interp = PortfolioRiskAnalyzer.generate_ai_interpretation(metrics, var_metrics, stress)
        self.assertIn("health_score", interp)
        self.assertIn("health_badge", interp)
        self.assertIn("market_posture", interp)
        self.assertIn("margin_and_leverage", interp)
        self.assertIn("liquidation_safety", interp)
        self.assertIsInstance(interp["recommendations"], list)
        self.assertGreater(len(interp["recommendations"]), 0)

    def test_empty_account(self):
        empty_data = {
            "total_wallet_balance": 0.0,
            "available_balance": 0.0,
            "assets": [],
            "positions": []
        }
        res = PortfolioRiskAnalyzer.analyze_portfolio(empty_data)
        self.assertEqual(res["metrics"]["positions_count"], 0)
        self.assertEqual(res["var_metrics"]["var_95_usd"], 0.0)
        self.assertEqual(res["interpretation"]["health_score"], 100.0)

    def test_binance_client_credential_resolution(self):
        with patch.dict(os.environ, {
            "BINANCE_TESTNET_API_KEY": "testnet_key_123",
            "BINANCE_TESTNET_SECRET_KEY": "testnet_sec_123",
            "BINANCE_REAL_API_KEY": "real_key_456",
            "BINANCE_REAL_SECRET_KEY": "real_sec_456",
        }):
            with patch('execution_engine.binance_client.Client'):
                client_test = BinanceTestnetClient(use_testnet=True)
                self.assertEqual(client_test.api_key, "testnet_key_123")
                self.assertEqual(client_test.api_secret, "testnet_sec_123")

                client_real = BinanceTestnetClient(use_testnet=False)
                self.assertEqual(client_real.api_key, "real_key_456")
                self.assertEqual(client_real.api_secret, "real_sec_456")


if __name__ == '__main__':
    unittest.main()
