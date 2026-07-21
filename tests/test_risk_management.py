import unittest
import pandas as pd
import numpy as np
from strategy_engine.risk_management import RiskManager

class TestRiskManagement(unittest.TestCase):
    def setUp(self):
        # Create a simple DataFrame for testing
        data = {
            'open': [100, 102, 104, 103, 105],
            'high': [101, 103, 105, 106, 108],
            'low': [99, 101, 102, 102, 104],
            'close': [100, 102, 104, 105, 107],
            'volume': [1000, 1200, 1100, 1300, 1400]
        }
        self.df = pd.DataFrame(data)
        
    def test_fixed_percentage_sl_tp_long(self):
        config = {
            "stop_loss": {"type": "percentage", "value": 2.0},
            "take_profit": {"type": "percentage", "value": 4.0}
        }
        rm = RiskManager(config)
        sl, tp = rm.compute_sl_tp(self.df, entry_index=0, side="long")
        self.assertAlmostEqual(sl, 98.0)
        self.assertAlmostEqual(tp, 104.0)

    def test_fixed_percentage_sl_tp_short(self):
        config = {
            "stop_loss": {"type": "percentage", "value": 2.0},
            "take_profit": {"type": "percentage", "value": 4.0}
        }
        rm = RiskManager(config)
        sl, tp = rm.compute_sl_tp(self.df, entry_index=0, side="short")
        self.assertAlmostEqual(sl, 102.0)
        self.assertAlmostEqual(tp, 96.0)

    def test_dynamic_atr_sl_long(self):
        config = {
            "stop_loss": {
                "type": "dynamic", 
                "dynamic_method": "atr", 
                "atr_period": 2, 
                "atr_multiplier": 1.5
            }
        }
        rm = RiskManager(config)
        # Entry at index 1: close = 102
        # ATR requires some data. If NaNs, it falls back to 2%. Let's see.
        sl, tp = rm.compute_sl_tp(self.df, entry_index=1, side="long")
        # Ensure it calculates something valid
        self.assertTrue(sl < 102.0)

    def test_dynamic_rr_tp_long(self):
        config = {
            "stop_loss": {"type": "percentage", "value": 2.0},
            "take_profit": {
                "type": "dynamic", 
                "dynamic_method": "risk_reward_ratio", 
                "risk_reward_ratio": 3.0
            }
        }
        rm = RiskManager(config)
        sl, tp = rm.compute_sl_tp(self.df, entry_index=0, side="long") # close = 100
        # sl = 98. Risk = 2. RR=3 -> TP = 100 + 2*3 = 106.
        self.assertAlmostEqual(sl, 98.0)
        self.assertAlmostEqual(tp, 106.0)

    def test_position_sizing_fixed_fractional(self):
        config = {
            "position_sizing": {
                "method": "fixed_fractional",
                "risk_per_trade_pct": 1.0
            }
        }
        rm = RiskManager(config)
        capital = 10000.0
        entry_price = 100.0
        sl_price = 90.0 # 10 risk per share
        # Risk amount = 1% of 10000 = 100
        # Quantity = 100 / 10 = 10 shares
        qty = rm.compute_position_size(capital, entry_price, sl_price)
        self.assertAlmostEqual(qty, 10.0)
        
    def test_update_trailing_sl_percent(self):
        config = {
            "stop_loss": {"type": "trailing_percent", "value": 2.0}
        }
        rm = RiskManager(config)
        # Long side
        # Entry 100, SL should be 98
        current_sl = 98.0
        # Price goes up to 110, new SL should be 110 * 0.98 = 107.8
        new_sl = rm.update_trailing_sl(current_sl, current_price=110.0, current_high=110.0, current_low=109.0, current_atr=None, side="long")
        self.assertAlmostEqual(new_sl, 107.8)
        
        # Price goes down to 105, SL should remain 107.8
        new_sl = rm.update_trailing_sl(new_sl, current_price=105.0, current_high=106.0, current_low=104.0, current_atr=None, side="long")
        self.assertAlmostEqual(new_sl, 107.8)
        
    def test_dynamic_chandelier_sl_long(self):
        config = {
            "stop_loss": {
                "type": "dynamic", 
                "dynamic_method": "chandelier", 
                "atr_period": 2, 
                "atr_multiplier": 2.5
            }
        }
        rm = RiskManager(config)
        sl, tp = rm.compute_sl_tp(self.df, entry_index=1, side="long")
        # Ensure initial sl is set and is less than current entry price
        self.assertTrue(sl is not None)
        self.assertTrue(sl < 102.0)
        # Check if 'ATR' column was added to df
        self.assertTrue('ATR' in self.df.columns)

if __name__ == '__main__':
    unittest.main()
