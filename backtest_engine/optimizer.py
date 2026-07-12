import pandas as pd
import itertools
import copy
from typing import List, Dict, Any
from backtest_engine.backtester import Backtester
from strategy_engine.base_strategy import BaseStrategy

class GridSearchOptimizer:
    def __init__(self, base_config: dict, param_grid: Dict[str, List[Any]], initial_capital: float = 10000.0, commission_pct: float = 0.1):
        self.base_config = base_config
        self.param_grid = param_grid
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct
        
    def _set_nested_value(self, d: dict, path: str, value: Any):
        keys = path.split('.')
        current = d
        for i, key in enumerate(keys[:-1]):
            # Convert to int if it's a list index
            if key.isdigit():
                key = int(key)
                
            next_key = keys[i+1]
            if isinstance(current, list):
                if next_key.isdigit():
                    current = current[key]
                else:
                    # current is a list of dicts. If key exists, we access it.
                    current = current[key]
            else:
                if key not in current:
                    current[key] = [] if next_key.isdigit() else {}
                current = current[key]
                
        last_key = keys[-1]
        if last_key.isdigit():
            current[int(last_key)] = value
        else:
            current[last_key] = value

    def run(self, df: pd.DataFrame, progress_callback=None) -> pd.DataFrame:
        keys = list(self.param_grid.keys())
        values = list(self.param_grid.values())
        
        combinations = list(itertools.product(*values))
        total_combos = len(combinations)
        results = []
        
        for idx, combo in enumerate(combinations):
            if progress_callback:
                progress_callback(idx, total_combos)
                
            config = copy.deepcopy(self.base_config)
            combo_dict = dict(zip(keys, combo))
            
            for k, v in combo_dict.items():
                self._set_nested_value(config, k, v)
                
            # Instance strategy
            strategy = BaseStrategy(config)
            
            # Backtest
            bt = Backtester(strategy, initial_capital=self.initial_capital, commission_pct=self.commission_pct)
            try:
                run_metrics = bt.run(df)
            except Exception as e:
                print(f"Error in combination {idx}: {e}")
                continue
                
            row = combo_dict.copy()
            row['CAGR (%)'] = run_metrics.get('cagr', 0)
            row['Max Drawdown (%)'] = run_metrics.get('max_drawdown_pct', 0)
            row['Net Profit'] = run_metrics.get('net_profit', 0)
            row['Win Rate (%)'] = run_metrics.get('percent_profitable', 0)
            row['Profit Factor'] = run_metrics.get('profit_factor', 0)
            row['Total Trades'] = run_metrics.get('total_trades', 0)
            
            results.append(row)
            
        if progress_callback:
            progress_callback(total_combos, total_combos)
            
        df_res = pd.DataFrame(results)
        if not df_res.empty:
            df_res = df_res.sort_values(by='CAGR (%)', ascending=False).reset_index(drop=True)
        return df_res
