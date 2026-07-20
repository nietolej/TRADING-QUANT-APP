import glob
import os
from datetime import datetime, timezone
from nicegui import ui
import pandas as pd

from strategy_engine.base_strategy import BaseStrategy
from backtest_engine.backtester import Backtester
from data_layer.market_data import MarketDataManager
from data_layer.storage import SessionLocal
from backtest_engine.metrics import calculate_metrics, calculate_equity_curve_metrics

def render_strategy_analyzer():
    with ui.column().classes('w-full q-pa-md'):
        ui.label('Strategy Analyzer & Backtest History').classes('text-2xl font-bold text-primary q-mb-md')
        
        # Load strategies
        strategy_files = glob.glob("config/strategies/*.yaml")
        strategies = {os.path.basename(f): f for f in strategy_files}
        
        state = {
            'strategy_name': list(strategies.keys())[0] if strategies else '',
            'symbol': 'BTC/USDT',
            'start_date': '2024-01-01',
            'end_date': datetime.now().strftime('%Y-%m-%d'),
            'capital': 10000.0,
            
            # Results
            'cagr': '-- %',
            'max_dd': '-- %',
            'sharpe': '--',
            'total_trades': '--',
        }
        
        with ui.row().classes('w-full gap-4'):
            strat_combo = ui.select(list(strategies.keys()), label='Select Strategy', value=state['strategy_name']).bind_value(state, 'strategy_name').classes('flex-1')
            sym_combo = ui.select(['BTC/USDT', 'ETH/USDT', 'SOL/USDT'], label='Symbol', value=state['symbol']).bind_value(state, 'symbol').classes('flex-1')
            start_date = ui.input('Start Date (YYYY-MM-DD)', value=state['start_date']).bind_value(state, 'start_date').classes('flex-1')
            end_date = ui.input('End Date (YYYY-MM-DD)', value=state['end_date']).bind_value(state, 'end_date').classes('flex-1')
            capital = ui.number('Initial Capital', value=state['capital']).bind_value(state, 'capital').classes('flex-1')
            
        with ui.row().classes('w-full mt-4'):
            btn_run = ui.button('Run Backtest', on_click=lambda: run_backtest()).classes('bg-blue-600 text-white font-bold flex-1')
            ui.button('Run Optimizer (Grid Search)', on_click=lambda: ui.notify('Optimizer logic coming soon...', type='info')).classes('bg-purple-500 text-white flex-1 ml-4')

        ui.separator().classes('my-6')
        ui.label('Results Overview').classes('text-xl font-bold')
        
        with ui.row().classes('w-full gap-4 mt-4'):
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50'):
                ui.label('CAGR').classes('text-sm text-gray-500')
                ui.label().bind_text_from(state, 'cagr').classes('text-2xl font-bold text-green-600')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50'):
                ui.label('Max Drawdown').classes('text-sm text-gray-500')
                ui.label().bind_text_from(state, 'max_dd').classes('text-2xl font-bold text-red-600')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50'):
                ui.label('Sharpe Ratio').classes('text-sm text-gray-500')
                ui.label().bind_text_from(state, 'sharpe').classes('text-2xl font-bold text-blue-600')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50'):
                ui.label('Total Trades').classes('text-sm text-gray-500')
                ui.label().bind_text_from(state, 'total_trades').classes('text-2xl font-bold')

        ui.label('Equity Curve').classes('text-lg font-bold mt-6')
        chart = ui.echart({
            'tooltip': {'trigger': 'axis'},
            'xAxis': {'type': 'category', 'data': []},
            'yAxis': {'type': 'value', 'scale': True},
            'series': [{'name': 'Equity', 'type': 'line', 'data': []}],
        }).classes('w-full h-96 border rounded-lg p-2 bg-white mt-2')

        ui.label('Executions (Trades)').classes('text-lg font-bold mt-6')
        trades_container = ui.column().classes('w-full')

        async def run_backtest():
            if not state['strategy_name']:
                ui.notify("No strategy selected", type="warning")
                return
                
            btn_run.text = "Calculating..."
            
            try:
                start_dt = datetime.strptime(state['start_date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
                end_dt = datetime.strptime(state['end_date'], '%Y-%m-%d').replace(hour=23, minute=59, tzinfo=timezone.utc)
                
                file_path = strategies[state['strategy_name']]
                strategy = BaseStrategy(file_path)
                strategy.symbol = state['symbol']
                
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
                    ui.notify(f"No historical data found for {strategy.symbol} on {strategy.timeframe}", type="warning")
                    btn_run.text = "Run Backtest"
                    return
                    
                backtester = Backtester(strategy, initial_capital=state['capital'])
                results = backtester.run(df)
                
                trades_df = results.get("trades")
                equity_curve = results.get("equity_curve")
                
                if equity_curve is not None and not equity_curve.empty:
                    trade_metrics = calculate_metrics(trades_df, state['capital'])
                    eq_metrics = calculate_equity_curve_metrics(equity_curve['equity'])
                    
                    state['cagr'] = f"{eq_metrics.get('cagr', 0):.2f}%"
                    state['max_dd'] = f"{eq_metrics.get('max_drawdown_pct', 0):.2f}%"
                    state['sharpe'] = f"{eq_metrics.get('sharpe_ratio', 0):.2f}"
                    state['total_trades'] = str(trade_metrics.get('total_trades', 0))
                    
                    chart.options['xAxis']['data'] = equity_curve.index.strftime('%Y-%m-%d').tolist()
                    chart.options['series'][0]['data'] = equity_curve['equity'].tolist()
                    chart.update()
                    
                    trades_container.clear()
                    with trades_container:
                        if not trades_df.empty:
                            html_rows = ""
                            for _, row in trades_df.iterrows():
                                html_rows += f'''
                                <tr>
                                    <td class="border p-2">{str(row.get('entry_time', ''))[:16]}</td>
                                    <td class="border p-2">{str(row.get('exit_time', ''))[:16]}</td>
                                    <td class="border p-2 font-bold {'text-green-600' if row.get('side')=='long' else 'text-red-600'}">{row.get('side', '').upper()}</td>
                                    <td class="border p-2">${row.get('entry_price', 0):.2f}</td>
                                    <td class="border p-2">${row.get('exit_price', 0):.2f}</td>
                                    <td class="border p-2 font-bold {'text-green-600' if row.get('pnl', 0)>0 else 'text-red-600'}">${row.get('pnl', 0):.2f}</td>
                                </tr>
                                '''
                            ui.html(f'''
                            <div class="h-64 overflow-y-auto w-full">
                                <table class="w-full text-center border collapse bg-white">
                                    <tr class="bg-gray-200 sticky top-0">
                                        <th class="border p-2">Entry Time</th>
                                        <th class="border p-2">Exit Time</th>
                                        <th class="border p-2">Side</th>
                                        <th class="border p-2">Entry Price</th>
                                        <th class="border p-2">Exit Price</th>
                                        <th class="border p-2">P&L</th>
                                    </tr>
                                    {html_rows}
                                </table>
                            </div>
                            ''').classes('w-full')
                        else:
                            ui.label("No trades executed.").classes('text-gray-500 italic')
                            
                    ui.notify("Backtest completed successfully!", type="positive")
                
            except Exception as e:
                ui.notify(f"Error during backtest: {e}", type="negative")
            finally:
                btn_run.text = "Run Backtest"
