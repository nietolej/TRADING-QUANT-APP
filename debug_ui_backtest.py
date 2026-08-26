import pandas as pd
import asyncio
from datetime import datetime, timezone
import traceback
import sys

from strategy_engine.base_strategy import BaseStrategy
from data_layer.market_data import MarketDataManager, normalize_timeframe
from data_layer.storage import SessionLocal
from backtest_engine.backtester import Backtester
from backtest_engine.metrics import calculate_metrics, calculate_equity_curve_metrics

async def simulate_ui_run():
    try:
        state = {
            'strategy_name': 'config/strategies/ema_long.yaml',
            'start_date': '2024-01-01',
            'end_date': '2026-07-25',
            'symbol': 'BNB/BTC',
            'timeframe': '1d',
            'capital': 1,
            'capital_type': 'QUOTE',
            'custom_parameters': {
                'FAST': 15,
                'LOW': 30,
                'SL': 5.0,
                'TP': 50.0
            }
        }

        print("Parsing dates...")
        start_dt = datetime.strptime(state['start_date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(state['end_date'], '%Y-%m-%d').replace(hour=23, minute=59, tzinfo=timezone.utc)

        print("Loading strategy...")
        strategy = BaseStrategy(state['strategy_name'], custom_parameters=state.get('custom_parameters', {}))
        strategy.symbol = state['symbol']
        strategy.timeframe = normalize_timeframe(state['timeframe'])

        print("Fetching data...")
        db = SessionLocal()
        market_mgr = MarketDataManager(db)
        df = market_mgr.get_data(
            strategy.symbol, 
            strategy.timeframe, 
            start_dt,
            end_dt
        )
        db.close()

        if df.empty:
            print("DF is empty!")
            return

        print(f"DF shape: {df.shape}")

        start_price = df.iloc[0]['open'] if not df.empty else 1.0
        initial_cap_quote = state['capital']

        print("Running backtester...")
        backtester = Backtester(strategy, initial_capital=initial_cap_quote)
        
        # Simulating run.io_bound
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, backtester.run, df)

        print("Extracting results...")
        trades_df = results.get("trades")
        equity_curve = results.get("equity_curve")
        
        print(f"Trades DF empty? {trades_df.empty if trades_df is not None else True}")
        print(f"Equity Curve empty? {equity_curve.empty if equity_curve is not None else True}")

        if equity_curve is not None and not equity_curve.empty:
            if 'timestamp' in equity_curve.columns:
                equity_curve = equity_curve.set_index('timestamp')
            equity_curve.index = pd.to_datetime(equity_curve.index)

        if equity_curve is not None and not equity_curve.empty:
            print("Calculating metrics...")
            trade_metrics = calculate_metrics(trades_df, initial_cap_quote) if trades_df is not None and not trades_df.empty else {'total_trades': 0}
            eq_metrics = calculate_equity_curve_metrics(equity_curve['equity'])
            print(f"Trade metrics: {trade_metrics}")
            print(f"Eq metrics: {eq_metrics}")
        else:
            print("No equity curve to calculate metrics.")

    except Exception as e:
        print("ERROR IN UI LOGIC:")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(simulate_ui_run())
