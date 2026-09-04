import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
from datetime import datetime
from execution_engine.paper_trader import PaperTrader, Position

class TestLiveExchangeSync(unittest.TestCase):
    def setUp(self):
        self.bot = PaperTrader(
            strategy_yaml_path="config/strategies/ema_long.yaml",
            name="Test Bot",
            custom_symbol="BTC/USDT",
            custom_timeframe="1m",
            initial_balance=1000.0,
            currency="USDT",
            use_testnet=True
        )
        self.bot._client = MagicMock()

    def test_position_sync_closed_on_binance(self):
        """Test that when Binance reports positionAmt == 0, internal position is closed with reason BINANCE_EXCHANGE_CLOSED"""
        self.bot.position = Position(side="long", entry_price=80000.0, quantity=0.01, timestamp=datetime.now())
        self.bot.binance_position_info = {
            "symbol": "BTCUSDT",
            "amount": 0.01,
            "entry_price": 80000.0
        }
        self.bot.klines_df = pd.DataFrame([{"close": 79500.0}], index=[pd.to_datetime("2026-09-04 12:00:00")])
        
        # Simulate exchange sync detection of 0 position
        open_positions = [] # empty list, Binance has 0 open positions
        open_by_symbol = {str(p.get('symbol', '')).upper(): p for p in open_positions if float(p.get('positionAmt', 0.0)) != 0}
        
        binance_sym = self.bot.symbol.replace('/', '').upper()
        p_info = open_by_symbol.get(binance_sym)
        self.assertIsNone(p_info)
        
        # When position closed on exchange
        if self.bot.position or self.bot.binance_position_info:
            self.bot.binance_position_info = None
            if self.bot.position:
                exit_p = float(self.bot.klines_df['close'].iloc[-1])
                self.bot._close_position(exit_p, datetime.now(), reason="BINANCE_EXCHANGE_CLOSED")

        # Verify bot state
        self.assertIsNone(self.bot.position)
        self.assertIsNone(self.bot.binance_position_info)
        self.assertEqual(len(self.bot.trade_history), 1)
        self.assertEqual(self.bot.trade_history[0]['reason'], "BINANCE_EXCHANGE_CLOSED")
        self.assertEqual(self.bot.trade_history[0]['exit_price'], 79500.0)

if __name__ == '__main__':
    unittest.main()
