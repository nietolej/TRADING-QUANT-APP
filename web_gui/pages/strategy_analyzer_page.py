import glob
import os
import json
import uuid
import asyncio
from datetime import datetime, timezone
from nicegui import ui, run
import pandas as pd
import numpy as np

from strategy_engine.base_strategy import BaseStrategy
from strategy_engine.risk_management import RiskManager
from backtest_engine.backtester import Backtester
from backtest_engine.optimizer import run_grid_search, count_combinations
from data_layer.market_data import MarketDataManager, normalize_timeframe
from data_layer.storage import SessionLocal, OHLCV, BacktestRun
from backtest_engine.metrics import calculate_metrics, calculate_equity_curve_metrics
import yaml
from web_gui.components.tradingview_chart import build_tradingview_plotly_figure


def render_strategy_analyzer(on_back_to_builder=None):
    with ui.column().classes('w-full q-pa-md'):
        with ui.row().classes('w-full justify-between items-center mb-4'):
            ui.label('Análisis de Estrategia y Backtesting').classes('text-2xl font-bold text-slate-200')
            if on_back_to_builder:
                ui.button('Volver al Strategy Builder', on_click=on_back_to_builder, icon='arrow_back').classes('bg-slate-700 text-white hover:bg-slate-600 shadow-xl rounded-lg')

        with ui.dialog() as catalog_dialog, ui.card().classes('w-[800px] max-w-4xl q-pa-md'):
            ui.label('Catálogo de Estrategias').classes('text-xl font-bold q-mb-md')
            catalog_columns = [
                {'name': 'name', 'label': 'Nombre', 'field': 'name', 'sortable': True},
                {'name': 'direction', 'label': 'Dirección', 'field': 'direction', 'sortable': True},
                {'name': 'tp', 'label': 'Take Profit', 'field': 'tp', 'sortable': True},
                {'name': 'sl', 'label': 'Stop Loss', 'field': 'sl', 'sortable': True},
                {'name': 'description', 'label': 'Descripción', 'field': 'description', 'sortable': True},
                {'name': 'actions', 'label': 'Acción', 'field': 'actions'},
            ]
            catalog_table = ui.table(columns=catalog_columns, rows=[], row_key='name').classes('w-full')
            
            catalog_table.add_slot('body-cell-actions', '''
                <q-td :props="props">
                    <q-btn flat dense color="primary" label="Seleccionar" icon="check" @click="() => $parent.$emit('select_strat', props.row)" />
                    <q-btn flat dense round icon="delete" color="negative" @click="() => $parent.$emit('delete_strat', props.row)" />
                </q-td>
            ''')

            def load_catalog():
                rows = []
                for f_path in glob.glob("config/strategies/*.yaml"):
                    try:
                        with open(f_path, 'r', encoding='utf-8') as f:
                            data = yaml.safe_load(f)
                            if data:
                                tp = data.get('risk_management', {}).get('take_profit', {}).get('value', 'N/A')
                                sl = data.get('risk_management', {}).get('stop_loss', {}).get('value', 'N/A')
                                rows.append({
                                    'name': data.get('strategy_name', os.path.basename(f_path)),
                                    'filename': os.path.basename(f_path),
                                    'direction': data.get('trade_direction', 'N/A'),
                                    'tp': f"{tp}%" if isinstance(tp, (int, float)) else str(tp),
                                    'sl': f"{sl}%" if isinstance(sl, (int, float)) else str(sl),
                                    'description': data.get('description', '')
                                })
                    except:
                        pass
                catalog_table.rows = rows
                catalog_table.update()
                catalog_dialog.open()

            def on_select_strat(e):
                row = e.args
                fname = row.get('filename')
                if fname and fname in strategies:
                    state['strategy_name'] = fname
                    strat_combo.value = fname
                    ui.notify(f"Estrategia '{row['name']}' seleccionada", type='info')
                    catalog_dialog.close()

            def on_delete_strat(e):
                row = e.args
                strategy_name = row['name']
                matched_file = None
                for f_path in glob.glob("config/strategies/*.yaml"):
                    try:
                        with open(f_path, 'r', encoding='utf-8') as f:
                            data = yaml.safe_load(f)
                            if data and data.get('strategy_name') == strategy_name:
                                matched_file = f_path
                                break
                    except Exception:
                        pass
                if not matched_file:
                    matched_file = f"config/strategies/{strategy_name.lower().replace(' ', '_')}.yaml"

                try:
                    if os.path.exists(matched_file):
                        os.remove(matched_file)
                        ui.notify(f"Estrategia '{strategy_name}' eliminada correctamente", type='positive')
                    else:
                        ui.notify(f"No se encontró el archivo de la estrategia", type='warning')
                    
                    # Refresh strategies list
                    nonlocal strategies
                    strategy_files = glob.glob("config/strategies/*.yaml")
                    strategies = {os.path.basename(f): f for f in strategy_files}
                    strat_combo.options = list(strategies.keys())
                    strat_combo.update()
                    load_catalog()
                except Exception as ex:
                    ui.notify(f"Error eliminando estrategia: {str(ex)}", type='negative')

            catalog_table.on('select_strat', on_select_strat)
            catalog_table.on('delete_strat', on_delete_strat)

            ui.button('Cerrar', on_click=catalog_dialog.close).classes('mt-4')

        # ════════════════════════════════════════════════════════
        # DIALOG DEL HISTORIAL DE BACKTESTS GUARDADOS
        # ════════════════════════════════════════════════════════
        with ui.dialog() as history_dialog, ui.card().classes('w-[1100px] max-w-6xl q-pa-md overflow-auto'):
            with ui.row().classes('w-full items-center justify-between q-mb-md'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('history', size='2rem').classes('text-emerald-500')
                    ui.label('Historial de Backtests Guardados').classes('text-xl font-bold text-gray-800')
                ui.button(icon='close', on_click=history_dialog.close).props('flat round dense')
                
            history_columns = [
                {'name': 'created_at', 'label': 'Fecha', 'field': 'created_at', 'sortable': True},
                {'name': 'strategy_name', 'label': 'Estrategia', 'field': 'strategy_name', 'sortable': True},
                {'name': 'symbol', 'label': 'Par', 'field': 'symbol', 'sortable': True},
                {'name': 'timeframe', 'label': 'TF', 'field': 'timeframe', 'sortable': True},
                {'name': 'params_str', 'label': 'Parámetros', 'field': 'params_str'},
                {'name': 'cagr', 'label': 'CAGR (%)', 'field': 'cagr', 'sortable': True},
                {'name': 'max_dd', 'label': 'Max DD (%)', 'field': 'max_dd', 'sortable': True},
                {'name': 'pf', 'label': 'Profit Factor', 'field': 'pf', 'sortable': True},
                {'name': 'win_rate', 'label': '% Ganadoras', 'field': 'win_rate', 'sortable': True},
                {'name': 'trades', 'label': 'Trades', 'field': 'trades', 'sortable': True},
                {'name': 'actions', 'label': 'Acción', 'field': 'actions'},
            ]
            history_table = ui.table(columns=history_columns, rows=[], row_key='run_id').classes('w-full')
            
            history_table.add_slot('body-cell-actions', '''
                <q-td :props="props">
                    <q-btn flat dense color="primary" label="Cargar" icon="upload" @click="() => $parent.$emit('load_run', props.row)" />
                    <q-btn flat dense round icon="delete" color="negative" @click="() => $parent.$emit('delete_run', props.row)" />
                </q-td>
            ''')

            def load_history():
                db = SessionLocal()
                try:
                    runs = db.query(BacktestRun).order_by(BacktestRun.created_at.desc()).all()
                    rows = []
                    for run in runs:
                        params_summary = ""
                        if run.config_snapshot:
                            try:
                                cfg = json.loads(run.config_snapshot)
                                custom_p = cfg.get('custom_parameters', {})
                                params_summary = ", ".join([f"{k}:{v}" for k, v in custom_p.items()])
                            except:
                                params_summary = str(run.config_snapshot)[:40]

                        rows.append({
                            'run_id': run.run_id,
                            'created_at': run.created_at.strftime("%Y-%m-%d %H:%M") if run.created_at else "N/A",
                            'strategy_name': run.strategy_name or "",
                            'symbol': run.symbol or "",
                            'timeframe': run.timeframe or "",
                            'params_str': params_summary or "Standard",
                            'cagr': f"{run.cagr:.2f}%" if run.cagr is not None else "0.00%",
                            'max_dd': f"{run.max_drawdown_pct:.2f}%" if run.max_drawdown_pct is not None else "0.00%",
                            'pf': f"{run.profit_factor:.2f}" if run.profit_factor is not None else "0.00",
                            'win_rate': f"{run.win_rate:.2f}%" if run.win_rate is not None else "0.00%",
                            'trades': run.total_trades or 0,
                            'config_snapshot': run.config_snapshot,
                            'start_date': run.start_date.strftime("%Y-%m-%d") if run.start_date else "2024-01-01",
                            'end_date': run.end_date.strftime("%Y-%m-%d") if run.end_date else datetime.now().strftime("%Y-%m-%d")
                        })
                    history_table.rows = rows
                    history_table.update()
                    history_dialog.open()
                except Exception as ex:
                    ui.notify(f"Error cargando historial: {ex}", type="negative")
                finally:
                    db.close()

            def on_load_run(e):
                row = e.args
                try:
                    if row.get('strategy_name') and row['strategy_name'] in strategies:
                        state['strategy_name'] = row['strategy_name']
                        strat_combo.value = row['strategy_name']
                    if row.get('symbol'):
                        state['symbol'] = row['symbol']
                        sym_combo.value = row['symbol']
                    if row.get('timeframe'):
                        state['timeframe'] = row['timeframe']
                        tf_combo.value = row['timeframe']
                    if row.get('start_date'):
                        state['start_date'] = row['start_date']
                        start_date_input.value = row['start_date']
                    if row.get('end_date'):
                        state['end_date'] = row['end_date']
                        end_date_input.value = row['end_date']

                    if row.get('config_snapshot'):
                        try:
                            cfg = json.loads(row['config_snapshot'])
                            if 'custom_parameters' in cfg:
                                state['custom_parameters'] = cfg['custom_parameters']
                            if 'sizing_mode' in cfg and cfg['sizing_mode']:
                                state['sizing_mode'] = cfg['sizing_mode']
                                sizing_combo.value = cfg['sizing_mode']
                            if 'commission_pct' in cfg and cfg['commission_pct'] is not None:
                                state['commission_pct'] = float(cfg['commission_pct'])
                                comm_input.value = float(cfg['commission_pct'])
                            if 'slippage_pct' in cfg and cfg['slippage_pct'] is not None:
                                state['slippage_pct'] = float(cfg['slippage_pct'])
                                slip_input.value = float(cfg['slippage_pct'])
                        except Exception as parse_ex:
                            print(f"Error parsing config snapshot: {parse_ex}")

                    update_parameters_ui()
                    history_dialog.close()
                    ui.notify(f"✅ Parámetros de '{row.get('strategy_name')}' cargados desde el historial", type="positive")
                except Exception as ex:
                    ui.notify(f"Error al aplicar parámetros: {ex}", type="negative")

            def on_delete_run(e):
                row = e.args
                run_id = row.get('run_id')
                if run_id:
                    db = SessionLocal()
                    try:
                        db.query(BacktestRun).filter(BacktestRun.run_id == run_id).delete()
                        db.commit()
                        ui.notify("🗑️ Registro de backtest eliminado del historial", type="positive")
                        load_history()
                    except Exception as ex:
                        db.rollback()
                        ui.notify(f"Error eliminando registro: {ex}", type="negative")
                    finally:
                        db.close()

            history_table.on('load_run', on_load_run)
            history_table.on('delete_run', on_delete_run)

        def save_current_backtest():
            metrics = state.get('last_computed_metrics')
            if not metrics:
                ui.notify("Ejecuta primero una prueba retrospectiva para guardar sus resultados", type="warning")
                return

            db = SessionLocal()
            try:
                run_id = str(uuid.uuid4())
                config_json = json.dumps({
                    'strategy_filename': state.get('strategy_name'),
                    'strategy_name': state.get('strategy_name'),
                    'custom_parameters': state.get('custom_parameters', {}),
                    'sizing_mode': state.get('sizing_mode'),
                    'commission_pct': state.get('commission_pct'),
                    'slippage_pct': state.get('slippage_pct'),
                    'capital': state.get('capital'),
                    'capital_type': state.get('capital_type')
                })
                run_record = BacktestRun(
                    run_id=run_id,
                    strategy_name=state.get('strategy_name', 'Estrategia'),
                    symbol=state.get('symbol', 'BTC/USDT'),
                    timeframe=state.get('timeframe', '1d'),
                    start_date=metrics.get('start_dt'),
                    end_date=metrics.get('end_dt'),
                    created_at=datetime.now(timezone.utc),
                    config_snapshot=config_json,
                    cagr=metrics.get('cagr', 0.0),
                    sharpe_ratio=metrics.get('sharpe_ratio', 0.0),
                    max_drawdown_pct=metrics.get('max_drawdown_pct', 0.0),
                    win_rate=metrics.get('win_rate', 0.0),
                    profit_factor=metrics.get('profit_factor', 0.0),
                    total_trades=metrics.get('total_trades', 0),
                    percent_profitable=metrics.get('win_rate', 0.0),
                    average_trade_net_profit=0.0
                )
                db.add(run_record)
                db.commit()
                ui.notify("💾 ¡Backtest guardado exitosamente en el historial!", type="positive")
            except Exception as ex:
                db.rollback()
                ui.notify(f"Error al guardar backtest: {ex}", type="negative")
            finally:
                db.close()

        # Hero Header
        with ui.card().classes('w-full bg-slate-900 text-white p-6 rounded-2xl shadow-xl mb-6'):
            with ui.row().classes('items-center justify-between w-full'):
                with ui.row().classes('items-center gap-4'):
                    ui.icon('analytics', size='3rem').classes('text-blue-400')
                    with ui.column().classes('gap-1'):
                        ui.label('Strategy Analyzer').classes('text-3xl font-extrabold tracking-tight')
                        ui.label('Análisis de estrategia e historial de pruebas retrospectivas').classes('text-slate-400 text-sm')
                with ui.row().classes('items-center gap-3'):
                    ui.button('Ver Catálogo', on_click=load_catalog, icon='list').props('rounded').classes('bg-blue-600 hover:bg-blue-500 text-white font-bold px-4 py-2 shadow-lg transition-all')
                    ui.button('Historial de Backtests', on_click=load_history, icon='history').props('rounded').classes('bg-emerald-600 hover:bg-emerald-500 text-white font-bold px-4 py-2 shadow-lg transition-all')

        # Rutas absolutas — independientes del directorio de trabajo
        _page_dir = os.path.dirname(os.path.abspath(__file__))
        _base_dir = os.path.abspath(os.path.join(_page_dir, '..', '..'))
        _strategies_dir = os.path.join(_base_dir, 'config', 'strategies')

        # Cargar estrategias con ruta absoluta
        strategy_files = glob.glob(os.path.join(_strategies_dir, '*.yaml'))
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
            'capital': 1.0,
            'capital_type': 'BASE',
            'sizing_mode': 'Interés Compuesto (100% Capital)',
            'commission_pct': 0.1,
            'slippage_pct': 0.05,
            
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
            # Reactive display strings for overview cards
            'init_quote_str': '--',
            'init_base_str': '--',
            'bal_quote_str': '--',
            'bal_base_str': '--',
            'pnl_quote_str': '--',
            'pnl_base_str': '--',
        }

        async def run_backtest(e=None):
            import logging as _logging
            _logging.info("run_backtest: started")

            if not state['strategy_name']:
                ui.notify("No hay estrategia seleccionada", type="warning")
                return

            # Obtain client from the button element directly (avoids slot context issues in background tasks)
            client = btn_run.client

            # Direct element updates – do NOT require a slot context
            btn_run.set_text("⏳ Calculando simulación...")
            btn_run.disable()

            # ui.notify() creates a new element and DOES need a slot context
            calc_notify = None
            try:
                with client:
                    calc_notify = ui.notify(
                        "⚡ Ejecutando simulación de backtest... Por favor espera",
                        type="info", spinner=True, timeout=5.0
                    )
            except Exception as _ne:
                _logging.warning(f"run_backtest: could not show calc_notify: {_ne}")
            
            try:
                try:
                    start_dt = datetime.strptime(str(state['start_date']).strip(), '%Y-%m-%d').replace(tzinfo=timezone.utc)
                except Exception:
                    start_dt = datetime(2024, 1, 1, tzinfo=timezone.utc)

                try:
                    end_dt = datetime.strptime(str(state['end_date']).strip(), '%Y-%m-%d').replace(hour=23, minute=59, tzinfo=timezone.utc)
                except Exception:
                    end_dt = datetime.now(timezone.utc)
                
                file_path = strategies[state['strategy_name']]
                symbol = state['symbol']
                timeframe = normalize_timeframe(state['timeframe'])

                try:
                    cap_val = float(state.get('capital', 1.0) or 1.0)
                except (ValueError, TypeError):
                    cap_val = 1.0
                
                # Para estimar capital inicial si se configuró en activo BASE
                db_temp = SessionLocal()
                mgr_temp = MarketDataManager(db_temp)
                first_candle = mgr_temp.get_data(symbol, timeframe, start_dt, end_dt)
                db_temp.close()
                
                start_price = first_candle.iloc[0]['open'] if not first_candle.empty else 1.0
                
                if state.get('capital_type', 'QUOTE') == 'BASE':
                    initial_cap_quote = cap_val * start_price
                else:
                    initial_cap_quote = cap_val
                
                initial_cap_base = initial_cap_quote / start_price if start_price > 0 else 0
                
                comm_pct = float(state.get('commission_pct', 0.1) or 0.1)
                slip_pct = float(state.get('slippage_pct', 0.05) or 0.05)
                sizing_mode = state.get('sizing_mode', 'Interés Compuesto (100% Capital)')
                
                # Ejecutar descarga + simulación en hilo I/O secundario para NO congelar la UI
                results = await run.io_bound(
                    _sync_load_and_run,
                    file_path,
                    state.get('custom_parameters', {}),
                    symbol,
                    timeframe,
                    start_dt,
                    end_dt,
                    initial_cap_quote,
                    sizing_mode,
                    comm_pct,
                    slip_pct
                )
                
                with client:
                    if 'error' in results:
                        ui.notify(results['error'], type="warning")
                    else:
                        df          = results.get('df', pd.DataFrame())
                        trades_df   = results.get("trades")
                        equity_curve = results.get("equity_curve")
                        drawdown    = pd.Series(dtype=float)
    
                        if equity_curve is not None and not equity_curve.empty:
                            if 'strategy_indicators' in results:
                                # Desactivar medias previas
                                for k in list(chart_config.keys()):
                                    if k.startswith('sma_') or k.startswith('ema_'):
                                        chart_config[k] = False
                                # Activar dinámicamente las calculadas en la estrategia
                                for ind, p in results['strategy_indicators']:
                                    key = f"{ind}_{p}"
                                    chart_config[key] = True
                                
                                try:
                                    render_price_overlays.refresh()
                                except Exception:
                                    pass

                            if 'timestamp' in equity_curve.columns:
                                equity_curve = equity_curve.set_index('timestamp')
                            equity_curve.index = pd.to_datetime(equity_curve.index)
    
                            try:
                                trade_metrics = (
                                    calculate_metrics(trades_df, initial_cap_quote)
                                    if trades_df is not None and not trades_df.empty
                                    else {'total_trades': 0}
                                )
                                eq_metrics = calculate_equity_curve_metrics(equity_curve['equity'])
    
                                def safe_num(v, default=0.0):
                                    try:
                                        f = float(v)
                                        return default if (v is None or f != f or abs(f) == float('inf')) else f
                                    except Exception:
                                        return default
    
                                cagr_val      = safe_num(eq_metrics.get('cagr'))
                                maxdd_val     = safe_num(eq_metrics.get('max_drawdown_pct'))
                                sharpe_val    = safe_num(eq_metrics.get('sharpe_ratio'))
                                pf_val        = safe_num(trade_metrics.get('profit_factor'))
                                n_trades      = int(safe_num(trade_metrics.get('total_trades')))
                                win_trades    = int(safe_num(trade_metrics.get('winning_trades')))
                                lose_trades   = int(safe_num(trade_metrics.get('losing_trades')))
                                win_rate      = safe_num(trade_metrics.get('percent_profitable'))
                                max_cons_loss = int(safe_num(trade_metrics.get('max_consecutive_losers')))
    
                                state['last_computed_metrics'] = {
                                    'cagr': cagr_val, 'max_drawdown_pct': maxdd_val,
                                    'sharpe_ratio': sharpe_val, 'profit_factor': pf_val,
                                    'total_trades': n_trades, 'winning_trades': win_trades,
                                    'losing_trades': lose_trades, 'win_rate': win_rate,
                                    'max_consecutive_losers': max_cons_loss,
                                    'start_dt': str(start_dt)[:10], 'end_dt': str(end_dt)[:10]
                                }
    
                                lbl_cagr.set_text(f"{cagr_val:.2f}%")
                                lbl_maxdd.set_text(f"{maxdd_val:.2f}%")
                                lbl_sharpe.set_text(f"{sharpe_val:.2f}")
                                lbl_profit_factor.set_text(f"{pf_val:.2f}")
                                lbl_total_trades.set_text(str(n_trades))
                                lbl_win_trades.set_text(str(win_trades))
                                lbl_lose_trades.set_text(str(lose_trades))
                                lbl_win_rate.set_text(f"{win_rate:.2f}%")
                                lbl_max_cons_losers.set_text(str(max_cons_loss))
    
                                lbl_cagr.classes(remove='text-green-600 text-red-600',
                                                 add='text-green-600' if cagr_val >= 0 else 'text-red-600')
                                lbl_maxdd.classes(remove='text-green-600 text-red-600', add='text-red-600')
                                lbl_sharpe.classes(remove='text-green-600 text-red-600 text-blue-600',
                                                   add='text-blue-600' if sharpe_val >= 0 else 'text-red-600')
                                lbl_profit_factor.classes(
                                    remove='text-green-600 text-yellow-600 text-red-600',
                                    add='text-green-600' if pf_val >= 1.2 else 'text-yellow-600' if pf_val >= 1.0 else 'text-red-600'
                                )
                                lbl_win_rate.classes(remove='text-green-600 text-red-600',
                                                     add='text-green-600' if win_rate > 50 else 'text-red-600')
    
                                import numpy as np
                                roll_max = equity_curve['equity'].cummax()
                                drawdown = ((equity_curve['equity'] - roll_max) / roll_max * 100).replace([np.inf, -np.inf], np.nan).fillna(0)
                                
                                # Downsample to prevent browser freeze and WebSocket disconnects on large datasets
                                MAX_POINTS = 1000
                                if len(equity_curve) > MAX_POINTS:
                                    step = len(equity_curve) // MAX_POINTS
                                    dates_eq = equity_curve.index[::step].strftime('%Y-%m-%d %H:%M').tolist()
                                    eq_vals = equity_curve['equity'].iloc[::step].replace([np.inf, -np.inf], np.nan).fillna(0)
                                    drawdown_sampled = drawdown.iloc[::step]
                                else:
                                    dates_eq = equity_curve.index.strftime('%Y-%m-%d %H:%M').tolist()
                                    eq_vals = equity_curve['equity'].replace([np.inf, -np.inf], np.nan).fillna(0)
                                    drawdown_sampled = drawdown

                                close_aln = df['close'].reindex(eq_vals.index).ffill()
                                eq_base   = (eq_vals / close_aln).replace([np.inf, -np.inf], np.nan).fillna(0)
    
                                state['equity_dates']      = dates_eq
                                state['equity_quote_data'] = eq_vals.round(6).tolist()
                                state['equity_base_data']  = eq_base.round(6).tolist()
    
                                is_quote = (equity_toggle.value == 'QUOTE')
                                chart.options['xAxis']['data'] = dates_eq
                                chart.options['series'][0]['data'] = (
                                    state['equity_quote_data'] if is_quote else state['equity_base_data']
                                )
                                base_a  = state['symbol'].split('/')[0] if '/' in state['symbol'] else 'BASE'
                                quote_a = state['symbol'].split('/')[1] if '/' in state['symbol'] else 'QUOTE'
                                chart.options['series'][0]['name'] = f"Equity ({quote_a if is_quote else base_a})"
                                chart.update()
    
                                drawdown_chart.options['xAxis']['data'] = dates_eq
                                drawdown_chart.options['series'][0]['data'] = drawdown_sampled.round(4).tolist()
                                drawdown_chart.update()
    
                                price_chart_df = df
                                state['last_df'] = df
                                state['last_trades_df'] = trades_df
                                await render_tradingview_plotly()

    
                            except Exception as _me:
                                import traceback as _tb
                                ui.notify(f"Error métricas: {_me}\n{_tb.format_exc()[:400]}", type='negative', timeout=15000)
    
                        else:
                            lbl_cagr.set_text("0.00%"); lbl_maxdd.set_text("0.00%")
                            lbl_sharpe.set_text("0.00"); lbl_profit_factor.set_text("0.00")
                            lbl_total_trades.set_text("0"); lbl_win_trades.set_text("0")
                            lbl_lose_trades.set_text("0"); lbl_win_rate.set_text("0.00%")
                            lbl_max_cons_losers.set_text("0")
                            
                            # Downsample empty chart
                            MAX_POINTS = 1000
                            df_sampled = df.iloc[::max(1, len(df)//MAX_POINTS)] if len(df) > MAX_POINTS else df
                            dates_empty = df_sampled.index.strftime('%Y-%m-%d %H:%M').tolist()
                            
                            chart.options['xAxis']['data'] = dates_empty
                            chart.options['series'][0]['data'] = [round(initial_cap_quote, 6)] * len(df_sampled)
                            chart.update()
                            drawdown_chart.options['xAxis']['data'] = dates_empty
                            drawdown_chart.options['series'][0]['data'] = [0] * len(df_sampled)
                            drawdown_chart.update()
                            state['last_df'] = df
                            state['last_trades_df'] = None
                            await render_tradingview_plotly()

                            ui.notify("El backtest no generó curva de equity (sin señales o error).", type='warning')
    
                        # ── Trades table ──
                        base_asset  = state['symbol'].split('/')[0] if '/' in state['symbol'] else 'BTC'
                        quote_asset = state['symbol'].split('/')[1] if '/' in state['symbol'] else 'USDT'
                        state['quote_asset'] = quote_asset
                        state['base_asset']  = base_asset
    
                        columns = [
                            {'name': 'result',       'label': 'W/L',                         'field': 'result',       'sortable': True, 'align': 'center'},
                            {'name': 'entry_time',   'label': 'Fecha entrada (UTC)',          'field': 'entry_time',   'sortable': True, 'align': 'left'},
                            {'name': 'exit_time',    'label': 'Fecha salida (UTC)',           'field': 'exit_time',    'sortable': True, 'align': 'left'},
                            {'name': 'side',         'label': 'Lado',                        'field': 'side',         'sortable': True, 'align': 'center'},
                            {'name': 'exit_reason',  'label': 'Razón de Salida',             'field': 'exit_reason',  'sortable': True, 'align': 'center'},
                            {'name': 'entry_price',  'label': 'Precio entrada',              'field': 'entry_price',  'sortable': True, 'align': 'right'},
                            {'name': 'exit_price',   'label': 'Precio salida',               'field': 'exit_price',   'sortable': True, 'align': 'right'},
                            {'name': 'pnl_pct',      'label': '% Resultado',                 'field': 'pnl_pct',      'sortable': True, 'align': 'right'},
                            {'name': 'pnl_quote',    'label': f'P&L ({quote_asset})',        'field': 'pnl_quote',    'sortable': True, 'align': 'right'},
                            {'name': 'pnl_base',     'label': f'P&L ({base_asset})',         'field': 'pnl_base',     'sortable': True, 'align': 'right'},
                            {'name': 'cum_pnl_quote','label': f'P&L acum. ({quote_asset})',  'field': 'cum_pnl_quote','sortable': True, 'align': 'right'},
                            {'name': 'cum_pnl_base', 'label': f'P&L acum. ({base_asset})',   'field': 'cum_pnl_base', 'sortable': True, 'align': 'right'},
                            {'name': 'balance_quote','label': f'Saldo ({quote_asset})',       'field': 'balance_quote','sortable': True, 'align': 'right'},
                            {'name': 'balance_base', 'label': f'Saldo ({base_asset})',        'field': 'balance_base', 'sortable': True, 'align': 'right'},
                            {'name': 'drawdown',     'label': 'Reducción máx. (%)',          'field': 'drawdown',     'sortable': True, 'align': 'right'},
                        ]
                        trades_table.columns = columns
    
                        def fmt_price(p):
                            if p is None: return 'N/A'
                            try:
                                p = float(p)
                                if abs(p) >= 1000: return f"{p:,.2f}"
                                if abs(p) >= 1:    return f"{p:.4f}"
                                if abs(p) >= 0.01: return f"{p:.6f}"
                                return f"{p:.8f}"
                            except Exception:
                                return str(p)
    
                        def fmt_pnl(p):
                            if p is None: return 'N/A'
                            try:
                                p = float(p)
                                if abs(p) >= 1000: return f"{p:+,.2f}"
                                if abs(p) >= 1:    return f"{p:+.4f}"
                                if abs(p) >= 0.01: return f"{p:+.6f}"
                                return f"{p:+.8f}"
                            except Exception:
                                return str(p)
    
                        trades_rows   = []
                        cum_q         = 0.0
                        cum_b         = 0.0
                        balance_quote = initial_cap_quote
                        balance_base  = initial_cap_base
    
                        if trades_df is not None and not trades_df.empty:
                            import asyncio
                            trades_df_clean = trades_df.replace([np.inf, -np.inf], np.nan).fillna(0)
                            for idx_counter, (_, trow) in enumerate(trades_df_clean.iterrows()):
                                if idx_counter % 100 == 0:
                                    await asyncio.sleep(0)
                                pnl_quote   = float(trow.get('pnl', 0) or 0)
                                entry_price = float(trow.get('entry_price', 1) or 1)
                                exit_price  = float(trow.get('exit_price',  1) or 1)
                                side        = str(trow.get('side', '')).upper()
                                pnl_base    = pnl_quote / exit_price if exit_price > 0 else 0
    
                                if entry_price > 0:
                                    if side == 'LONG':
                                        pnl_pct = (exit_price - entry_price) / entry_price * 100
                                    elif side == 'SHORT':
                                        pnl_pct = (entry_price - exit_price) / entry_price * 100
                                    else:
                                        pnl_pct = 0.0
                                else:
                                    pnl_pct = 0.0
    
                                cum_q        += pnl_quote
                                balance_quote = initial_cap_quote + cum_q
                                balance_base  = balance_quote / exit_price if exit_price > 0 else 0
                                cum_b         = balance_base - initial_cap_base
    
                                dd_val = 0.0
                                try:
                                    ts = pd.to_datetime(trow.get('exit_time'))
                                    if len(drawdown) > 0:
                                        idx = drawdown.index.get_indexer([ts], method='nearest')[0]
                                        if idx >= 0:
                                            dd_val = float(drawdown.iloc[idx])
                                except Exception:
                                    pass
    
                                trades_rows.append({
                                    'result':       'W' if pnl_quote > 0 else 'L',
                                    'entry_time':   str(trow.get('entry_time', ''))[:19],
                                    'exit_time':    str(trow.get('exit_time',  ''))[:19],
                                    'side':         side,
                                    'entry_price':  fmt_price(entry_price),
                                    'exit_price':   fmt_price(exit_price),
                                    'pnl_pct':      float(pnl_pct),
                                    'pnl_quote':    fmt_pnl(pnl_quote),
                                    'pnl_base':     fmt_pnl(pnl_base),
                                    'cum_pnl_quote':fmt_pnl(cum_q),
                                    'cum_pnl_base': fmt_pnl(cum_b),
                                    'balance_quote':fmt_price(balance_quote),
                                    'balance_base': fmt_price(balance_base),
                                    'drawdown':     f"{dd_val:.2f}%",
                                    'exit_reason':  str(trow.get('exit_reason', ''))
                                })
    
                        # Limit the trades table to the last 2000 trades to prevent browser freeze and disconnects
                        if len(trades_rows) > 2000:
                            trades_table.rows = trades_rows[-2000:]
                            ui.notify(f"Mostrando los últimos 2000 trades (de {len(trades_rows)}).", type='info')
                        else:
                            trades_table.rows = trades_rows
                            
                        trades_table.update()
    
                        # ── Summary labels ──
                        lbl_hdr_init_q.set_text(f'Capital Inicial ({quote_asset})')
                        lbl_hdr_init_b.set_text(f'Capital Inicial ({base_asset})')
                        lbl_hdr_bal_q.set_text(f'Balance Final ({quote_asset})')
                        lbl_hdr_bal_b.set_text(f'Balance Final ({base_asset})')
                        lbl_hdr_pnl_q.set_text(f'P&L Total ({quote_asset})')
                        lbl_hdr_pnl_b.set_text(f'P&L Total ({base_asset})')
                        lbl_init_quote.set_text(fmt_price(initial_cap_quote))
                        lbl_init_base.set_text(fmt_price(initial_cap_base))
                        lbl_bal_quote.set_text(fmt_price(balance_quote))
                        lbl_bal_base.set_text(fmt_price(balance_base))
                        lbl_pnl_quote.set_text(fmt_pnl(cum_q))
                        lbl_pnl_base.set_text(fmt_pnl(cum_b))
    
                        def _upd_color(lbl, positive):
                            lbl.classes(remove='text-white text-green-600 text-red-600',
                                        add='text-green-600' if positive else 'text-red-600')
    
                        _upd_color(lbl_bal_quote, balance_quote >= initial_cap_quote)
                        _upd_color(lbl_bal_base,  balance_base  >= initial_cap_base)
                        _upd_color(lbl_pnl_quote, cum_q >= 0)
                        _upd_color(lbl_pnl_base,  cum_b >= 0)
                        ui.notify("✅ Backtest completado exitosamente", type="positive", timeout=3.0)
    
            except Exception as e:
                import traceback as _tb3
                import logging as _log2
                _full_err = _tb3.format_exc()
                _log2.error(f"Backtest error:\n{_full_err}")
                print(f"BACKTEST ERROR:\n{_full_err}", flush=True)
                try:
                    with client:
                        ui.notify(f"Error: {type(e).__name__}: {e}", type="negative", timeout=20000)
                except Exception:
                    pass
            finally:
                try:
                    if calc_notify is not None:
                        calc_notify.dismiss()
                except Exception:
                    pass
                try:
                    btn_run.enable()
                    btn_run.set_text("EJECUTAR PRUEBA RETROSPECTIVA")
                except Exception:
                    pass

 


        # ── Fila 1: Estrategia + Par de activo + Timeframe ──
        with ui.row().classes('w-full gap-3 items-end mt-2'):
            with ui.column().classes('flex-1 gap-0'):
                ui.label('Seleccionar estrategia').classes('text-xs text-gray-500 mb-1')
                strat_combo = ui.select(
                    list(strategies.keys()),
                    label='Estrategia',
                    value=state['strategy_name']
                ).bind_value(state, 'strategy_name').classes('w-full')

            with ui.column().classes('flex-1 gap-0'):
                ui.label('Símbolo / Par de activo').classes('text-xs text-gray-500 mb-1')

                def _parse_assets(symbol: str):
                    """Devuelve (base, quote) dado 'BNB/BTC'."""
                    parts = symbol.split('/') if '/' in symbol else [symbol, 'USDT']
                    return parts[0].strip(), parts[1].strip()

                sym_combo = ui.select(
                    available_symbols,
                    label='Símbolo',
                    value=state['symbol'],
                    new_value_mode='add-unique'
                ).bind_value(state, 'symbol').classes('w-full')

            with ui.column().classes('w-32 gap-0'):
                ui.label('Timeframe').classes('text-xs text-gray-500 mb-1')
                time_combo = ui.select(
                    ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w'],
                    label='Intervalo',
                    value=state['timeframe']
                ).bind_value(state, 'timeframe').classes('w-full')

            with ui.column().classes('w-auto justify-end'):
                ui.label(' ').classes('text-xs mb-1')
                def refresh_symbols():
                    db = SessionLocal()
                    try:
                        db_syms = sorted([r[0] for r in db.query(OHLCV.symbol).distinct().all()])
                        extra = ['BTC/USDT','ETH/USDT','SOL/USDT','BNB/USDT','BNB/BTC',
                                 'XRP/USDT','ADA/USDT','DOGE/USDT','AVAX/USDT','DOT/USDT']
                        all_syms = sorted(list(set(db_syms + extra)))
                        sym_combo.options = all_syms
                        sym_combo.update()
                        ui.notify(f'{len(all_syms)} pares disponibles', type='positive', timeout=2000)
                    except Exception as ex:
                        ui.notify(f'Error cargando pares: {ex}', type='warning')
                    finally:
                        db.close()
                ui.button(icon='refresh', on_click=refresh_symbols).props('round dense color=blue-6').tooltip('Recargar pares disponibles')

        refresh_symbols()

        # ── Fila 2: Fechas + Capital + Activo inicial ──
        with ui.row().classes('w-full gap-3 items-end mt-2'):
            with ui.column().classes('flex-1 gap-0'):
                ui.label('Fecha de inicio (AAAA-MM-DD)').classes('text-xs text-gray-500 mb-1')
                start_date = ui.input('Fecha de inicio', value=state['start_date']).bind_value(state, 'start_date').classes('w-full')
            with ui.column().classes('flex-1 gap-0'):
                ui.label('Fecha de finalización (AAAA-MM-DD)').classes('text-xs text-gray-500 mb-1')
                end_date = ui.input('Fecha de finalización', value=state['end_date']).bind_value(state, 'end_date').classes('w-full')
            with ui.column().classes('flex-1 gap-0'):
                ui.label('Capital inicial').classes('text-xs text-gray-500 mb-1')
                capital = ui.number('Capital', value=state['capital']).bind_value(state, 'capital').classes('w-full')

            # ── Selector de activo dinámico ──
            with ui.column().classes('w-52 gap-0'):
                _init_base, _init_quote = _parse_assets(state['symbol'])
                _asset_opts = [
                    f'{_init_quote} (CITA)',
                    f'{_init_base} (BASE)',
                ]
                state['capital_asset'] = _asset_opts[1]   # default: BASE
                state['capital_type'] = 'BASE'

                lbl_asset_hdr = ui.label(f'Activo inicial ({_init_base}/{_init_quote})').classes('text-xs text-gray-500 mb-1')
                def _on_asset_change(e):
                    val = getattr(e, 'value', None) or _asset_opts[1]
                    state['capital_asset'] = val
                    state['capital_type'] = 'QUOTE' if '(CITA)' in val else 'BASE'

                capital_type = ui.select(
                    _asset_opts,
                    label='Activo de inicio',
                    value=_asset_opts[1],
                    on_change=_on_asset_change
                ).bind_value(state, 'capital_asset').classes('w-full')

                def _update_asset_combo(e=None):
                    sym = state['symbol']
                    b, q = _parse_assets(sym)
                    opts = [f'{q} (CITA)', f'{b} (BASE)']
                    capital_type.options = opts
                    capital_type.value = opts[1]
                    state['capital_asset'] = opts[1]
                    state['capital_type'] = 'BASE'
                    lbl_asset_hdr.set_text(f'Activo inicial ({b}/{q})')
                    capital_type.update()

                sym_combo.on('update:model-value', _update_asset_combo)

        # ── Fila 3: Configuración de Fricción y Tamaño de Posición (Sizing / Comisiones / Slippage) ──
        with ui.row().classes('w-full gap-3 items-end mt-2'):
            with ui.column().classes('flex-1 gap-0'):
                ui.label('Modo de Posición (Sizing)').classes('text-xs text-gray-500 mb-1')
                sizing_combo = ui.select(
                    ['Interés Compuesto (100% Capital)', 'Riesgo Fijo (1% por trade)', 'Monto Fijo ($1000)'],
                    label='Modo de Tamaño',
                    value=state['sizing_mode']
                ).bind_value(state, 'sizing_mode').classes('w-full')
            with ui.column().classes('flex-1 gap-0'):
                ui.label('Comisión por Trade (%)').classes('text-xs text-gray-500 mb-1')
                comm_input = ui.number('Comisión (%)', value=state['commission_pct'], step=0.01, min=0.0).bind_value(state, 'commission_pct').classes('w-full')
            with ui.column().classes('flex-1 gap-0'):
                ui.label('Deslizamiento / Slippage (%)').classes('text-xs text-gray-500 mb-1')
                slip_input = ui.number('Slippage (%)', value=state['slippage_pct'], step=0.01, min=0.0).bind_value(state, 'slippage_pct').classes('w-full')

        # ── Fila 3: Botones ──
        with ui.row().classes('w-full mt-4 gap-3 items-center'):
            btn_run = ui.button(
                'EJECUTAR PRUEBA RETROSPECTIVA',
                on_click=lambda e: asyncio.create_task(run_backtest(e))
            ).classes('bg-blue-700 hover:bg-blue-800 text-white font-bold flex-1 py-3')
            
            btn_portfolio = ui.button(
                '💼 SIMULAR PORTAFOLIO (COMBINAR ESTRATEGIAS)'
            ).classes('bg-emerald-700 hover:bg-emerald-800 text-white font-bold py-3 px-5 shadow')

            btn_save = ui.button(
                '💾 GUARDAR BACKTEST',
                on_click=save_current_backtest
            ).classes('bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-3 px-5 shadow')

            btn_history = ui.button(
                '📋 HISTORIAL',
                on_click=load_history
            ).classes('bg-slate-700 hover:bg-slate-800 text-white font-bold py-3 px-5 shadow')

            btn_optimizer = ui.button(
                'EJECUTAR OPTIMIZADOR (BÚSQUEDA EN CUADRÍCULA)'
            ).classes('bg-purple-600 hover:bg-purple-700 text-white font-bold flex-1 py-3')

        # ════════════════════════════════════════════════════════
        # DIALOG DEL OPTIMIZADOR
        # ════════════════════════════════════════════════════════
        with ui.dialog().props('maximized') as optimizer_dialog, \
             ui.card().classes('w-full h-full q-pa-md overflow-auto'):

            # ── Encabezado ──
            with ui.row().classes('w-full justify-between items-center mb-4'):
                ui.label('Optimizador — Búsqueda en Cuadrícula').classes('text-2xl font-bold text-purple-700')
                ui.button(icon='close', on_click=optimizer_dialog.close).props('flat round')

            ui.label(
                'Define el rango de cada parámetro (mínimo, máximo, paso). '
                'El optimizador probará TODAS las combinaciones posibles.'
            ).classes('text-sm text-gray-500 mb-4')

            # ── Información de combinaciones ──
            lbl_combo_info = ui.label('').classes('text-sm font-semibold text-blue-700 mb-2')

            # ── Contenedor de rangos (se llena dinámicamente) ──
            opt_ranges_container = ui.column().classes('w-full gap-2')
            opt_ranges: dict = {}   # { param_name: {'min': el, 'max': el, 'step': el} }

            # ── Métrica de optimización ──
            with ui.row().classes('items-center gap-4 mt-2 mb-4'):
                ui.label('Optimizar por:').classes('font-semibold')
                opt_metric = ui.select(
                    {'sharpe_ratio': 'Coeficiente de Sharpe',
                     'cagr': 'CAGR (%)',
                     'net_pnl': 'PnL Neto',
                     'max_drawdown_pct': 'Menor Reducción Máxima'},
                    value='sharpe_ratio'
                ).classes('w-64')

            # ── Barra de progreso ──
            opt_progress = ui.linear_progress(value=0).classes('w-full').props('color=purple')
            lbl_progress = ui.label('').classes('text-xs text-gray-500 mt-1')

            # ── Tabla de resultados ──
            opt_result_cols = [
                {'name': 'rank',    'label': '#',        'field': 'rank',    'sortable': True},
                {'name': 'params',  'label': 'Parámetros','field': 'params',  'sortable': False},
                {'name': 'sharpe',  'label': 'Sharpe',   'field': 'sharpe',  'sortable': True},
                {'name': 'cagr',    'label': 'CAGR%',    'field': 'cagr',    'sortable': True},
                {'name': 'maxdd',   'label': 'Max DD%',  'field': 'maxdd',   'sortable': True},
                {'name': 'profit_factor', 'label': 'Profit Factor', 'field': 'profit_factor', 'sortable': True},
                {'name': 'trades',  'label': 'Trades',   'field': 'trades',  'sortable': True},
                {'name': 'wins', 'label': 'Ganadoras', 'field': 'wins', 'sortable': True},
                {'name': 'losses', 'label': 'Perdedoras', 'field': 'losses', 'sortable': True},
                {'name': 'win_rate', 'label': '% Ganadoras', 'field': 'win_rate', 'sortable': True},
                {'name': 'cons_losses', 'label': 'Perdedoras consec.', 'field': 'cons_losses', 'sortable': True},
                {'name': 'init_cap', 'label': 'Cap. Inicial', 'field': 'init_cap', 'sortable': True},
                {'name': 'final_eq', 'label': 'Balance Final', 'field': 'final_eq', 'sortable': True},
                {'name': 'pnl',     'label': 'PnL Neto', 'field': 'pnl',     'sortable': True},
            ]
            opt_result_table = ui.table(
                columns=opt_result_cols, rows=[], row_key='rank'
            ).classes('w-full mt-4')
            opt_result_table.add_slot('body-cell-rank', '''
                <q-td :props="props">
                    <q-badge :color="props.value <= 3 ? 'purple' : 'grey-6'" :label="'#' + props.value" />
                </q-td>
            ''')
            opt_result_table.add_slot('body-cell-sharpe', '''
                <q-td :props="props">
                    <span :style="{color: props.value >= 1 ? '#16a34a' : props.value >= 0 ? '#d97706' : '#dc2626',
                                   fontWeight: 'bold'}">
                        {{ props.value }}
                    </span>
                </q-td>
            ''')
            opt_result_table.add_slot('body-cell-cagr', '''
                <q-td :props="props">
                    <span :style="{color: props.value >= 0 ? '#16a34a' : '#dc2626', fontWeight:'bold'}">
                        {{ props.value }}%
                    </span>
                </q-td>
            ''')

            btn_run_opt = ui.button(
                'INICIAR OPTIMIZADOR',
                icon='play_arrow'
            ).classes('bg-purple-700 text-white font-bold mt-4 w-full py-3')

            # ── Funciones del optimizador ──
            def _build_opt_ranges_ui():
                """Reconstruye la UI de rangos cuando cambia la estrategia."""
                opt_ranges_container.clear()
                opt_ranges.clear()
                preview_labels = {}
                file_path = strategies.get(state['strategy_name'])
                if not file_path or not os.path.exists(file_path):
                    return
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        cfg = yaml.safe_load(f)
                    params = cfg.get('parameters', {})
                    if not params:
                        with opt_ranges_container:
                            ui.label('Esta estrategia no tiene parámetros editables.').classes('text-gray-400')
                        return

                    # Encabezado de columnas
                    with opt_ranges_container:
                        with ui.row().classes('w-full gap-2 font-semibold text-xs text-gray-500 border-b pb-1'):
                            ui.label('Parámetro').classes('w-36')
                            ui.label('Valor actual').classes('w-28 text-center')
                            ui.label('Mínimo').classes('flex-1')
                            ui.label('Máximo').classes('flex-1')
                            ui.label('Paso').classes('flex-1')
                            ui.label('Valores a probar').classes('w-48')

                        for p_name, p_val in params.items():
                            try:
                                p_num = float(p_val)
                            except (ValueError, TypeError):
                                continue  # Saltamos parámetros no numéricos

                            # Estado de rango para este parámetro
                            r = {'min': p_num, 'max': p_num * 3 if p_num > 0 else 10, 'step': max(1.0, p_num)}
                            opt_ranges[p_name] = r

                            def _update_preview(pn, pl):
                                cfg_r = opt_ranges[pn]
                                try:
                                    mn = float(cfg_r['min']); mx = float(cfg_r['max']); st = float(cfg_r['step'])
                                    vals = []
                                    v = mn
                                    while v <= mx + 1e-9:
                                        vals.append(int(v) if float(v) == int(v) else round(v, 4))
                                        v += st
                                        if len(vals) > 20: vals.append('...'); break
                                    combo_count = count_combinations(opt_ranges)
                                    pl.set_text(', '.join(str(x) for x in vals))
                                    lbl_combo_info.set_text(
                                        f'ℹ️  Total combinaciones: {combo_count:,}  '
                                        f'(tiempo estimado: ~{max(1, combo_count // 5)}s)'
                                    )
                                except Exception:
                                    pl.set_text('?')

                            with ui.row().classes('w-full gap-2 items-center py-1 border-b border-gray-100'):
                                ui.label(p_name).classes('w-36 font-bold text-purple-800')
                                ui.label(str(int(p_num) if p_num == int(p_num) else p_num)).classes('w-28 text-center text-gray-600 text-sm')

                                def _mk_field(key, pn=p_name):
                                    def _on_change(e, k=key, n=pn):
                                        try:
                                            if e.value is not None:
                                                opt_ranges[n][k] = float(e.value)
                                                if n in preview_labels:
                                                    _update_preview(n, preview_labels[n])
                                        except Exception:
                                            pass
                                    inp = ui.number(
                                        label=key.capitalize(),
                                        value=opt_ranges[pn][key],
                                        format='%.4g',
                                        on_change=_on_change
                                    ).classes('flex-1')
                                    return inp

                                _mk_field('min')
                                _mk_field('max')
                                _mk_field('step')
                                
                                plbl = ui.label('').classes('w-48 text-xs text-blue-700 font-mono self-center')
                                preview_labels[p_name] = plbl

                            _update_preview(p_name, preview_labels[p_name])

                except Exception as ex:
                    with opt_ranges_container:
                        ui.label(f'Error cargando parámetros: {ex}').classes('text-red-500')

            def open_optimizer():
                _build_opt_ranges_ui()
                optimizer_dialog.open()

            btn_optimizer.on_click(open_optimizer)

            async def _run_optimizer():
                if not state['strategy_name']:
                    ui.notify('No hay estrategia seleccionada', type='warning')
                    return
                if not opt_ranges:
                    ui.notify('No hay parámetros de rango definidos', type='warning')
                    return

                file_path = strategies.get(state['strategy_name'])
                if not file_path:
                    ui.notify('No se encontró el archivo de estrategia', type='warning')
                    return

                total_combos = count_combinations(opt_ranges)
                if total_combos > 2000:
                    ui.notify(
                        f'El grid tiene {total_combos:,} combinaciones. Reduce los rangos para evitar tiempos muy largos.',
                        type='warning', timeout=5000
                    )

                btn_run_opt.set_text(f'Calculando... (0/{total_combos})')
                btn_run_opt.props('disabled')
                opt_progress.value = 0
                lbl_progress.set_text(f'0 / {total_combos} combinaciones probadas')
                opt_result_table.rows = []
                opt_result_table.update()

                # Cargar datos del mercado
                try:
                    start_dt = datetime.strptime(state['start_date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
                    end_dt = datetime.strptime(state['end_date'], '%Y-%m-%d').replace(hour=23, minute=59, tzinfo=timezone.utc)
                    db = SessionLocal()
                    market_mgr = MarketDataManager(db)
                    df_opt = market_mgr.get_data(state['symbol'], state['timeframe'], start_dt, end_dt)
                    db.close()
                except Exception as ex:
                    ui.notify(f'Error cargando datos: {ex}', type='negative')
                    btn_run_opt.set_text('INICIAR OPTIMIZADOR')
                    btn_run_opt.props(remove='disabled')
                    return

                if df_opt.empty:
                    ui.notify('No hay datos para el par/periodo seleccionado. Descarga primero los datos.', type='warning')
                    btn_run_opt.set_text('INICIAR OPTIMIZADOR')
                    btn_run_opt.props(remove='disabled')
                    return

                # Capital inicial
                start_price = df_opt.iloc[0]['open'] if not df_opt.empty else 1.0
                if state.get('capital_type', 'QUOTE') == 'BASE':
                    initial_cap = state['capital'] * start_price
                else:
                    initial_cap = state['capital']

                done_counter = [0]
                def _progress(done, total):
                    done_counter[0] = done

                # Ejecutar en hilo para no bloquear la UI
                import copy
                param_ranges_copy = copy.deepcopy(opt_ranges)
                metric_key = opt_metric.value

                results = await run.io_bound(
                    lambda: run_grid_search(
                        file_path, df_opt, initial_cap,
                        param_ranges_copy, metric_key, _progress
                    )
                )

                # Mostrar resultados
                rows = []
                for i, r in enumerate(results, 1):
                    param_str = '  |  '.join(f"{k}={v}" for k, v in r['params'].items())
                    rows.append({
                        'rank': i,
                        'params': param_str,
                        'sharpe': r['sharpe_ratio'],
                        'cagr': r['cagr'],
                        'maxdd': f"{r['max_drawdown_pct']:.2f}%",
                        'profit_factor': r.get('profit_factor', 0),
                        'trades': r['total_trades'],
                        'wins': r.get('winning_trades', 0),
                        'losses': r.get('losing_trades', 0),
                        'win_rate': f"{r.get('percent_profitable', 0):.2f}%",
                        'cons_losses': r.get('max_consecutive_losers', 0),
                        'init_cap': r.get('initial_capital', 0),
                        'final_eq': r.get('final_equity', 0),
                        'pnl': r['net_pnl'],
                    })

                opt_result_table.rows = rows
                opt_result_table.update()
                opt_progress.value = 1.0
                lbl_progress.set_text(f'{len(results)} combinaciones completadas — ordenadas por {metric_key}')
                btn_run_opt.set_text('INICIAR OPTIMIZADOR')
                btn_run_opt.props(remove='disabled')
                ui.notify(f'Optimización completa: {len(results)} combinaciones', type='positive')

            btn_run_opt.on('click', lambda: asyncio.ensure_future(_run_optimizer()))

        ui.label('Strategy Parameters').classes('text-lg font-bold mt-6')
        params_container = ui.row().classes('w-full gap-4 flex-wrap mb-4')
        
        _last_loaded_strategy = [None]

        def update_parameters_ui(e=None, reset=False):
            params_container.clear()
            current_strat = state.get('strategy_name')
            file_path = strategies.get(current_strat)
            if not file_path or not os.path.exists(file_path):
                return
                
            try:
                import yaml
                with open(file_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f) or {}
                    
                params = config.get('parameters', {})
                
                # If strategy changed or reset requested, set defaults
                if reset or _last_loaded_strategy[0] != current_strat or 'custom_parameters' not in state or not isinstance(state['custom_parameters'], dict):
                    state['custom_parameters'] = params.copy()
                    _last_loaded_strategy[0] = current_strat
                else:
                    # Ensure all YAML params exist in state['custom_parameters']
                    for k, v in params.items():
                        if k not in state['custom_parameters']:
                            state['custom_parameters'][k] = v
                
                if not params:
                    with params_container:
                        ui.label("No configurable parameters in this strategy YAML.").classes('text-gray-500 italic mt-2')
                    return
                    
                with params_container:
                    for key, val in params.items():
                        current_val = state['custom_parameters'].get(key, val)
                        label_title = key.replace('_', ' ').title()
                        if isinstance(val, (int, float)):
                            try:
                                num_val = float(current_val)
                            except (ValueError, TypeError):
                                num_val = float(val)
                            def make_on_change_num(k):
                                def _on_change(evt):
                                    if evt.value is not None:
                                        try:
                                            val_num = float(evt.value)
                                            state['custom_parameters'][k] = int(val_num) if val_num.is_integer() else val_num
                                        except (ValueError, TypeError):
                                            state['custom_parameters'][k] = evt.value
                                return _on_change
                            ui.number(label_title, value=num_val, on_change=make_on_change_num(key)).classes('w-48')
                        else:
                            def make_on_change_str(k):
                                def _on_change(evt):
                                    if evt.value is not None:
                                        state['custom_parameters'][k] = str(evt.value)
                                return _on_change
                            ui.input(label_title, value=str(current_val), on_change=make_on_change_str(key)).classes('w-48')
            except Exception as ex:
                ui.notify(f"Error loading parameters: {ex}", type='negative')
                
        strat_combo.on_value_change(update_parameters_ui)
        update_parameters_ui() # Trigger initial load

        ui.separator().classes('my-6')
        ui.label('Resumen de resultados').classes('text-xl font-bold')
        with ui.row().classes('w-full gap-4 mt-4 flex-wrap'):
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label('CAGR').classes('text-sm text-gray-500')
                lbl_cagr = ui.label('-- %').classes('text-2xl font-bold text-green-600')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label('Reducción máxima').classes('text-sm text-gray-500')
                lbl_maxdd = ui.label('-- %').classes('text-2xl font-bold text-red-600')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label('Coeficiente de Sharpe').classes('text-sm text-gray-500')
                lbl_sharpe = ui.label('--').classes('text-2xl font-bold text-blue-600')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label('Profit Factor').classes('text-sm text-gray-500')
                lbl_profit_factor = ui.label('--').classes('text-2xl font-bold text-indigo-600')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label('Comercios totales').classes('text-sm text-gray-500')
                lbl_total_trades = ui.label('--').classes('text-2xl font-bold')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label('Ganadoras').classes('text-sm text-gray-500')
                lbl_win_trades = ui.label('--').classes('text-2xl font-bold text-green-600')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label('Perdedoras').classes('text-sm text-gray-500')
                lbl_lose_trades = ui.label('--').classes('text-2xl font-bold text-red-600')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label('% Ganadoras').classes('text-sm text-gray-500')
                lbl_win_rate = ui.label('-- %').classes('text-2xl font-bold')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                ui.label('Perdedoras consec.').classes('text-sm text-gray-500')
                lbl_max_cons_losers = ui.label('--').classes('text-2xl font-bold text-orange-600')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                lbl_hdr_init_q = ui.label('Capital Inicial (CITA)').classes('text-sm text-gray-500')
                lbl_init_quote = ui.label('--').classes('text-2xl font-bold text-white')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                lbl_hdr_init_b = ui.label('Capital Inicial (BASE)').classes('text-sm text-gray-500')
                lbl_init_base = ui.label('--').classes('text-2xl font-bold text-white')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                lbl_hdr_bal_q = ui.label('Saldo final (CITA)').classes('text-sm text-gray-500')
                lbl_bal_quote = ui.label('--').classes('text-2xl font-bold text-white')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                lbl_hdr_bal_b = ui.label('Saldo final (BASE)').classes('text-sm text-gray-500')
                lbl_bal_base = ui.label('--').classes('text-2xl font-bold text-white')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                lbl_hdr_pnl_q = ui.label('Total de pérdidas y ganancias (CITA)').classes('text-sm text-gray-500')
                lbl_pnl_quote = ui.label('--').classes('text-2xl font-bold text-white')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                lbl_hdr_pnl_b = ui.label('Total de pérdidas y ganancias (BASE)').classes('text-sm text-gray-500')
                lbl_pnl_base = ui.label('--').classes('text-2xl font-bold text-white')

        # ════════════════════════════════════════════════════════
        # PANEL DE CONTROL DE GRÁFICO Y INDICADORES TRADINGVIEW / NT8
        # ════════════════════════════════════════════════════════
        chart_config = {
            'sma_20': True,
            'sma_50': False,
            'sma_200': False,
            'ema_9': True,
            'ema_21': False,
            'ema_50': False,
            'bollinger': False,
            'vwap': False,
            'supertrend': False,
            'rsi': False,
            'macd': False,
            'atr': False,
            'show_trades': True,
            'show_trade_lines': True,
            'show_trade_labels': True,
            'trade_filter': 'ALL'
        }

        with ui.card().classes('w-full bg-slate-900 text-white p-4 rounded-2xl shadow-xl mt-6 border border-slate-800'):
            with ui.row().classes('w-full justify-between items-center mb-2'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('show_chart', size='1.8rem').classes('text-emerald-400')
                    ui.label('Gráfico de Análisis Técnico & Backtest (TradingView / NT8)').classes('text-xl font-extrabold tracking-tight')
                ui.badge('Plotly Pro Engine', color='slate-8').classes('px-3 py-1 text-xs')

            # --- Funciones auxiliares para la UI de gráficos ---
            def _set_cfg(k, v):
                chart_config[k] = v
                render_tradingview_plotly()

            def _make_toggle(key, label):
                return ui.checkbox(label, value=chart_config.get(key, False), on_change=lambda e, k=key: _set_cfg(k, e.value)).classes('text-slate-200 text-sm')

            # --- Indicadores Activos (Siempre Visibles) ---
            @ui.refreshable
            def render_price_overlays():
                with ui.row().classes('w-full gap-4 flex-wrap items-center bg-slate-800/80 p-3 rounded-xl mb-3'):
                    ui.label('Indicadores Activos:').classes('text-xs font-bold text-slate-400 uppercase tracking-wider mr-2')
                    # Renderear todos los que empiecen con sma_ o ema_
                    for key, val in chart_config.items():
                        if key.startswith('sma_') or key.startswith('ema_'):
                            label_text = f"{key.split('_')[0].upper()} {key.split('_')[1]}"
                            ui.checkbox(label_text, value=val, on_change=lambda e, k=key: _set_cfg(k, e.value)).classes('text-slate-200 text-sm font-semibold')
                    
                    # También los estáticos
                    ui.checkbox('Bandas Bollinger', value=chart_config.get('bollinger', False), on_change=lambda e: _set_cfg('bollinger', e.value)).classes('text-slate-200 text-sm font-semibold')
                    ui.checkbox('VWAP', value=chart_config.get('vwap', False), on_change=lambda e: _set_cfg('vwap', e.value)).classes('text-slate-200 text-sm font-semibold')
                    ui.checkbox('SuperTrend', value=chart_config.get('supertrend', False), on_change=lambda e: _set_cfg('supertrend', e.value)).classes('text-slate-200 text-sm font-semibold')
            
            render_price_overlays()

            # --- Panel de Otras Configuraciones (Osciladores y Filtros) ---
            with ui.expansion('⚙️ Más Configuraciones (Osciladores & Filtros de Trades)', icon='tune').classes('w-full bg-slate-800/80 text-white rounded-xl mb-3'):
                with ui.column().classes('w-full p-3 gap-3'):

                    # Fila 2: Subgráficos Osciladores
                    ui.label('Osciladores y Volumen (Subplots)').classes('text-xs font-bold text-slate-400 uppercase tracking-wider mt-1')
                    with ui.row().classes('w-full gap-4 flex-wrap items-center bg-slate-900/50 p-2 rounded-lg'):
                        _make_toggle('rsi', 'RSI (14)')
                        _make_toggle('macd', 'MACD (12, 26, 9)')
                        _make_toggle('atr', 'ATR (14)')

                    # Fila 3: Configuración de Señales de Trades
                    ui.label('Señales de Entrada / Salida de Backtest').classes('text-xs font-bold text-slate-400 uppercase tracking-wider mt-1')
                    with ui.row().classes('w-full gap-4 flex-wrap items-center bg-slate-900/50 p-2 rounded-lg'):
                        _make_toggle('show_trades', 'Mostrar Señales')
                        _make_toggle('show_trade_lines', 'Líneas de Conexión')
                        _make_toggle('show_trade_labels', 'Etiquetas PnL')
                        
                        ui.label('Filtro de Trades:').classes('text-xs text-slate-400 ml-2')
                        trade_filter_select = ui.select(
                            {'ALL': 'Todos los Trades', 'WINS': 'Solo Ganadores (W)', 'LOSSES': 'Solo Perdedores (L)', 'LONG': 'Solo Long', 'SHORT': 'Solo Short'},
                            value=chart_config['trade_filter'],
                            on_change=lambda e: _set_cfg('trade_filter', e.value)
                        ).classes('w-44 bg-slate-800 text-white text-xs')

            # Contenedor principal del gráfico Plotly
            plotly_chart_container = ui.column().classes('w-full overflow-hidden rounded-xl border border-slate-700/60 bg-slate-950')

        async def render_tradingview_plotly(zoom_dates=None):
            plotly_chart_container.clear()
            df = state.get('last_df', pd.DataFrame())
            trades_df = state.get('last_trades_df')
            symbol = state.get('symbol', 'BTC/USDT')
            timeframe = state.get('timeframe', '1d')

            fig = await run.io_bound(
                build_tradingview_plotly_figure,
                df,
                trades_df,
                chart_config,
                symbol,
                timeframe
            )

            if zoom_dates and len(zoom_dates) == 2:
                fig.update_xaxes(range=zoom_dates)

            with plotly_chart_container:
                # Pasar scrollZoom: True para permitir alejar/acercar con la rueda del ratón
                # y permitir estirar el gráfico
                ui.plotly(fig).props('config="{scrollZoom: true, responsive: true, displayModeBar: true}"').classes('w-full h-[780px]')

        # Renderizado inicial no bloqueante
        import asyncio
        ui.timer(0.1, lambda: asyncio.create_task(render_tradingview_plotly()), once=True)


        with ui.row().classes('w-full items-center justify-between mt-6'):
            ui.label('Equity Curve').classes('text-lg font-bold')
            equity_toggle = ui.toggle(['QUOTE', 'BASE'], value='QUOTE').props('color=blue size=sm')

        chart = ui.echart({
            'tooltip': {'trigger': 'axis'},
            'xAxis': {'type': 'category', 'data': []},
            'yAxis': {'type': 'value', 'scale': True},
            'series': [{'name': 'Equity', 'type': 'line', 'data': []}],
        }).classes('w-full h-96 border rounded-lg p-2 bg-slate-900/50 mt-2')

        def _on_equity_toggle_change(e):
            if not state.get('equity_dates'): return
            is_quote = (e.value == 'QUOTE')
            chart.options['series'][0]['data'] = state['equity_quote_data'] if is_quote else state['equity_base_data']
            
            base_a = state['symbol'].split('/')[0] if '/' in state['symbol'] else 'BASE'
            quote_a = state['symbol'].split('/')[1] if '/' in state['symbol'] else 'QUOTE'
            chart.options['series'][0]['name'] = f"Equity ({quote_a if is_quote else base_a})"
            chart.update()
            
        equity_toggle.on_value_change(_on_equity_toggle_change)

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
        }).classes('w-full h-64 border rounded-lg p-2 bg-slate-900/50 mt-2')

        ui.label('Ejecuciones (Operaciones)').classes('text-lg font-bold mt-6 mb-2')
        trades_columns = []
        with ui.card().classes('w-full overflow-x-auto p-2 bg-slate-900/50 rounded-xl shadow-lg border border-slate-700/50 mt-2'):
            trades_table = ui.table(columns=trades_columns, rows=[], row_key='entry_time').classes('w-full min-w-[1100px]')
        
        trades_table.add_slot('body-cell-result', '''
            <q-td :props="props">
                <q-badge :color="props.value === 'W' ? 'positive' : 'negative'"
                         :label="props.value === 'W' ? '✓ WIN' : '✗ LOSS'"
                         class="px-2 py-1 text-xs font-bold text-white shadow-lg" />
            </q-td>
        ''')
        trades_table.add_slot('body-cell-side', '''
            <q-td :props="props">
                <q-badge :color="props.value === 'LONG' ? 'blue-8' : 'purple-8'"
                         :label="props.value"
                         class="px-2 py-1 text-xs font-bold text-white shadow-lg" />
            </q-td>
        ''')
        trades_table.add_slot('body-cell-pnl_pct', '''
            <q-td :props="props">
                <span :style="{ color: props.value >= 0 ? '#16a34a' : '#dc2626', fontWeight: 'bold' }">
                    {{ props.value > 0 ? '+' : '' }}{{ props.value != null ? Number(props.value).toFixed(2) + '%' : '0.00%' }}
                </span>
            </q-td>
        ''')
        trades_table.add_slot('body-cell-exit_reason', '''
            <q-td :props="props">
                <q-badge
                    :color="props.value === 'TP' ? 'positive' : props.value === 'SL' ? 'negative' : 'primary'"
                    class="px-2.5 py-1 text-xs font-bold text-white shadow-lg"
                >
                    {{ props.value === 'TP' ? 'TP' : props.value === 'SL' ? 'SL' : props.value === 'Signal' || props.value === 'Señal' ? 'Señal' : props.value }}
                </q-badge>
            </q-td>
        ''')

        def _sync_load_and_run(strategy_path, custom_params, symbol, timeframe, start_dt, end_dt, initial_capital, sizing_mode, comm_pct, slip_pct):
            db = SessionLocal()
            try:
                market_mgr = MarketDataManager(db)
                df = market_mgr.get_data(symbol, timeframe, start_dt, end_dt)
                if df.empty:
                    market_mgr.update_historical_data(symbol, timeframe, start_dt, end_dt)
                    df = market_mgr.get_data(symbol, timeframe, start_dt, end_dt)
                    
                if df.empty:
                    return {'error': f"No hay datos históricos disponibles para {symbol} en {timeframe}."}
                    
                strategy = BaseStrategy(strategy_path, custom_parameters=custom_params)
                strategy.symbol = symbol
                strategy.timeframe = timeframe
                
                if 'risk_management' not in strategy.config:
                    strategy.config['risk_management'] = {}
                    
                if 'Riesgo Fijo' in sizing_mode:
                    strategy.config['risk_management']['position_sizing'] = {'method': 'fixed_fractional', 'risk_per_trade_pct': 1.0}
                elif 'Monto Fijo' in sizing_mode:
                    strategy.config['risk_management']['position_sizing'] = {'method': 'fixed_amount', 'value': 1000.0}
                else:
                    strategy.config['risk_management']['position_sizing'] = {'method': 'compounding', 'value': 100.0}
                    
                strategy.risk_manager = RiskManager(strategy.config['risk_management'])
                
                backtester = Backtester(strategy, initial_capital=initial_capital, commission_pct=comm_pct, slippage_pct=slip_pct)
                results = backtester.run(df)
                results['df'] = df
                
                # Extraer indicadores de la estrategia para graficarlos dinámicamente
                strategy_indicators = []
                for rule_type in ['entry_conditions', 'exit_conditions']:
                    rules = strategy.config.get(rule_type, {}).get('rules', [])
                    for rule in rules:
                        if rule.get('type') == 'technical_indicator':
                            for prefix in ['1', '2']:
                                ind = rule.get(f'indicator{prefix}')
                                per = rule.get(f'period{prefix}')
                                if ind and per:
                                    try:
                                        period_val = strategy.config.get('parameters', {}).get(per, per)
                                        strategy_indicators.append((ind.lower(), int(period_val)))
                                    except ValueError:
                                        pass
                results['strategy_indicators'] = list(set(strategy_indicators))
                
                return results
            finally:
                db.close()

def _sync_run_portfolio_backtest(portfolio_items, total_capital, start_dt, end_dt, comm_pct, slip_pct):
    db = SessionLocal()
    try:
        market_mgr = MarketDataManager(db)
        equity_series_dict = {}
        all_trades = []
        strategy_breakdown = []

        for idx, item in enumerate(portfolio_items):
            strategy_path = item['strategy_path']
            symbol = item['symbol']
            timeframe = item['timeframe']
            weight_pct = float(item.get('weight_pct', 0.0))
            if weight_pct <= 0:
                continue

            allocated_cap = total_capital * (weight_pct / 100.0)

            df = market_mgr.get_data(symbol, timeframe, start_dt, end_dt)
            if df.empty:
                market_mgr.update_historical_data(symbol, timeframe, start_dt, end_dt)
                df = market_mgr.get_data(symbol, timeframe, start_dt, end_dt)

            if df.empty:
                continue

            custom_params = item.get('custom_params', {})
            strategy = BaseStrategy(strategy_path, custom_parameters=custom_params)
            strategy.symbol = symbol
            strategy.timeframe = timeframe

            if 'risk_management' not in strategy.config:
                strategy.config['risk_management'] = {}
            strategy.config['risk_management']['position_sizing'] = {'method': 'compounding', 'value': 100.0}
            strategy.risk_manager = RiskManager(strategy.config['risk_management'])

            backtester = Backtester(strategy, initial_capital=allocated_cap, commission_pct=comm_pct, slippage_pct=slip_pct)
            results = backtester.run(df)

            eq_curve = results.get('equity_curve')
            name_label = f"Strat {idx+1}: {os.path.basename(strategy_path)} ({symbol} {timeframe})"
            
            if eq_curve is not None and not eq_curve.empty:
                if 'timestamp' in eq_curve.columns:
                    eq_curve = eq_curve.set_index('timestamp')
                eq_curve.index = pd.to_datetime(eq_curve.index)
                equity_series_dict[name_label] = eq_curve['equity']

            trades = results.get('trades')
            if trades is not None and not trades.empty:
                trades_copy = trades.copy()
                trades_copy['strategy'] = name_label
                all_trades.append(trades_copy)

            strat_cagr = results.get('cagr', 0.0)
            strat_max_dd = results.get('max_drawdown_pct', 0.0)
            strat_final_eq = results.get('final_equity', allocated_cap)
            strat_pnl_pct = ((strat_final_eq - allocated_cap) / allocated_cap * 100.0) if allocated_cap > 0 else 0.0

            strategy_breakdown.append({
                'name': name_label,
                'weight_pct': weight_pct,
                'allocated_cap': allocated_cap,
                'final_cap': strat_final_eq,
                'pnl_pct': strat_pnl_pct,
                'cagr': strat_cagr,
                'max_dd': strat_max_dd,
                'trades_count': len(trades) if trades is not None else 0
            })

        if not equity_series_dict:
            return {'error': "No se pudieron obtener datos o generar equidad para las estrategias del portafolio."}

        combined_df = pd.DataFrame(equity_series_dict).ffill().bfill()
        portfolio_equity = combined_df.sum(axis=1)

        port_eq_metrics = calculate_equity_curve_metrics(portfolio_equity)

        combined_trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
        port_trade_metrics = calculate_metrics(combined_trades, total_capital) if not combined_trades.empty else {}

        # Calculate Drawdown series (%)
        roll_max = portfolio_equity.cummax()
        drawdown_series = (portfolio_equity - roll_max) / roll_max * 100.0

        # Calculate individual drawdown series (%)
        individual_drawdowns = {}
        for col in combined_df.columns:
            s_series = combined_df[col]
            s_roll_max = s_series.cummax()
            s_dd = (s_series - s_roll_max) / s_roll_max * 100.0
            individual_drawdowns[col] = s_dd.round(2).tolist()

        dates_str = portfolio_equity.index.strftime('%Y-%m-%d').tolist()

        total_trades_count = len(combined_trades)
        winning_trades_count = int(port_trade_metrics.get('winning_trades', 0))
        losing_trades_count = int(port_trade_metrics.get('losing_trades', 0))
        if winning_trades_count == 0 and losing_trades_count == 0 and not combined_trades.empty:
            pnl_col = 'pnl' if 'pnl' in combined_trades.columns else 'net_pnl' if 'net_pnl' in combined_trades.columns else None
            if pnl_col:
                winning_trades_count = int((combined_trades[pnl_col] > 0).sum())
                losing_trades_count = int((combined_trades[pnl_col] < 0).sum())

        win_rate_val = port_trade_metrics.get('percent_profitable', 0.0)
        if win_rate_val == 0.0 and total_trades_count > 0:
            win_rate_val = (winning_trades_count / total_trades_count) * 100.0

        # Formatear y ordenar operaciones cronológicamente
        formatted_trades = []
        if not combined_trades.empty:
            sort_col = None
            for candidate in ['entry_time', 'Entry Timestamp', 'timestamp', 'date', 'entry_date']:
                if candidate in combined_trades.columns:
                    sort_col = candidate
                    break

            if sort_col:
                try:
                    combined_trades[sort_col] = pd.to_datetime(combined_trades[sort_col])
                    combined_trades = combined_trades.sort_values(by=sort_col, ascending=True)
                except Exception:
                    pass

            for trade_idx, tr in combined_trades.iterrows():
                e_time = tr.get('entry_time') or tr.get('Entry Timestamp') or tr.get('timestamp') or "N/A"
                if isinstance(e_time, (pd.Timestamp, datetime)):
                    e_time_str = e_time.strftime("%Y-%m-%d %H:%M")
                else:
                    e_time_str = str(e_time)[:16]

                x_time = tr.get('exit_time') or tr.get('Exit Timestamp') or "N/A"
                if isinstance(x_time, (pd.Timestamp, datetime)):
                    x_time_str = x_time.strftime("%Y-%m-%d %H:%M")
                else:
                    x_time_str = str(x_time)[:16]

                pnl_val = float(tr.get('pnl', 0.0) or 0.0)
                entry_p = float(tr.get('entry_price', 0.0) or 0.0)
                exit_p = float(tr.get('exit_price', 0.0) or 0.0)
                side_val = str(tr.get('side', 'LONG')).upper()

                if 'pnl_pct' in tr and pd.notna(tr['pnl_pct']):
                    pnl_pct_val = float(tr['pnl_pct'])
                elif entry_p > 0:
                    if side_val == 'LONG':
                        pnl_pct_val = ((exit_p - entry_p) / entry_p) * 100.0
                    else:
                        pnl_pct_val = ((entry_p - exit_p) / entry_p) * 100.0
                else:
                    pnl_pct_val = 0.0

                pnl_pct_str = f"{'+' if pnl_pct_val > 0 else ''}{pnl_pct_val:.2f}%"

                formatted_trades.append({
                    'trade_id': trade_idx,
                    'entry_time': e_time_str,
                    'exit_time': x_time_str,
                    'strategy': str(tr.get('strategy', 'N/A')),
                    'side': side_val,
                    'entry_price': f"${entry_p:,.4f}" if entry_p > 0 else "$0.00",
                    'exit_price': f"${exit_p:,.4f}" if exit_p > 0 else "$0.00",
                    'pnl_raw': pnl_val,
                    'pnl_str': f"{'+' if pnl_val > 0 else ''}${pnl_val:,.2f}",
                    'pnl_pct_raw': pnl_pct_val,
                    'pnl_pct_str': pnl_pct_str,
                    'reason': str(tr.get('exit_reason', 'Señal'))
                })

        return {
            'portfolio_equity': portfolio_equity.round(2).tolist(),
            'individual_equity': {col: combined_df[col].round(2).tolist() for col in combined_df.columns},
            'drawdown_series': drawdown_series.round(2).tolist(),
            'individual_drawdowns': individual_drawdowns,
            'dates': dates_str,
            'cagr': port_eq_metrics.get('cagr', 0.0),
            'max_drawdown_pct': port_eq_metrics.get('max_drawdown_pct', 0.0),
            'sharpe_ratio': port_eq_metrics.get('sharpe_ratio', 0.0),
            'profit_factor': port_trade_metrics.get('profit_factor', 0.0),
            'total_trades': total_trades_count,
            'winning_trades': winning_trades_count,
            'losing_trades': losing_trades_count,
            'win_rate': win_rate_val,
            'chronological_trades': formatted_trades,
            'strategy_breakdown': strategy_breakdown,
            'initial_capital': total_capital,
            'final_equity': float(portfolio_equity.iloc[-1]) if not portfolio_equity.empty else total_capital
        }
    finally:
        db.close()

    # ════════════════════════════════════════════════════════
    # DIALOG DEL SIMULADOR DE PORTAFOLIO Y COMBINACIÓN
    # ════════════════════════════════════════════════════════
    with ui.dialog().props('maximized') as portfolio_dialog, \
         ui.card().classes('w-full h-full q-pa-md overflow-auto bg-slate-50'):

        with ui.row().classes('w-full justify-between items-center mb-4 bg-slate-900 text-white p-4 rounded-xl shadow'):
            with ui.row().classes('items-center gap-3'):
                ui.icon('pie_chart', size='2.5rem').classes('text-emerald-400')
                with ui.column().classes('gap-0'):
                    ui.label('Simulador de Portafolio & Combinación de Estrategias').classes('text-2xl font-bold')
                    ui.label('Asigna porcentajes de tu capital a múltiples estrategias para evaluar el rendimiento consolidado.').classes('text-xs text-slate-300')
            ui.button(icon='close', on_click=portfolio_dialog.close).props('flat round text-color=white')

        # Global inputs for Portfolio
        with ui.card().classes('w-full p-4 mb-4 rounded-xl border border-slate-700/50 shadow-lg'):
            ui.label('Configuración Global del Portafolio').classes('font-bold text-slate-200 mb-2')
            with ui.row().classes('w-full gap-4 items-center'):
                port_capital_input = ui.number('Capital Total Inicial ($)', value=10000.0, step=500.0, min=10.0).classes('flex-1')
                port_start_input = ui.input('Fecha Inicio', value='2024-01-01').classes('flex-1')
                port_end_input = ui.input('Fecha Fin', value=datetime.now().strftime('%Y-%m-%d')).classes('flex-1')
                port_comm_input = ui.number('Comisión (%)', value=0.1, step=0.01).classes('w-32')
                port_slip_input = ui.number('Slippage (%)', value=0.05, step=0.01).classes('w-32')

        # Table/List of Portfolio Items
        portfolio_state = {
            'items': [
                {'strategy': list(strategies.keys())[0] if strategies else '', 'symbol': available_symbols[0], 'timeframe': '1d', 'weight_pct': 50.0},
                {'strategy': list(strategies.keys())[0] if strategies else '', 'symbol': available_symbols[min(1, len(available_symbols)-1)], 'timeframe': '4h', 'weight_pct': 50.0}
            ]
        }

        port_items_container = ui.column().classes('w-full gap-3 mb-4')

        def get_strategy_default_params(strat_name):
            file_path = strategies.get(strat_name)
            if not file_path or not os.path.exists(file_path):
                return {}
            try:
                import yaml
                with open(file_path, 'r', encoding='utf-8') as f:
                    cfg = yaml.safe_load(f)
                return cfg.get('parameters', {}) or {}
            except Exception:
                return {}

        def fetch_saved_runs():
            db = SessionLocal()
            try:
                runs = db.query(BacktestRun).order_by(BacktestRun.created_at.desc()).all()
                opts = {}
                runs_map = {}
                for r in runs:
                    dt_s = r.created_at.strftime('%Y-%m-%d %H:%M') if r.created_at else ''
                    cagr_s = f"CAGR: {r.cagr:.1f}%" if r.cagr is not None else ""
                    lbl = f"[#{r.run_id}] {r.strategy_name} ({r.symbol} {r.timeframe}) {cagr_s} | {dt_s}"
                    opts[r.run_id] = lbl
                    runs_map[r.run_id] = r
                return opts, runs_map
            except Exception:
                return {}, {}
            finally:
                db.close()

        def refresh_portfolio_items_ui():
            port_items_container.clear()
            saved_opts, saved_map = fetch_saved_runs()

            with port_items_container:
                for i, item in enumerate(portfolio_state['items']):
                    if 'custom_params' not in item or not item['custom_params']:
                        item['custom_params'] = get_strategy_default_params(item['strategy']).copy()

                    with ui.card().classes('w-full p-4 rounded-xl border border-slate-700/50 shadow-xs bg-slate-900/50 mb-2'):
                        if saved_opts:
                            with ui.row().classes('w-full mb-2 items-center gap-2 bg-slate-50 p-2 rounded-lg border border-slate-700/50'):
                                ui.icon('cloud_download', size='1.2rem').classes('text-blue-600')
                                saved_sel = ui.select(saved_opts, label='📥 Cargar Parámetros desde Backtest Guardado', value=item.get('saved_run_id')).classes('flex-1')

                                def make_saved_load_handler(idx=i, s_map=saved_map):
                                    def on_saved_change(e):
                                        run_id = e.value
                                        if run_id and run_id in s_map:
                                            run_obj = s_map[run_id]
                                            portfolio_state['items'][idx]['saved_run_id'] = run_id
                                            if run_obj.strategy_name in strategies:
                                                portfolio_state['items'][idx]['strategy'] = run_obj.strategy_name
                                            if run_obj.symbol:
                                                portfolio_state['items'][idx]['symbol'] = run_obj.symbol
                                            if run_obj.timeframe:
                                                portfolio_state['items'][idx]['timeframe'] = run_obj.timeframe
                                                
                                            if run_obj.config_snapshot:
                                                try:
                                                    cfg = json.loads(run_obj.config_snapshot)
                                                    if 'custom_parameters' in cfg:
                                                        portfolio_state['items'][idx]['custom_params'] = cfg['custom_parameters'].copy()
                                                except Exception:
                                                    pass
                                            refresh_portfolio_items_ui()
                                            ui.notify(f"✅ Estrategia #{idx+1} cargada desde Historial #{run_id}", type="positive")
                                    return on_saved_change

                                saved_sel.on_value_change(make_saved_load_handler(i, saved_map))

                        with ui.row().classes('w-full gap-3 items-center mb-2'):
                            ui.label(f"Estrategia #{i+1}").classes('font-bold text-slate-300 w-24')
                            strat_sel = ui.select(list(strategies.keys()), label='Estrategia', value=item['strategy']).classes('flex-1')
                            sym_sel = ui.select(available_symbols, label='Símbolo', value=item['symbol']).classes('w-44')
                            tf_sel = ui.select(['1m', '5m', '15m', '1h', '4h', '1d'], label='TF', value=item['timeframe']).classes('w-28')
                            weight_num = ui.number('Peso (%)', value=item['weight_pct'], min=0.0, max=100.0, step=5.0).classes('w-28')

                            def remove_item(idx_to_remove=i):
                                if len(portfolio_state['items']) <= 1:
                                    ui.notify("Debe existir al menos 1 estrategia en el portafolio", type="warning")
                                    return
                                portfolio_state['items'].pop(idx_to_remove)
                                refresh_portfolio_items_ui()

                            ui.button(icon='delete', on_click=lambda idx=i: remove_item(idx)).props('flat round color=negative')

                        # Expansion panel for Strategy Parameters
                        with ui.expansion('⚙️ Parámetros de la Estrategia', icon='tune').classes('w-full bg-slate-50 border border-slate-700/50 rounded-lg p-2'):
                            params_row = ui.row().classes('w-full gap-3 flex-wrap items-center')
                            with params_row:
                                cur_params = item['custom_params']
                                if not cur_params:
                                    ui.label('No hay parámetros configurables para esta estrategia.').classes('text-xs text-slate-400 italic')
                                else:
                                    for p_key, p_val in cur_params.items():
                                        if isinstance(p_val, (int, float)):
                                            p_num = ui.number(f"{p_key}", value=p_val).classes('w-32')
                                            def make_change_handler(idx=i, pk=p_key):
                                                def on_p_change(e):
                                                    portfolio_state['items'][idx]['custom_params'][pk] = float(e.value or 0.0)
                                                return on_p_change
                                            p_num.on_value_change(make_change_handler(i, p_key))
                                        else:
                                            p_inp = ui.input(f"{p_key}", value=str(p_val)).classes('w-32')
                                            def make_change_str_handler(idx=i, pk=p_key):
                                                def on_p_str_change(e):
                                                    portfolio_state['items'][idx]['custom_params'][pk] = e.value
                                                return on_p_str_change
                                            p_inp.on_value_change(make_change_str_handler(i, p_key))

                        def update_item_val(val_idx=i, s_sel=strat_sel, sym_s=sym_sel, t_s=tf_sel, w_n=weight_num):
                            old_strat = portfolio_state['items'][val_idx]['strategy']
                            new_strat = s_sel.value
                            portfolio_state['items'][val_idx]['strategy'] = new_strat
                            portfolio_state['items'][val_idx]['symbol'] = sym_s.value
                            portfolio_state['items'][val_idx]['timeframe'] = t_s.value
                            portfolio_state['items'][val_idx]['weight_pct'] = float(w_n.value or 0.0)

                            if old_strat != new_strat:
                                portfolio_state['items'][val_idx]['custom_params'] = get_strategy_default_params(new_strat).copy()
                                refresh_portfolio_items_ui()

                        strat_sel.on_value_change(lambda e, idx=i: update_item_val(idx))
                        sym_sel.on_value_change(lambda e, idx=i: update_item_val(idx))
                        tf_sel.on_value_change(lambda e, idx=i: update_item_val(idx))
                        weight_num.on_value_change(lambda e, idx=i: update_item_val(idx))

        refresh_portfolio_items_ui()

        with ui.row().classes('w-full gap-3 mb-6 items-center'):
            def add_portfolio_item():
                default_strat = list(strategies.keys())[0] if strategies else ''
                portfolio_state['items'].append({
                    'strategy': default_strat,
                    'symbol': available_symbols[0],
                    'timeframe': '1d',
                    'weight_pct': 0.0,
                    'custom_params': get_strategy_default_params(default_strat).copy()
                })
                refresh_portfolio_items_ui()

            def rebalance_equal_weights():
                n = len(portfolio_state['items'])
                if n > 0:
                    eq_w = round(100.0 / n, 2)
                    for item in portfolio_state['items']:
                        item['weight_pct'] = eq_w
                    refresh_portfolio_items_ui()
                    ui.notify(f"Pesos distribuidos equitativamente ({eq_w}% por estrategia)", type="info")

            ui.button('➕ Agregar Estrategia al Portafolio', on_click=add_portfolio_item).classes('bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded-xl')
            ui.button('⚖️ Distribuir Capital Equitativamente', on_click=rebalance_equal_weights).classes('bg-slate-700 hover:bg-slate-800 text-white font-bold py-2 px-4 rounded-xl')

        # Action Button
        btn_run_portfolio = ui.button('⚡ EJECUTAR SIMULACIÓN DE PORTAFOLIO COMBINADO').classes('w-full bg-emerald-700 hover:bg-emerald-800 text-white font-extrabold py-4 text-lg rounded-xl shadow-lg')

        # Results Section
        port_results_container = ui.column().classes('w-full mt-6 gap-4')

        async def run_portfolio_simulation():
            p_client = ui.context.client
            total_w = sum(float(item['weight_pct']) for item in portfolio_state['items'])
            if abs(total_w - 100.0) > 1.0:
                ui.notify(f"La suma de los pesos del portafolio debe ser 100% (actualmente es {total_w:.1f}%)", type="warning")

            btn_run_portfolio.disable()
            btn_run_portfolio.set_text("⏳ Calculando simulación de portafolio...")
            notify_p = ui.notify("⚡ Calculando simulación combinada del portafolio... Por favor espera", type="info", spinner=True, timeout=3.0)

            try:
                p_start = datetime.strptime(port_start_input.value, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                p_end = datetime.strptime(port_end_input.value, '%Y-%m-%d').replace(hour=23, minute=59, tzinfo=timezone.utc)
                tot_cap = float(port_capital_input.value or 10000.0)
                c_pct = float(port_comm_input.value or 0.1)
                s_pct = float(port_slip_input.value or 0.05)

                items_for_sync = []
                for item in portfolio_state['items']:
                    s_name = item['strategy']
                    items_for_sync.append({
                        'strategy_path': strategies.get(s_name, s_name),
                        'symbol': item['symbol'],
                        'timeframe': normalize_timeframe(item['timeframe']),
                        'weight_pct': float(item['weight_pct']),
                        'custom_params': item.get('custom_params', {})
                    })

                res = await run.io_bound(
                    _sync_run_portfolio_backtest,
                    items_for_sync,
                    tot_cap,
                    p_start,
                    p_end,
                    c_pct,
                    s_pct
                )

                with p_client:
                    if 'error' in res:
                        ui.notify(res['error'], type="warning")
                        return

                    port_results_container.clear()
                    with port_results_container:
                        # 1. Summary Cards
                        with ui.row().classes('w-full gap-4 flex-wrap'):
                            with ui.card().classes('flex-1 bg-slate-900/50 p-4 rounded-xl border border-slate-700/50 shadow-lg'):
                                ui.label('CAGR del Portafolio').classes('text-xs text-slate-500 uppercase font-bold')
                                ui.label(f"{res['cagr']:.2f}%").classes('text-2xl font-black text-emerald-600')

                            with ui.card().classes('flex-1 bg-slate-900/50 p-4 rounded-xl border border-slate-700/50 shadow-lg'):
                                ui.label('Max Drawdown Combinado').classes('text-xs text-slate-500 uppercase font-bold')
                                ui.label(f"{res['max_drawdown_pct']:.2f}%").classes('text-2xl font-black text-red-600')

                            with ui.card().classes('flex-1 bg-slate-900/50 p-4 rounded-xl border border-slate-700/50 shadow-lg'):
                                ui.label('Profit Factor (PF)').classes('text-xs text-slate-500 uppercase font-bold')
                                ui.label(f"{res['profit_factor']:.2f}").classes('text-2xl font-black text-purple-600')

                            with ui.card().classes('flex-1 bg-slate-900/50 p-4 rounded-xl border border-slate-700/50 shadow-lg'):
                                ui.label('Trades Totales').classes('text-xs text-slate-500 uppercase font-bold')
                                ui.label(f"{res['total_trades']}").classes('text-2xl font-black text-blue-600')

                            with ui.card().classes('flex-1 bg-slate-900/50 p-4 rounded-xl border border-slate-700/50 shadow-lg'):
                                ui.label('Ganadoras / Perdedoras').classes('text-xs text-slate-500 uppercase font-bold')
                                with ui.row().classes('items-center gap-2 mt-1'):
                                    ui.label(f"🟢 {res['winning_trades']}").classes('text-lg font-bold text-emerald-600')
                                    ui.label(f"/ 🔴 {res['losing_trades']}").classes('text-lg font-bold text-red-600')
                                    ui.label(f"({res['win_rate']:.1f}%)").classes('text-xs font-semibold text-slate-500')

                            with ui.card().classes('flex-1 bg-slate-900/50 p-4 rounded-xl border border-slate-700/50 shadow-lg'):
                                ui.label('Capital Final Portafolio').classes('text-xs text-slate-500 uppercase font-bold')
                                ui.label(f"${res['final_equity']:,.2f}").classes('text-2xl font-black text-slate-900')

                        # 2. Curva de Capital Combinada Chart
                        palette_colors = ['#2563eb', '#9333ea', '#d97706', '#0891b2', '#e11d48', '#65a30d', '#0284c7']
                            
                        equity_series_list = []
                        # Estrategias individuales
                        for idx, (k, v) in enumerate(res['individual_equity'].items()):
                            col_c = palette_colors[idx % len(palette_colors)]
                            equity_series_list.append({
                                'name': k,
                                'type': 'line',
                                'data': v,
                                'smooth': True,
                                'z': 3,
                                'lineStyle': {'width': 2, 'type': 'solid', 'color': col_c},
                                'showSymbol': False
                            })
                        # Total Portafolio Combinado (Resaltado en Verde Esmeralda Grueso)
                        equity_series_list.append({
                            'name': '★ PORTAFOLIO TOTAL COMBINADO',
                            'type': 'line',
                            'data': res['portfolio_equity'],
                            'smooth': True,
                            'z': 10,
                            'lineStyle': {'width': 4, 'color': '#059669'},
                            'areaStyle': {'opacity': 0.15, 'color': '#059669'}
                        })

                        with ui.card().classes('w-full bg-slate-900/50 p-4 rounded-xl border border-slate-700/50 shadow-lg mt-4'):
                            ui.label('📈 Curvas de Capital Individuales y Total Combinado').classes('text-lg font-bold text-slate-200 mb-2')
                            port_chart_options = {
                                'title': {'text': 'Comparativa de Equidad ($) por Estrategia vs Portafolio Total', 'left': 'center', 'textStyle': {'fontSize': 14}},
                                'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'cross'}},
                                'legend': {'data': [s['name'] for s in equity_series_list], 'bottom': 0},
                                'xAxis': {'type': 'category', 'data': res['dates']},
                                'yAxis': {'type': 'value', 'scale': True},
                                'series': equity_series_list
                            }
                            ui.echart(port_chart_options).classes('w-full h-80')

                        # 3. Drawdown Real Combinado Chart
                        dd_series_list = []
                        # Drawdowns individuales
                        for idx, (k, v) in enumerate(res.get('individual_drawdowns', {}).items()):
                            col_c = palette_colors[idx % len(palette_colors)]
                            dd_series_list.append({
                                'name': f"DD {k}",
                                'type': 'line',
                                'data': v,
                                'smooth': True,
                                'z': 3,
                                'lineStyle': {'width': 1.5, 'type': 'dashed', 'color': col_c},
                                'showSymbol': False
                            })
                        # Drawdown Total (Resaltado en Rojo Grueso)
                        dd_series_list.append({
                            'name': '★ DRAWDOWN TOTAL COMBINADO (%)',
                            'type': 'line',
                            'data': res['drawdown_series'],
                            'smooth': True,
                            'z': 10,
                            'lineStyle': {'width': 3.5, 'color': '#dc2626'},
                            'areaStyle': {'opacity': 0.15, 'color': '#dc2626'}
                        })

                        with ui.card().classes('w-full bg-slate-900/50 p-4 rounded-xl border border-slate-700/50 shadow-lg mt-4'):
                            ui.label('📉 Caídas (Drawdown %) Individuales y Total Combinado').classes('text-lg font-bold text-slate-200 mb-2')
                            dd_chart_options = {
                                'title': {'text': 'Drawdown Relativo (%) por Estrategia y Combinado', 'left': 'center', 'textStyle': {'fontSize': 14}},
                                'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'cross'}},
                                'legend': {'data': [s['name'] for s in dd_series_list], 'bottom': 0},
                                'xAxis': {'type': 'category', 'data': res['dates']},
                                'yAxis': {'type': 'value', 'max': 0, 'scale': True, 'axisLabel': {'formatter': '{value}%'}},
                                'series': dd_series_list
                            }
                            ui.echart(dd_chart_options).classes('w-full h-72')

                        # 4. Tabla Desglosada por Estrategia
                        ui.label('Desglose Individual de Rendimiento por Estrategia').classes('text-lg font-bold text-slate-200 mt-4')
                        breakdown_cols = [
                            {'name': 'name', 'label': 'Estrategia / Par', 'field': 'name'},
                            {'name': 'weight_pct', 'label': 'Peso (%)', 'field': 'weight_pct'},
                            {'name': 'allocated_cap', 'label': 'Capital Inicial ($)', 'field': 'allocated_cap'},
                            {'name': 'final_cap', 'label': 'Capital Final ($)', 'field': 'final_cap'},
                            {'name': 'pnl_pct', 'label': 'Retorno (%)', 'field': 'pnl_pct'},
                            {'name': 'cagr', 'label': 'CAGR (%)', 'field': 'cagr'},
                            {'name': 'max_dd', 'label': 'Max DD (%)', 'field': 'max_dd'},
                            {'name': 'trades_count', 'label': 'Trades', 'field': 'trades_count'},
                        ]
                        bd_rows = []
                        for bd in res['strategy_breakdown']:
                            bd_rows.append({
                                'name': bd['name'],
                                'weight_pct': f"{bd['weight_pct']:.1f}%",
                                'allocated_cap': f"${bd['allocated_cap']:,.2f}",
                                'final_cap': f"${bd['final_cap']:,.2f}",
                                'pnl_pct': f"{bd['pnl_pct']:.2f}%",
                                'cagr': f"{bd['cagr']:.2f}%",
                                'max_dd': f"{bd['max_dd']:.2f}%",
                                'trades_count': bd['trades_count']
                            })
                        ui.table(columns=breakdown_cols, rows=bd_rows).classes('w-full bg-slate-900/50 shadow-lg rounded-xl')

                        # 5. Tabla Sucesión Cronológica de Operaciones
                        with ui.row().classes('w-full items-center justify-between mt-6 mb-2'):
                            ui.label('📜 Sucesión Cronológica de Operaciones (Portafolio Combinado)').classes('text-lg font-bold text-slate-200')
                            trade_search_input = ui.input(placeholder='Buscar operación o estrategia...').props('dense outlined clearable icon=search').classes('w-72')

                        trades_columns = [
                            {'name': 'entry_time', 'label': 'Fecha Entrada', 'field': 'entry_time', 'sortable': True},
                            {'name': 'exit_time', 'label': 'Fecha Salida', 'field': 'exit_time', 'sortable': True},
                            {'name': 'strategy', 'label': 'Estrategia / Par', 'field': 'strategy', 'sortable': True},
                            {'name': 'side', 'label': 'Tipo', 'field': 'side', 'sortable': True},
                            {'name': 'entry_price', 'label': 'Precio Entrada', 'field': 'entry_price'},
                            {'name': 'exit_price', 'label': 'Precio Salida', 'field': 'exit_price'},
                            {'name': 'pnl_str', 'label': 'PnL ($)', 'field': 'pnl_str', 'sortable': True},
                            {'name': 'pnl_pct_str', 'label': 'Retorno (%)', 'field': 'pnl_pct_str', 'sortable': True},
                            {'name': 'reason', 'label': 'Motivo Cierre', 'field': 'reason'},
                        ]

                        port_trades_table = ui.table(
                            columns=trades_columns,
                            rows=res.get('chronological_trades', []),
                            row_key='trade_id',
                            pagination={'rowsPerPage': 15}
                        ).classes('w-full bg-slate-900/50 shadow-lg rounded-xl mb-6')

                        port_trades_table.bind_filter(trade_search_input, 'value')

                        port_trades_table.add_slot('body-cell-pnl_str', '''
                            <q-td :props="props">
                                <span :class="props.row.pnl_raw >= 0 ? 'text-emerald-600 font-bold' : 'text-red-600 font-bold'">
                                    {{ props.row.pnl_str }}
                                </span>
                            </q-td>
                        ''')

                        port_trades_table.add_slot('body-cell-pnl_pct_str', '''
                            <q-td :props="props">
                                <span :class="props.row.pnl_pct_raw >= 0 ? 'text-emerald-600 font-bold' : 'text-red-600 font-bold'">
                                    {{ props.row.pnl_pct_str }}
                                </span>
                            </q-td>
                        ''')

                        port_trades_table.add_slot('body-cell-side', '''
                            <q-td :props="props">
                                <q-badge :color="props.row.side === 'LONG' ? 'blue-8' : 'purple-8'" :label="props.row.side" class="px-2 py-1 text-xs font-bold text-white shadow-lg" />
                            </q-td>
                        ''')

                        port_trades_table.add_slot('body-cell-reason', '''
                            <q-td :props="props">
                                <q-badge
                                    :color="props.value === 'TP' ? 'positive' : props.value === 'SL' ? 'negative' : 'primary'"
                                    class="px-2.5 py-1 text-xs font-bold text-white shadow-lg"
                                >
                                    {{ props.value === 'TP' ? 'TP' : props.value === 'SL' ? 'SL' : props.value === 'Signal' || props.value === 'Señal' ? 'Señal' : props.value }}
                                </q-badge>
                            </q-td>
                        ''')

                    ui.notify("✅ Simulación de portafolio combinada completada exitosamente", type="positive")

            except Exception as p_ex:
                import traceback
                print("PORTFOLIO SIM ERROR:\n", traceback.format_exc())
                with p_client:
                    ui.notify(f"Error en simulación de portafolio: {p_ex}", type="negative")
            finally:
                with p_client:
                    btn_run_portfolio.enable()
                    btn_run_portfolio.set_text("⚡ EJECUTAR SIMULACIÓN DE PORTAFOLIO COMBINADO")

        btn_run_portfolio.on_click(run_portfolio_simulation)

        def open_portfolio_modal():
            with ui.context.client:
                if state.get('start_date'):
                    port_start_input.value = state['start_date']
                if state.get('end_date'):
                    port_end_input.value = state['end_date']
                if state.get('commission_pct') is not None:
                    port_comm_input.value = float(state['commission_pct'])
                if state.get('slippage_pct') is not None:
                    port_slip_input.value = float(state['slippage_pct'])

                if portfolio_state['items']:
                    if state.get('strategy_name'):
                        portfolio_state['items'][0]['strategy'] = state['strategy_name']
                    if state.get('symbol'):
                        portfolio_state['items'][0]['symbol'] = state['symbol']
                    if state.get('timeframe'):
                        portfolio_state['items'][0]['timeframe'] = state['timeframe']
                    if state.get('custom_parameters'):
                        portfolio_state['items'][0]['custom_params'] = state['custom_parameters'].copy()

                refresh_portfolio_items_ui()
                portfolio_dialog.open()

        btn_portfolio.on_click(open_portfolio_modal)

    def select_strategy(filename):
        if filename in strategies:
            state['strategy_name'] = filename
            strat_combo.value = filename
            update_parameters_ui()

    def load_from_history(row):
        if row.get('strategy_name') and row['strategy_name'] in strategies:
            state['strategy_name'] = row['strategy_name']
            strat_combo.value = row['strategy_name']
        if row.get('symbol'):
            state['symbol'] = row['symbol']
            sym_combo.value = row['symbol']
        if row.get('timeframe'):
            state['timeframe'] = row['timeframe']
            tf_combo.value = row['timeframe']
        if row.get('start_date'):
            state['start_date'] = row['start_date']
            start_date_input.value = row['start_date']
        if row.get('end_date'):
            state['end_date'] = row['end_date']
            end_date_input.value = row['end_date']

        if row.get('config_snapshot'):
            try:
                cfg = json.loads(row['config_snapshot'])
                if 'custom_parameters' in cfg:
                    state['custom_parameters'] = cfg['custom_parameters']
                if 'sizing_mode' in cfg and cfg['sizing_mode']:
                    state['sizing_mode'] = cfg['sizing_mode']
                    sizing_combo.value = cfg['sizing_mode']
                if 'commission_pct' in cfg and cfg['commission_pct'] is not None:
                    state['commission_pct'] = float(cfg['commission_pct'])
                    comm_input.value = float(cfg['commission_pct'])
                if 'slippage_pct' in cfg and cfg['slippage_pct'] is not None:
                    state['slippage_pct'] = float(cfg['slippage_pct'])
                    slip_input.value = float(cfg['slippage_pct'])
            except Exception as parse_ex:
                print(f"Error parsing config snapshot: {parse_ex}")

        update_parameters_ui()
        ui.notify(f"✅ Parámetros cargados desde el historial para '{row.get('strategy_name')}'", type="positive")

    return {
        'select_strategy': select_strategy,
        'load_from_history': load_from_history,
        'open_portfolio_modal': open_portfolio_modal
    }
