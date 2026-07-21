import glob
import os
from datetime import datetime, timezone
from nicegui import ui
import pandas as pd

from strategy_engine.base_strategy import BaseStrategy
from backtest_engine.backtester import Backtester
from data_layer.market_data import MarketDataManager
from data_layer.storage import SessionLocal, OHLCV
from backtest_engine.metrics import calculate_metrics, calculate_equity_curve_metrics

def render_strategy_analyzer():
    with ui.column().classes('w-full q-pa-md'):
        ui.label('Strategy Analyzer & Backtest History').classes('text-2xl font-bold text-primary q-mb-md')
        
        # Load strategies
        strategy_files = glob.glob("config/strategies/*.yaml")
        strategies = {os.path.basename(f): f for f in strategy_files}
        
        # Load available symbols from database
        db = SessionLocal()
        try:
            available_symbols = [r[0] for r in db.query(OHLCV.symbol).distinct().all()]
        except Exception:
            available_symbols = []
        finally:
            db.close()
            
        if not available_symbols:
            available_symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']

        state = {
            'strategy_name': list(strategies.keys())[0] if strategies else '',
            'symbol': available_symbols[0],
            'timeframe': '4h',
            'start_date': '2024-01-01',
            'end_date': datetime.now().strftime('%Y-%m-%d'),
            'capital': 10000.0,
            'capital_type': 'QUOTE',
            
            # Results
            'cagr': '-- %',
            'max_dd': '-- %',
            'sharpe': '--',
            'total_trades': '--',
            'net_pnl_quote': '--',
            'net_pnl_base': '--',
            'total_pnl_quote': '--',
            'total_pnl_base': '--',
            'quote_asset': 'USDT',
            'base_asset': 'BTC',
            'custom_parameters': {},
        }
        
        with ui.row().classes('w-full gap-4'):
            strat_combo = ui.select(list(strategies.keys()), label='Select Strategy', value=state['strategy_name']).bind_value(state, 'strategy_name').classes('flex-1')
            sym_combo = ui.select(available_symbols, label='Symbol', value=state['symbol']).bind_value(state, 'symbol').classes('flex-1')
            time_combo = ui.select(['1m', '5m', '15m', '1h', '4h', '1d'], label='Timeframe', value=state['timeframe']).bind_value(state, 'timeframe').classes('w-32')
            start_date = ui.input('Start Date (YYYY-MM-DD)', value=state['start_date']).bind_value(state, 'start_date').classes('flex-1')
            end_date = ui.input('End Date (YYYY-MM-DD)', value=state['end_date']).bind_value(state, 'end_date').classes('flex-1')
            capital = ui.number('Initial Capital', value=state['capital']).bind_value(state, 'capital').classes('flex-1')
            capital_type = ui.select(['BASE', 'QUOTE'], label='Asset Type', value=state['capital_type']).bind_value(state, 'capital_type').classes('w-32')
            
        with ui.row().classes('w-full mt-4'):
            btn_run = ui.button('Run Backtest', on_click=lambda: run_backtest()).classes('bg-blue-600 text-white font-bold flex-1')
            ui.button('Run Optimizer (Grid Search)', on_click=lambda: ui.notify('Optimizer logic coming soon...', type='info')).classes('bg-purple-500 text-white flex-1 ml-4')

        ui.label('Strategy Parameters').classes('text-lg font-bold mt-6')
        params_container = ui.row().classes('w-full gap-4 flex-wrap mb-4')
        
        def update_parameters_ui():
            params_container.clear()
            file_path = strategies.get(state['strategy_name'])
            if not file_path or not os.path.exists(file_path):
                return
                
            try:
                import yaml
                with open(file_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                    
                params = config.get('parameters', {})
                state['custom_parameters'] = params.copy()
                
                if not params:
                    with params_container:
                        ui.label("No configurable parameters in this strategy YAML.").classes('text-gray-500 italic mt-2')
                    return
                    
                with params_container:
                    for key, val in params.items():
                        if isinstance(val, (int, float)):
                            ui.number(key.replace('_', ' ').title(), value=val).bind_value(state['custom_parameters'], key).classes('w-48')
                        else:
                            ui.input(key.replace('_', ' ').title(), value=str(val)).bind_value(state['custom_parameters'], key).classes('w-48')
            except Exception as e:
                ui.notify(f"Error loading parameters: {e}", type='negative')
                
        strat_combo.on_value_change(update_parameters_ui)
        update_parameters_ui() # Trigger initial load

        ui.separator().classes('my-6')
        ui.label('Results Overview').classes('text-xl font-bold')
        
        with ui.row().classes('w-full gap-4 mt-4 flex-wrap'):
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label('CAGR').classes('text-sm text-gray-500')
                ui.label().bind_text_from(state, 'cagr').classes('text-2xl font-bold text-green-600')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label('Max Drawdown').classes('text-sm text-gray-500')
                ui.label().bind_text_from(state, 'max_dd').classes('text-2xl font-bold text-red-600')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label('Sharpe Ratio').classes('text-sm text-gray-500')
                ui.label().bind_text_from(state, 'sharpe').classes('text-2xl font-bold text-blue-600')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label('Total Trades').classes('text-sm text-gray-500')
                ui.label().bind_text_from(state, 'total_trades').classes('text-2xl font-bold')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label().bind_text_from(state, 'quote_asset', backward=lambda x: f'Initial Cap ({x})').classes('text-sm text-gray-500')
                lbl_init_quote = ui.label('--').classes('text-2xl font-bold text-black')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label().bind_text_from(state, 'base_asset', backward=lambda x: f'Initial Cap ({x})').classes('text-sm text-gray-500')
                lbl_init_base = ui.label('--').classes('text-2xl font-bold text-black')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label().bind_text_from(state, 'quote_asset', backward=lambda x: f'Final Balance ({x})').classes('text-sm text-gray-500')
                lbl_bal_quote = ui.label('--').classes('text-2xl font-bold text-black')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label().bind_text_from(state, 'base_asset', backward=lambda x: f'Final Balance ({x})').classes('text-sm text-gray-500')
                lbl_bal_base = ui.label('--').classes('text-2xl font-bold text-black')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label().bind_text_from(state, 'quote_asset', backward=lambda x: f'Total P&L ({x})').classes('text-sm text-gray-500')
                lbl_pnl_quote = ui.label('--').classes('text-2xl font-bold text-black')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label().bind_text_from(state, 'base_asset', backward=lambda x: f'Total P&L ({x})').classes('text-sm text-gray-500')
                lbl_pnl_base = ui.label('--').classes('text-2xl font-bold text-black')

        ui.label('Price Chart').classes('text-lg font-bold mt-6')
        price_chart = ui.echart({
            'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'cross'}},
            'xAxis': {'type': 'category', 'data': []},
            'yAxis': {'scale': True},
            'series': [{'name': 'Price', 'type': 'candlestick', 'data': []}],
        }).classes('w-full h-96 border rounded-lg p-2 bg-white mt-2')

        ui.label('Equity Curve').classes('text-lg font-bold mt-6')
        chart = ui.echart({
            'tooltip': {'trigger': 'axis'},
            'xAxis': {'type': 'category', 'data': []},
            'yAxis': {'type': 'value', 'scale': True},
            'series': [{'name': 'Equity', 'type': 'line', 'data': []}],
        }).classes('w-full h-96 border rounded-lg p-2 bg-white mt-2')

        ui.label('Drawdown Chart').classes('text-lg font-bold mt-6')
        drawdown_chart = ui.echart({
            'tooltip': {
                'trigger': 'axis',
                'formatter': '{b}<br/>Drawdown: {c}%'
            },
            'xAxis': {'type': 'category', 'data': []},
            'yAxis': {
                'type': 'value',
                'axisLabel': {'formatter': '{value}%'},
                'scale': True
            },
            'series': [{
                'name': 'Drawdown',
                'type': 'line',
                'areaStyle': {'color': 'rgba(239, 68, 68, 0.3)'},
                'lineStyle': {'color': 'rgb(239, 68, 68)'},
                'data': []
            }],
        }).classes('w-full h-64 border rounded-lg p-2 bg-white mt-2')

        ui.label('Executions (Trades)').classes('text-lg font-bold mt-6')
        trades_columns = []
        trades_table = ui.table(columns=trades_columns, rows=[], row_key='entry_time').classes('w-full')

        async def run_backtest():
            if not state['strategy_name']:
                ui.notify("No strategy selected", type="warning")
                return
                
            btn_run.text = "Calculating..."
            
            try:
                start_dt = datetime.strptime(state['start_date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
                end_dt = datetime.strptime(state['end_date'], '%Y-%m-%d').replace(hour=23, minute=59, tzinfo=timezone.utc)
                
                file_path = strategies[state['strategy_name']]
                strategy = BaseStrategy(file_path, custom_parameters=state.get('custom_parameters', {}))
                strategy.symbol = state['symbol']
                strategy.timeframe = state['timeframe']
                
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
                    
                start_price = df.iloc[0]['open'] if not df.empty else 1.0
                
                if state.get('capital_type', 'QUOTE') == 'BASE':
                    initial_cap_quote = state['capital'] * start_price
                else:
                    initial_cap_quote = state['capital']
                
                initial_cap_base = initial_cap_quote / start_price if start_price > 0 else 0
                    
                backtester = Backtester(strategy, initial_capital=initial_cap_quote)
                results = backtester.run(df)
                
                trades_df = results.get("trades")
                equity_curve = results.get("equity_curve")
                
                if equity_curve is not None and not equity_curve.empty:
                    trade_metrics = calculate_metrics(trades_df, initial_cap_quote)
                    eq_metrics = calculate_equity_curve_metrics(equity_curve['equity'])
                    
                    state['cagr'] = f"{eq_metrics.get('cagr', 0):.2f}%"
                    state['max_dd'] = f"{eq_metrics.get('max_drawdown_pct', 0):.2f}%"
                    state['sharpe'] = f"{eq_metrics.get('sharpe_ratio', 0):.2f}"
                    state['total_trades'] = str(trade_metrics.get('total_trades', 0))
                    
                    chart.options['xAxis']['data'] = equity_curve.index.strftime('%Y-%m-%d').tolist()
                    chart.options['series'][0]['data'] = equity_curve['equity'].tolist()
                    chart.update()
                    
                    # Calculate drawdown
                    roll_max = equity_curve['equity'].cummax()
                    drawdown = (equity_curve['equity'] - roll_max) / roll_max * 100
                    
                    drawdown_chart.options['xAxis']['data'] = equity_curve.index.strftime('%Y-%m-%d').tolist()
                    drawdown_chart.options['series'][0]['data'] = drawdown.round(2).tolist()
                    drawdown_chart.update()
                    
                    price_chart.options['xAxis']['data'] = df.index.strftime('%Y-%m-%d %H:%M').tolist()
                    price_chart.options['series'][0]['data'] = df[['open', 'close', 'low', 'high']].values.tolist()
                    price_chart.update()
                else:
                    state['cagr'] = "0.00%"
                    state['max_dd'] = "0.00%"
                    state['sharpe'] = "0.00"
                    state['total_trades'] = "0"
                    
                    chart.options['xAxis']['data'] = df.index.strftime('%Y-%m-%d').tolist()
                    chart.options['series'][0]['data'] = [initial_cap_quote] * len(df)
                    chart.update()
                    
                    drawdown_chart.options['xAxis']['data'] = df.index.strftime('%Y-%m-%d').tolist()
                    drawdown_chart.options['series'][0]['data'] = [0] * len(df)
                    drawdown_chart.update()
                    
                    price_chart.options['xAxis']['data'] = df.index.strftime('%Y-%m-%d %H:%M').tolist()
                    price_chart.options['series'][0]['data'] = df[['open', 'close', 'low', 'high']].values.tolist()
                    price_chart.update()

                    
                # Dynamically configure trades table columns with base and quote assets
                base_asset = state['symbol'].split('/')[0] if '/' in state['symbol'] else 'BTC'
                quote_asset = state['symbol'].split('/')[1] if '/' in state['symbol'] else 'USDT'
                
                state['quote_asset'] = quote_asset
                state['base_asset'] = base_asset
                
                columns = [
                    {'name': 'entry_time', 'label': 'Entry Date (UTC)', 'field': 'entry_time', 'sortable': True},
                    {'name': 'exit_time', 'label': 'Exit Date (UTC)', 'field': 'exit_time', 'sortable': True},
                    {'name': 'side', 'label': 'Side', 'field': 'side', 'sortable': True},
                    {'name': 'entry_price', 'label': 'Entry Price', 'field': 'entry_price', 'sortable': True},
                    {'name': 'exit_price', 'label': 'Exit Price', 'field': 'exit_price', 'sortable': True},
                    {'name': 'pnl_quote', 'label': f'P&L ({quote_asset})', 'field': 'pnl_quote', 'sortable': True},
                    {'name': 'pnl_base', 'label': f'P&L ({base_asset})', 'field': 'pnl_base', 'sortable': True},
                    {'name': 'cum_pnl_quote', 'label': f'Cum P&L ({quote_asset})', 'field': 'cum_pnl_quote', 'sortable': True},
                    {'name': 'cum_pnl_base', 'label': f'Cum P&L ({base_asset})', 'field': 'cum_pnl_base', 'sortable': True},
                    {'name': 'balance_quote', 'label': f'Balance ({quote_asset})', 'field': 'balance_quote', 'sortable': True},
                    {'name': 'balance_base', 'label': f'Balance ({base_asset})', 'field': 'balance_base', 'sortable': True},
                    {'name': 'drawdown', 'label': 'Drawdown (%)', 'field': 'drawdown', 'sortable': True},
                    {'name': 'exit_reason', 'label': 'Reason', 'field': 'exit_reason', 'sortable': True},
                ]
                trades_table.columns = columns
                
                trades_rows = []
                cum_q = 0.0
                cum_b = 0.0
                balance_quote = initial_cap_quote
                balance_base = initial_cap_base
                if trades_df is not None and not trades_df.empty:
                    for _, row in trades_df.iterrows():
                        pnl_quote = row.get('pnl', 0)
                        exit_price = row.get('exit_price', 1)
                        pnl_base = pnl_quote / exit_price if exit_price > 0 else 0
                        
                        cum_q += pnl_quote
                        
                        balance_quote = initial_cap_quote + cum_q
                        balance_base = balance_quote / exit_price if exit_price > 0 else 0
                        
                        cum_b = balance_base - initial_cap_base
                        
                        exit_time = row.get('exit_time')
                        dd_val = 0.0
                        try:
                            ts = pd.to_datetime(exit_time)
                            if ts in drawdown.index:
                                dd_val = drawdown.loc[ts]
                            else:
                                idx = drawdown.index.get_indexer([ts], method='nearest')[0]
                                dd_val = drawdown.iloc[idx]
                        except:
                            pass
                        
                        balance_quote = initial_cap_quote + cum_q
                        balance_base = balance_quote / exit_price if exit_price > 0 else 0
                        
                        trades_rows.append({
                            'entry_time': str(row.get('entry_time', ''))[:10],
                            'exit_time': str(row.get('exit_time', ''))[:10],
                            'side': str(row.get('side', '')).upper(),
                            'entry_price': f"${row.get('entry_price', 0):.2f}",
                            'exit_price': f"${row.get('exit_price', 0):.2f}",
                            'pnl_quote': f"${pnl_quote:.2f}",
                            'pnl_base': f"{pnl_base:.6f}",
                            'cum_pnl_quote': f"${cum_q:.2f}",
                            'cum_pnl_base': f"{cum_b:.6f}",
                            'balance_quote': f"${balance_quote:.2f}",
                            'balance_base': f"{balance_base:.6f}",
                            'drawdown': f"{dd_val:.2f}%",
                            'exit_reason': str(row.get('exit_reason', ''))
                        })
                trades_table.rows = trades_rows
                trades_table.update()
                
                lbl_init_quote.text = f"${initial_cap_quote:.2f}"
                lbl_init_base.text = f"{initial_cap_base:.6f}"
                
                lbl_bal_quote.text = f"${balance_quote:.2f}"
                lbl_bal_base.text = f"{balance_base:.6f}"
                lbl_pnl_quote.text = f"${cum_q:.2f}"
                lbl_pnl_base.text = f"{cum_b:.6f}"
                
                def update_color(lbl, is_green):
                    color_class = 'text-green-600' if is_green else 'text-red-600'
                    lbl.classes(remove='text-black text-green-600 text-red-600', add=color_class)
                    
                update_color(lbl_bal_quote, balance_quote >= initial_cap_quote)
                update_color(lbl_bal_base, balance_base >= initial_cap_base)
                
                update_color(lbl_pnl_quote, cum_q >= 0)
                update_color(lbl_pnl_base, cum_b >= 0)
                        
                ui.notify("Backtest completed successfully!", type="positive")
                
            except Exception as e:
                ui.notify(f"Error during backtest: {e}", type="negative")
            finally:
                btn_run.text = "Run Backtest"
