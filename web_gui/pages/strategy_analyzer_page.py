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
from backtest_engine.equity_curve_backtester import EquityCurveBacktester
from backtest_engine.optimizer import run_grid_search, count_combinations
from data_layer.market_data import MarketDataManager, normalize_timeframe
from data_layer.storage import SessionLocal, OHLCV, BacktestRun
from backtest_engine.metrics import calculate_metrics, calculate_equity_curve_metrics
import yaml
from web_gui.components.tradingview_chart import build_tradingview_plotly_figure
from data_layer.export_utils import export_df_to_ninjatrader8, format_dt_display, format_date_display, parse_flexible_date


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

        dates_str = [format_date_display(d) for d in portfolio_equity.index]

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
                e_time_str = format_dt_display(e_time)

                x_time = tr.get('exit_time') or tr.get('Exit Timestamp') or "N/A"
                x_time_str = format_dt_display(x_time)

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
            'dates': [format_date_display(d) for d in portfolio_equity.index],
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


def render_strategy_analyzer(on_back_to_builder=None, on_go_to_live=None, on_go_to_portfolio=None):
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
                            'created_at': format_dt_display(run.created_at) if run.created_at else "N/A",
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
                            'start_date': format_date_display(run.start_date) if run.start_date else "01/01/24",
                            'end_date': format_date_display(run.end_date) if run.end_date else format_date_display(datetime.now())
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

            s_name = str(state.get('strategy_name', 'Estrategia')).replace('.yaml', '')
            sym = str(state.get('symbol', 'BTC/USDT'))
            tf = str(state.get('timeframe', '1d'))
            params_dict = state.get('custom_parameters', {})
            p_summary = ", ".join([f"{k}={v}" for k, v in list(params_dict.items())[:3]]) if params_dict else "Parametros_Defecto"
            
            start_val = metrics.get('start_dt') or state.get('start_date')
            end_val = metrics.get('end_dt') or state.get('end_date')
            s_dt = parse_flexible_date(start_val) if start_val else datetime(2020, 1, 1, tzinfo=timezone.utc)
            e_dt = parse_flexible_date(end_val) if end_val else datetime.now(timezone.utc)
            s_date = format_date_display(s_dt)
            e_date = format_date_display(e_dt)
            
            default_name = f"BACKTEST - {s_name} - {sym} - {tf} - [{p_summary}] - [{s_date} a {e_date}]"
            
            with ui.dialog() as dialog, ui.card().classes('bg-slate-900 border border-slate-700 p-5 rounded-2xl w-[640px] max-w-full shadow-2xl text-white'):
                with ui.row().classes('items-center justify-between w-full mb-1'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('save', color='emerald-400', size='24px')
                        ui.label('Guardar Análisis de Backtest').classes('text-base font-bold text-white')
                    ui.badge('TIPO: BACKTEST / ANÁLISIS', color='emerald-9').props('rounded').classes('text-xs font-bold font-mono px-2 py-0.5')
                
                ui.label('Registra la simulación con sus métricas completas, parámetros exactos y rango de fechas en el historial.').classes('text-xs text-slate-400 mb-2')
                
                with ui.row().classes('w-full gap-2 mb-3 bg-slate-950/70 p-2 rounded-lg border border-slate-800 text-[11px] text-slate-300 flex-wrap'):
                    ui.label(f"Estrategia: {s_name}").classes('font-bold text-emerald-300')
                    ui.label(f"| Activo: {sym} ({tf})").classes('text-slate-400')
                    ui.label(f"| Rango: {s_date} ➔ {e_date}").classes('text-slate-400')
                    if metrics:
                        cagr_val = metrics.get('cagr', 0.0)
                        sh_val = metrics.get('sharpe_ratio', 0.0)
                        ui.label(f"| CAGR: {cagr_val:+.1f}% | Sharpe: {sh_val:.2f}").classes('text-amber-400 font-mono')
                
                name_input = ui.input('Nombre / Identificador del Archivo', value=default_name).classes('w-full mb-4').props('outlined dense')
                
                with ui.row().classes('w-full justify-end gap-2'):
                    ui.button('Cancelar', on_click=dialog.close).props('flat text-color=grey')
                    
                    def do_save():
                        val_name = name_input.value.strip() or default_name
                        db = SessionLocal()
                        try:
                            run_id = str(uuid.uuid4())
                            config_payload = {
                                'file_type': 'BACKTEST_ANALYSIS',
                                'record_name': val_name,
                                'strategy_filename': state.get('strategy_name'),
                                'strategy_name': state.get('strategy_name'),
                                'symbol': sym,
                                'timeframe': tf,
                                'parameters': params_dict,
                                'custom_parameters': params_dict,
                                'date_range': f"{s_date} a {e_date}",
                                'start_date': s_dt.isoformat(),
                                'end_date': e_dt.isoformat(),
                                'sizing_mode': state.get('sizing_mode'),
                                'commission_pct': state.get('commission_pct'),
                                'slippage_pct': state.get('slippage_pct'),
                                'capital': state.get('capital'),
                                'capital_type': state.get('capital_type'),
                                'metrics': {
                                    'cagr': metrics.get('cagr', 0.0),
                                    'sharpe_ratio': metrics.get('sharpe_ratio', 0.0),
                                    'max_drawdown_pct': metrics.get('max_drawdown_pct', 0.0),
                                    'win_rate': metrics.get('win_rate', 0.0),
                                    'profit_factor': metrics.get('profit_factor', 0.0),
                                    'total_trades': metrics.get('total_trades', 0),
                                }
                            }
                            config_json = json.dumps(config_payload)

                            run_record = BacktestRun(
                                run_id=run_id,
                                strategy_name=val_name,
                                symbol=sym,
                                timeframe=tf,
                                start_date=s_dt,
                                end_date=e_dt,
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
                            ui.notify(f"💾 Backtest '{val_name}' guardado exitosamente", type="positive")
                            dialog.close()
                        except Exception as ex:
                            db.rollback()
                            ui.notify(f"Error al guardar backtest: {ex}", type="negative")
                        finally:
                            db.close()
                            
                    ui.button('Guardar', icon='save', on_click=do_save).classes('bg-emerald-600 hover:bg-emerald-500 font-bold text-white px-4 rounded-xl')
            dialog.open()

        # Header Compacto y Optimizado
        with ui.card().classes('w-full bg-slate-900 text-white rounded-xl shadow border border-slate-800 px-4 py-2.5 mb-3'):
            with ui.row().classes('items-center justify-between w-full'):
                with ui.row().classes('items-center gap-2.5'):
                    with ui.row().classes('items-center justify-center w-8 h-8 rounded-lg bg-blue-500/15 border border-blue-500/30 text-blue-400'):
                        ui.icon('analytics', size='1.25rem')
                    with ui.column().classes('gap-0'):
                        ui.label('Strategy Analyzer').classes('text-base font-bold tracking-tight text-white leading-tight')
                        ui.label('Análisis de estrategia e historial de pruebas retrospectivas').classes('text-slate-400 text-[11px] leading-tight')
                with ui.row().classes('items-center gap-2'):
                    ui.button('Ver Catálogo', on_click=load_catalog, icon='list').props('dense size=sm rounded').classes('bg-blue-600 hover:bg-blue-500 text-white font-bold px-3 py-1 text-xs shadow transition-all')
                    ui.button('Historial de Backtests', on_click=load_history, icon='history').props('dense size=sm rounded').classes('bg-emerald-600 hover:bg-emerald-500 text-white font-bold px-3 py-1 text-xs shadow transition-all')

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
            'timeframe': '1d',
            'start_date': '01/01/20',
            'end_date': format_date_display(datetime.now()),
            'capital': 1.0,
            'capital_type': 'BASE',
            'sizing_mode': 'Interés Compuesto (100% Capital)',
            'fixed_amount': 1.0,
            'commission_pct': 0.1,
            'slippage_pct': 0.05,
            'account_mode': 'spot_cash',
            'leverage': 1.0,
            
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
            'ec_enabled': False,
            'ec_start_dd': 30.0,
            'ec_stop_dd': 0.0,
            'cl_enabled': False,
            'cl_start': 3,
            'cl_stop': 0,
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
                start_dt = parse_flexible_date(state.get('start_date'), default=datetime(2020, 1, 1, tzinfo=timezone.utc))
                end_dt = parse_flexible_date(state.get('end_date'), default=datetime.now(timezone.utc), is_end_of_day=True)
                
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
                
                comm_val = state.get('commission_pct')
                comm_pct = float(comm_val) if comm_val is not None and str(comm_val).strip() != '' else 0.1
                slip_val = state.get('slippage_pct')
                slip_pct = float(slip_val) if slip_val is not None and str(slip_val).strip() != '' else 0.05
                sizing_mode = state.get('sizing_mode', 'Interés Compuesto (100% Capital)')
                
                # Monto fijo en divisa quote (USDT) si se seleccionó 'Monto Fijo'
                raw_fixed_val = float(state.get('fixed_amount', cap_val) or cap_val)
                if state.get('capital_type', 'QUOTE') == 'BASE':
                    fixed_quote_amt = raw_fixed_val * start_price
                else:
                    fixed_quote_amt = raw_fixed_val

                # Ejecutar descarga + simulación en hilo I/O secundario para NO congelar la UI
                acct_mode = state.get('account_mode', 'spot_cash')
                lev_val = float(state.get('leverage', 1.0) or 1.0)
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
                    slip_pct,
                    fixed_quote_amt,
                    acct_mode,
                    lev_val,
                    initial_cap_base,
                    bool(state.get('entry_on_next_open', False))
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
                            if 'strategy_indicators' in results and results['strategy_indicators']:
                                # Desactivar medias previas fijas
                                for k in list(chart_config.keys()):
                                    if k.startswith('sma_') or k.startswith('ema_'):
                                        chart_config[k] = False
                                # Activar dinámicamente las calculadas en la estrategia
                                for ind, p in results['strategy_indicators']:
                                    if ind in ['sma', 'ema']:
                                        key = f"{ind}_{p}"
                                        chart_config[key] = True
                                    elif ind == 'rsi':
                                        chart_config['rsi'] = True
                                        chart_config['rsi_period'] = p
                                    elif ind == 'atr':
                                        chart_config['atr'] = True
                                        chart_config['atr_period'] = p
                                
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
                                r_tot_slip = safe_num(results.get('real_total_slippage', 0))
                                r_slip_pct = safe_num(results.get('real_slippage_pct_cap', 0))
                                r_tot_comm = safe_num(results.get('real_total_commission', 0))
                                r_comm_pct = safe_num(results.get('real_commission_pct_cap', 0))
                                lbl_cfg_slip.set_text(f"{r_tot_slip:.2f} ({r_slip_pct:.2f}%)")
                                lbl_cfg_comm.set_text(f"{r_tot_comm:.2f} ({r_comm_pct:.2f}%)")
    
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
                                roll_max_q = equity_curve['equity'].cummax()
                                drawdown_q = ((equity_curve['equity'] - roll_max_q) / roll_max_q * 100).replace([np.inf, -np.inf], np.nan).fillna(0)
                                
                                # Downsample to prevent browser freeze and WebSocket disconnects on large datasets
                                MAX_POINTS = 1000
                                if len(equity_curve) > MAX_POINTS:
                                    step = len(equity_curve) // MAX_POINTS
                                    dates_eq = [x.strftime('%d/%m/%y %H:%M') for x in equity_curve.index[::step]]
                                    eq_vals = equity_curve['equity'].iloc[::step].replace([np.inf, -np.inf], np.nan).fillna(0)
                                    dd_q_sampled = drawdown_q.iloc[::step]
                                else:
                                    dates_eq = [x.strftime('%d/%m/%y %H:%M') for x in equity_curve.index]
                                    eq_vals = equity_curve['equity'].replace([np.inf, -np.inf], np.nan).fillna(0)
                                    dd_q_sampled = drawdown_q

                                close_aln = df['close'].reindex(eq_vals.index).ffill()
                                eq_base   = (eq_vals / close_aln).replace([np.inf, -np.inf], np.nan).fillna(0)
                                roll_max_b = eq_base.cummax()
                                dd_b_sampled = ((eq_base - roll_max_b) / roll_max_b * 100).replace([np.inf, -np.inf], np.nan).fillna(0)
    
                                state['equity_dates']        = dates_eq
                                state['equity_quote_data']   = eq_vals.round(6).tolist()
                                state['equity_base_data']    = eq_base.round(6).tolist()
                                state['drawdown_quote_data'] = dd_q_sampled.round(4).tolist()
                                state['drawdown_base_data']  = dd_b_sampled.round(4).tolist()
    
                                is_quote = (equity_toggle.value == 'QUOTE')
                                chart.options['xAxis']['data'] = dates_eq
                                chart.options['series'][0]['data'] = (
                                    state['equity_quote_data'] if is_quote else state['equity_base_data']
                                )
                                base_a  = state['symbol'].split('/')[0] if '/' in state['symbol'] else 'BASE'
                                quote_a = state['symbol'].split('/')[1] if '/' in state['symbol'] else 'QUOTE'
                                chart.options['series'][0]['name'] = f"Equity (Real {quote_a if is_quote else base_a})"
                                
                                drawdown_chart.options['xAxis']['data'] = dates_eq
                                drawdown_chart.options['series'][0]['data'] = state['drawdown_quote_data'] if is_quote else state['drawdown_base_data']
                                drawdown_chart.options['series'][0]['name'] = f"Drawdown ({quote_a if is_quote else base_a})"

                                # Procesar curva virtual si existe
                                v_equity_curve = results.get("virtual_equity_curve")
                                if v_equity_curve is not None and not v_equity_curve.empty:
                                    state['has_virtual'] = True
                                    if 'timestamp' in v_equity_curve.columns:
                                        v_equity_curve = v_equity_curve.set_index('timestamp')
                                    v_equity_curve.index = pd.to_datetime(v_equity_curve.index)
                                    v_roll_max = v_equity_curve['equity'].cummax()
                                    v_drawdown = ((v_equity_curve['equity'] - v_roll_max) / v_roll_max * 100).replace([np.inf, -np.inf], np.nan).fillna(0)
                                    
                                    if len(v_equity_curve) > MAX_POINTS:
                                        v_step = len(v_equity_curve) // MAX_POINTS
                                        v_eq_vals = v_equity_curve['equity'].iloc[::v_step].replace([np.inf, -np.inf], np.nan).fillna(0)
                                        v_dd_vals = v_drawdown.iloc[::v_step]
                                    else:
                                        v_eq_vals = v_equity_curve['equity'].replace([np.inf, -np.inf], np.nan).fillna(0)
                                        v_dd_vals = v_drawdown
                                        
                                    v_eq_base = (v_eq_vals / close_aln).replace([np.inf, -np.inf], np.nan).fillna(0)
                                    state['v_equity_quote_data'] = v_eq_vals.round(6).tolist()
                                    state['v_equity_base_data']  = v_eq_base.round(6).tolist()
                                    
                                    chart.options['series'][1]['data'] = state['v_equity_quote_data'] if is_quote else state['v_equity_base_data']
                                    chart.options['series'][1]['name'] = f"Equity (Virtual {quote_a if is_quote else base_a})"
                                    drawdown_chart.options['series'][1]['data'] = v_dd_vals.round(4).tolist()
                                else:
                                    state['has_virtual'] = False
                                    chart.options['series'][1]['data'] = []
                                    drawdown_chart.options['series'][1]['data'] = []

                                chart.update()
                                drawdown_chart.update()
    
                                price_chart_df = df
                                state['last_df'] = df
                                state['last_trades_df'] = trades_df
                                await render_tradingview_plotly(reset_zoom=True)

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
                            dates_empty = [x.strftime('%d/%m/%y %H:%M') for x in df_sampled.index]
                            
                            chart.options['xAxis']['data'] = dates_empty
                            chart.options['series'][0]['data'] = [round(initial_cap_quote, 6)] * len(df_sampled)
                            chart.update()
                            drawdown_chart.options['xAxis']['data'] = dates_empty
                            drawdown_chart.options['series'][0]['data'] = [0] * len(df_sampled)
                            drawdown_chart.update()
                            state['last_df'] = df
                            state['last_trades_df'] = None
                            await render_tradingview_plotly(reset_zoom=True)

                            ui.notify("El backtest no generó curva de equity (sin señales o error).", type='warning')
    
                        # ── Trades table ──
                        base_asset  = state['symbol'].split('/')[0] if '/' in state['symbol'] else 'BTC'
                        quote_asset = state['symbol'].split('/')[1] if '/' in state['symbol'] else 'USDT'
                        state['quote_asset'] = quote_asset
                        state['base_asset']  = base_asset
    
                        columns = [
                            {'name': 'id',           'label': 'ID',                          'field': 'id',           'sortable': True, 'align': 'left'},
                            {'name': 'entry_time',   'label': 'Entrada',                     'field': 'entry_time',   'sortable': True, 'align': 'left'},
                            {'name': 'exit_time',    'label': 'Salida',                      'field': 'exit_time',    'sortable': True, 'align': 'left'},
                            {'name': 'side',         'label': 'Lado',                        'field': 'side',         'sortable': True, 'align': 'center'},
                            {'name': 'exit_reason',  'label': 'Raz.',                        'field': 'exit_reason',  'sortable': True, 'align': 'center'},
                            {'name': 'entry_price',  'label': 'P.Ent.',                      'field': 'entry_price',  'sortable': True, 'align': 'right'},
                            {'name': 'exit_price',   'label': 'P.Sal.',                      'field': 'exit_price',   'sortable': True, 'align': 'right'},
                            {'name': 'pnl_pct',      'label': '%PnL',                        'field': 'pnl_pct',      'sortable': True, 'align': 'right'},
                            {'name': 'pnl_quote',    'label': f'PnL({quote_asset})',         'field': 'pnl_quote',    'sortable': True, 'align': 'right'},
                            {'name': 'pnl_base',     'label': f'PnL({base_asset})',          'field': 'pnl_base',     'sortable': True, 'align': 'right'},
                            {'name': 'cum_pnl_quote','label': f'Acum({quote_asset})',        'field': 'cum_pnl_quote','sortable': True, 'align': 'right'},
                            {'name': 'cum_pnl_base', 'label': f'Acum({base_asset})',         'field': 'cum_pnl_base', 'sortable': True, 'align': 'right'},
                            {'name': 'pnl_account_pct','label': '%Cta',                      'field': 'pnl_account_pct','sortable': True, 'align': 'right'},
                            {'name': 'balance_quote','label': f'Sdo({quote_asset})',         'field': 'balance_quote','sortable': True, 'align': 'right'},
                            {'name': 'balance_base', 'label': f'Sdo({base_asset})',          'field': 'balance_base', 'sortable': True, 'align': 'right'},
                            {'name': 'drawdown',     'label': 'DD',                          'field': 'drawdown',     'sortable': True, 'align': 'right'},
                            {'name': 'real_status',  'label': 'Fase Real',                   'field': 'real_status_marker', 'sortable': True, 'align': 'center'},
                        ]
                        trades_table.columns = columns
                        v_trades_table.columns = columns
    
                        def fmt_price(p):
                            if p is None: return 'N/A'
                            try:
                                p = float(p)
                                if abs(p) >= 1000: return f"{p:,.2f}"
                                if abs(p) >= 1:    return f"{p:.2f}"
                                s = f"{p:.5f}".rstrip('0').rstrip('.')
                                return s if s else "0"
                            except Exception:
                                return str(p)
    
                        def fmt_pnl(p):
                            if p is None: return 'N/A'
                            try:
                                p = float(p)
                                if abs(p) >= 1000: return f"{p:+,.2f}"
                                if abs(p) >= 1:    return f"{p:+.2f}"
                                s = f"{p:+.5f}".rstrip('0').rstrip('.')
                                return s if s not in ['+', '-'] else "0"
                            except Exception:
                                return str(p)
    
                        def fmt_dt(v):
                            if not v: return ''
                            try:
                                dt = pd.to_datetime(v)
                                return dt.strftime('%d/%m/%y %H:%M')
                            except:
                                s = str(v).replace('T', ' ')
                                return s[:16]
    
                        async def _process_trades_df(t_df, dd_series):
                            t_rows = []
                            c_q, c_b = 0.0, 0.0
                            b_q, b_b = initial_cap_quote, initial_cap_base
                            max_closed_b_q = initial_cap_quote

                            price_lookup = {}
                            if df is not None and not df.empty:
                                for ts_idx, r_ohlcv in df.iterrows():
                                    o_val = float(r_ohlcv.get('open', 0.0) or 0.0)
                                    h_val = float(r_ohlcv.get('high', 0.0) or 0.0)
                                    l_val = float(r_ohlcv.get('low', 0.0) or 0.0)
                                    c_val = float(r_ohlcv.get('close', 0.0) or 0.0)
                                    v_val = float(r_ohlcv.get('volume', 0.0) or 0.0)
                                    d_val = {
                                        'open': o_val,
                                        'high': h_val,
                                        'low': l_val,
                                        'close': c_val,
                                        'volume': v_val
                                    }
                                    price_lookup[ts_idx] = d_val
                                    try:
                                        ts_p = pd.to_datetime(ts_idx)
                                        price_lookup[ts_p] = d_val
                                        price_lookup[ts_p.strftime('%Y-%m-%d %H:%M:%S')] = d_val
                                        price_lookup[ts_p.strftime('%Y-%m-%d %H:%M')] = d_val
                                        price_lookup[ts_p.strftime('%Y-%m-%d')] = d_val
                                        price_lookup[format_dt_display(ts_p)] = d_val
                                        price_lookup[format_date_display(ts_p)] = d_val
                                    except:
                                        pass

                            def _get_ohlc(raw_t):
                                if raw_t is None: return {}
                                if raw_t in price_lookup: return price_lookup[raw_t]
                                try:
                                    p_ts = pd.to_datetime(raw_t)
                                    if p_ts in price_lookup: return price_lookup[p_ts]
                                    if hasattr(p_ts, 'tz_localize') and p_ts.tz is not None:
                                        p_ts_naive = p_ts.tz_localize(None)
                                        if p_ts_naive in price_lookup: return price_lookup[p_ts_naive]
                                except:
                                    pass
                                s = str(raw_t)[:19]
                                return price_lookup.get(s, {})

                            if t_df is not None and not t_df.empty:
                                import asyncio
                                t_df_clean = t_df.replace([np.inf, -np.inf], np.nan).fillna(0)
                                records = t_df_clean.to_dict('records')
                                for idx_counter, trow in enumerate(records):
                                    if idx_counter % 1000 == 0:
                                        await asyncio.sleep(0)
                                    v_pnl = trow.get('pnl', 0)
                                    pnl_quote   = float(v_pnl) if v_pnl is not None and v_pnl == v_pnl else 0.0
                                    v_ent = trow.get('entry_price', 1)
                                    entry_price = float(v_ent) if v_ent is not None and v_ent == v_ent else 1.0
                                    v_ex = trow.get('exit_price', 1)
                                    exit_price  = float(v_ex) if v_ex is not None and v_ex == v_ex else 1.0
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
                                        
                                    if pnl_pct != pnl_pct:
                                        pnl_pct = 0.0
        
                                    is_coin_m = (state.get('account_mode', 'spot_cash') == 'coin_margined_hold')
                                    if is_coin_m:
                                        pnl_base_val = float(trow.get('pnl_base', pnl_base))
                                        c_b += pnl_base_val
                                        b_b = initial_cap_base + c_b
                                        b_q = b_b * exit_price
                                        c_q = b_q - initial_cap_quote
                                    else:
                                        c_q += pnl_quote
                                        b_q = initial_cap_quote + c_q
                                        b_b = b_q / exit_price if exit_price > 0 else 0
                                        c_b = b_b - initial_cap_base
        
                                    if b_q > max_closed_b_q:
                                        max_closed_b_q = b_q
                                        
                                    dd_val = (b_q - max_closed_b_q) / max_closed_b_q * 100 if max_closed_b_q > 0 else 0.0
                                    
                                    acc_growth_pct = (b_q - initial_cap_quote) / initial_cap_quote * 100 if initial_cap_quote > 0 else 0.0

                                    e_ohlc = _get_ohlc(trow.get('entry_time'))
                                    x_ohlc = _get_ohlc(trow.get('exit_time'))
        
                                    t_rows.append({
                                        'id':           idx_counter + 1,
                                        'entry_time':   fmt_dt(trow.get('entry_time')),
                                        'exit_time':    fmt_dt(trow.get('exit_time')),
                                        'side':         side,
                                        'entry_price':  fmt_price(entry_price),
                                        'exit_price':   fmt_price(exit_price),
                                        'entry_open':   e_ohlc.get('open'),
                                        'entry_high':   e_ohlc.get('high'),
                                        'entry_low':    e_ohlc.get('low'),
                                        'entry_close':  e_ohlc.get('close'),
                                        'entry_volume': e_ohlc.get('volume', 0.0),
                                        'exit_open':    x_ohlc.get('open'),
                                        'exit_high':    x_ohlc.get('high'),
                                        'exit_low':     x_ohlc.get('low'),
                                        'exit_close':   x_ohlc.get('close'),
                                        'exit_volume':  x_ohlc.get('volume', 0.0),
                                        'pnl_pct':      float(pnl_pct),
                                        'pnl_quote':    fmt_pnl(pnl_quote),
                                        'pnl_base':     fmt_pnl(pnl_base),
                                        'cum_pnl_quote':fmt_pnl(c_q),
                                        'cum_pnl_base': fmt_pnl(c_b),
                                        'pnl_account_pct': f"{acc_growth_pct:+.2f}%",
                                        'balance_quote':fmt_price(b_q),
                                        'balance_base': fmt_price(b_b),
                                        'drawdown':     f"{dd_val:.2f}%",
                                        'exit_reason':  str(trow.get('exit_reason', '')),
                                        'real_status_marker': str(trow.get('real_status_marker', '')),
                                        'is_real_trade': bool(trow.get('is_real_trade', False))
                                    })
                            return t_rows, c_q, c_b, b_q, b_b
                            
                        trades_rows, cum_q, cum_b, balance_quote, balance_base = await _process_trades_df(trades_df, drawdown)
    
                        trades_table.rows = trades_rows
                        lbl_trades_info.set_text(f"Total: {len(trades_rows)} operaciones (Paginado)")
                        trades_table.update()
                        
                        def _upd_color(lbl, positive):
                            lbl.classes(remove='text-white text-gray-900 text-slate-900 text-slate-100 text-green-600 text-red-600 text-green-500 text-rose-500',
                                        add='text-green-500' if positive else 'text-rose-500')

                        v_trades_df = results.get("virtual_trades")
                        if state.get('has_virtual') and v_trades_df is not None:
                            try:
                                import asyncio
                                await asyncio.sleep(0)  # Yield to event loop before heavy virtual processing
                                
                                v_trades_rows, v_cum_q, v_cum_b, v_balance_quote, v_balance_base = await _process_trades_df(v_trades_df, v_drawdown)
                                v_trades_table.rows = v_trades_rows
                                v_lbl_trades_info.set_text(f"Total: {len(v_trades_rows)} operaciones (Paginado)")
                                v_trades_table.update()
                                
                                await asyncio.sleep(0)  # Yield again after table update
                                
                                v_trade_metrics = calculate_metrics(v_trades_df, initial_cap_quote)
                                v_eq_metrics = calculate_equity_curve_metrics(v_equity_curve['equity'])
                                
                                v_cagr_val      = safe_num(v_eq_metrics.get('cagr'))
                                v_maxdd_val     = safe_num(v_eq_metrics.get('max_drawdown_pct'))
                                v_sharpe_val    = safe_num(v_eq_metrics.get('sharpe_ratio'))
                                v_pf_val        = safe_num(v_trade_metrics.get('profit_factor'))
                                v_n_trades      = int(safe_num(v_trade_metrics.get('total_trades')))
                                v_win_trades    = int(safe_num(v_trade_metrics.get('winning_trades')))
                                v_lose_trades   = int(safe_num(v_trade_metrics.get('losing_trades')))
                                v_win_rate      = safe_num(v_trade_metrics.get('percent_profitable'))
                                v_max_cons_loss = int(safe_num(v_trade_metrics.get('max_consecutive_losers')))
                                
                                v_lbl_cagr.set_text(f"{v_cagr_val:.2f}%")
                                v_lbl_maxdd.set_text(f"{v_maxdd_val:.2f}%")
                                v_lbl_sharpe.set_text(f"{v_sharpe_val:.2f}")
                                v_lbl_profit_factor.set_text(f"{v_pf_val:.2f}")
                                v_lbl_total_trades.set_text(str(v_n_trades))
                                v_lbl_win_trades.set_text(str(v_win_trades))
                                v_lbl_lose_trades.set_text(str(v_lose_trades))
                                v_lbl_win_rate.set_text(f"{v_win_rate:.2f}%")
                                v_lbl_max_cons_losers.set_text(str(v_max_cons_loss))
                                v_tot_slip = safe_num(results.get('virtual_total_slippage', 0))
                                v_slip_pct = safe_num(results.get('virtual_slippage_pct_cap', 0))
                                v_tot_comm = safe_num(results.get('virtual_total_commission', 0))
                                v_comm_pct = safe_num(results.get('virtual_commission_pct_cap', 0))
                                v_lbl_cfg_slip.set_text(f"{v_tot_slip:.2f} ({v_slip_pct:.2f}%)")
                                v_lbl_cfg_comm.set_text(f"{v_tot_comm:.2f} ({v_comm_pct:.2f}%)")
                                
                                v_lbl_cagr.classes(remove='text-green-600 text-red-600', add='text-green-600' if v_cagr_val >= 0 else 'text-red-600')
                                v_lbl_maxdd.classes(remove='text-green-600 text-red-600', add='text-red-600')
                                v_lbl_sharpe.classes(remove='text-green-600 text-red-600 text-blue-600', add='text-blue-600' if v_sharpe_val >= 0 else 'text-red-600')
                                v_lbl_profit_factor.classes(remove='text-green-600 text-yellow-600 text-red-600', add='text-green-600' if v_pf_val >= 1.2 else 'text-yellow-600' if v_pf_val >= 1.0 else 'text-red-600')
                                v_lbl_win_rate.classes(remove='text-green-600 text-red-600', add='text-green-600' if v_win_rate > 50 else 'text-red-600')
                                
                                v_lbl_hdr_init_q.set_text(f'Capital Inicial ({quote_asset})')
                                v_lbl_hdr_init_b.set_text(f'Capital Inicial ({base_asset})')
                                v_lbl_hdr_bal_q.set_text(f'Balance Final ({quote_asset})')
                                v_lbl_hdr_bal_b.set_text(f'Balance Final ({base_asset})')
                                v_lbl_hdr_pnl_q.set_text(f'P&L Total ({quote_asset})')
                                v_lbl_hdr_pnl_b.set_text(f'P&L Total ({base_asset})')
                                
                                v_lbl_init_quote.set_text(fmt_price(initial_cap_quote))
                                v_lbl_init_base.set_text(fmt_price(initial_cap_base))
                                v_lbl_bal_quote.set_text(fmt_price(v_balance_quote))
                                v_lbl_bal_base.set_text(fmt_price(v_balance_base))
                                v_lbl_pnl_quote.set_text(fmt_pnl(v_cum_q))
                                v_lbl_pnl_base.set_text(fmt_pnl(v_cum_b))
                                
                                _upd_color(v_lbl_bal_quote, v_balance_quote >= initial_cap_quote)
                                _upd_color(v_lbl_bal_base,  v_balance_base  >= initial_cap_base)
                                _upd_color(v_lbl_pnl_quote, v_cum_q >= 0)
                                _upd_color(v_lbl_pnl_base,  v_cum_b >= 0)
                            except Exception as ex:
                                import traceback as _tbv
                                print(f"Error virtual metrics: {ex}\n{_tbv.format_exc()}", flush=True)
                        else:
                            v_trades_table.rows = []
                            v_trades_table.update()
                            
                            v_lbl_cagr.set_text("0.00%"); v_lbl_maxdd.set_text("0.00%")
                            v_lbl_sharpe.set_text("0.00"); v_lbl_profit_factor.set_text("0.00")
                            v_lbl_total_trades.set_text("0"); v_lbl_win_trades.set_text("0")
                            v_lbl_lose_trades.set_text("0"); v_lbl_win_rate.set_text("0.00%")
                            v_lbl_max_cons_losers.set_text("0")
    
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
                    """Devuelve (base, quote) dado 'BNB/BTC' o 'ETHBTC'."""
                    if '/' in symbol:
                        parts = symbol.split('/')
                        return parts[0].strip(), parts[1].strip()
                    
                    # Heurística para separar pares sin slash (ej. ETHBTC, BTCUSDT)
                    symbol_upper = symbol.upper()
                    quotes = ['USDT', 'USDC', 'BUSD', 'TUSD', 'FDUSD', 'BTC', 'ETH', 'BNB', 'USD', 'EUR']
                    for q in quotes:
                        if symbol_upper.endswith(q):
                            base = symbol[:-len(q)].strip()
                            if base:
                                return base, symbol[-len(q):].strip()
                    
                    # Fallback si no coincide con los quotes comunes
                    return symbol.strip(), 'USDT'

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
        def _auto_detect_range_for_symbol(sym=None):
            if not sym:
                sym = state.get('symbol', 'BTC/USDT')
            db = SessionLocal()
            try:
                min_ts = db.query(func.min(OHLCV.timestamp)).filter(OHLCV.symbol == sym).scalar()
                max_ts = db.query(func.max(OHLCV.timestamp)).filter(OHLCV.symbol == sym).scalar()
                if min_ts:
                    state['start_date'] = format_date_display(min_ts)
                    start_date.value = state['start_date']
                else:
                    state['start_date'] = '01/01/20'
                    start_date.value = '01/01/20'
                    
                if max_ts:
                    state['end_date'] = format_date_display(max_ts)
                    end_date.value = state['end_date']
            except Exception as ex:
                logger.warning(f"Error auto-detectando rango: {ex}")
            finally:
                db.close()

        with ui.row().classes('w-full gap-3 items-end mt-2'):
            with ui.column().classes('flex-1 gap-0'):
                with ui.row().classes('w-full justify-between items-center mb-1'):
                    ui.label('Fecha de inicio (DD/MM/AA)').classes('text-xs text-gray-500')
                    ui.button('Rango Máximo BD', on_click=lambda: _auto_detect_range_for_symbol()).props('dense flat size=xs color=cyan').tooltip('Detectar rango completo guardado en BD')
                start_date = ui.input('Fecha de inicio', value=state['start_date']).bind_value(state, 'start_date').classes('w-full')
            with ui.column().classes('flex-1 gap-0'):
                ui.label('Fecha de finalización (DD/MM/AA)').classes('text-xs text-gray-500 mb-1')
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
                def _get_active_asset():
                    sym = state.get('symbol', 'BTC/USDT')
                    b, q = _parse_assets(sym)
                    return b if state.get('capital_type', 'BASE') == 'BASE' else q

                def _on_asset_change(e):
                    val = getattr(e, 'value', None) or _asset_opts[1]
                    state['capital_asset'] = val
                    state['capital_type'] = 'QUOTE' if '(CITA)' in val else 'BASE'
                    _update_sizing_ui()

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
                    _update_sizing_ui()
                    try:
                        if 'equity_toggle' in locals() or 'equity_toggle' in globals():
                            equity_toggle.options = {
                                'QUOTE': f'💵 {q} (Dólares / Quote)',
                                'BASE': f'🪙 {b} (Satoshis / Base)'
                            }
                            equity_toggle.update()
                    except Exception:
                        pass

                sym_combo.on('update:model-value', _update_asset_combo)

        # ── Fila 3: Configuración de Fricción y Tamaño de Posición (Sizing / Comisiones / Slippage) ──
        if 'fixed_amount' not in state:
            state['fixed_amount'] = state.get('capital', 1.0)

        with ui.row().classes('w-full gap-3 items-end mt-2'):
            with ui.column().classes('flex-1 gap-0'):
                ui.label('Modo de Posición (Sizing)').classes('text-xs text-gray-500 mb-1')
                sizing_combo = ui.select(
                    ['Interés Compuesto (100% Capital)', 'Monto Fijo por Operación', 'Riesgo Fijo (1% por trade)'],
                    label='Modo de Tamaño',
                    value=state['sizing_mode'] if state.get('sizing_mode') in ['Interés Compuesto (100% Capital)', 'Monto Fijo por Operación', 'Riesgo Fijo (1% por trade)'] else 'Interés Compuesto (100% Capital)'
                ).bind_value(state, 'sizing_mode').classes('w-full')

            with ui.column().classes('w-44 gap-0') as col_fixed_amt:
                lbl_fixed_amt = ui.label(f"Monto por Trade ({_get_active_asset()})").classes('text-xs text-gray-500 mb-1')
                fixed_amt_input = ui.number('Monto Fijo', value=state['fixed_amount'], min=0.000001, step=0.1).bind_value(state, 'fixed_amount').classes('w-full')

            def _update_sizing_ui(e=None):
                cur_mode = str(state.get('sizing_mode', ''))
                is_fixed = 'Monto Fijo' in cur_mode
                col_fixed_amt.set_visibility(is_fixed)
                lbl_fixed_amt.set_text(f"Monto por Trade ({_get_active_asset()})")

            sizing_combo.on_value_change(_update_sizing_ui)
            _update_sizing_ui()

            with ui.column().classes('flex-1 gap-0'):
                ui.label('Comisión por Trade (%)').classes('text-xs text-gray-500 mb-1')
                comm_input = ui.number('Comisión (%)', value=state['commission_pct'], step=0.01, min=0.0).bind_value(state, 'commission_pct').classes('w-full')
            with ui.column().classes('flex-1 gap-0'):
                ui.label('Deslizamiento / Slippage (%)').classes('text-xs text-gray-500 mb-1')
                slip_input = ui.number('Slippage (%)', value=state['slippage_pct'], step=0.01, min=0.0).bind_value(state, 'slippage_pct').classes('w-full')

        # ── Fila 3.5: Modo de Cuenta (Spot Efectivo vs Hold Colateral Coin-M) ──
        if 'account_mode' not in state: state['account_mode'] = 'spot_cash'
        if 'leverage' not in state: state['leverage'] = 1.0

        with ui.row().classes('w-full gap-3 items-center mt-2 bg-slate-900/70 p-2.5 rounded-xl border border-slate-800 flex-wrap'):
            with ui.row().classes('items-center gap-2 min-w-[240px]'):
                ui.icon('account_balance_wallet', size='1.3rem').classes('text-amber-400')
                with ui.column().classes('gap-0'):
                    ui.label('Modo de Cuenta & Colateral').classes('text-xs font-bold text-slate-200')
                    ui.label('¿Salidas a USDT o 100% HOLD del activo Base?').classes('text-[10px] text-slate-400')
            
            with ui.column().classes('flex-1 min-w-[260px] gap-0'):
                acct_mode_select = ui.select(
                    {
                        'spot_cash': '💵 Spot Efectivo (Salidas a CITA / USDT)',
                        'coin_margined_hold': '🪙 Hold Colateral + Trading (Coin-M)'
                    },
                    label='Modo de Operativa',
                    value=state['account_mode']
                ).bind_value(state, 'account_mode').classes('w-full')

            with ui.column().classes('w-44 gap-0') as col_leverage:
                ui.label('Apalancamiento Señal (x)').classes('text-xs text-gray-400 mb-1')
                leverage_input = ui.number('Apalancamiento', value=state['leverage'], min=0.1, max=20.0, step=0.5).bind_value(state, 'leverage').classes('w-full')

            def _update_acct_mode_ui(e=None):
                is_coin = (state.get('account_mode') == 'coin_margined_hold')
                col_leverage.set_visibility(is_coin)

            acct_mode_select.on_value_change(_update_acct_mode_ui)
            _update_acct_mode_ui()

        # ── Fila 4: Filtro de Equity Curve (Backtest Virtual vs Real) ──
        if 'ec_enabled' not in state: state['ec_enabled'] = False
        if 'ec_start_dd' not in state: state['ec_start_dd'] = 30.0
        if 'ec_stop_dd' not in state: state['ec_stop_dd'] = 0.0

        with ui.row().classes('w-full gap-3 items-end mt-2 bg-slate-900/50 p-2 rounded border border-slate-800'):
            with ui.column().classes('flex-none gap-0 justify-center h-full'):
                ui.label('Filtro Drawdown').classes('text-xs text-orange-400 font-bold mb-1')
                ec_toggle = ui.toggle(
                    ['Inactivo', 'Activo'],
                    value='Activo' if state.get('ec_enabled', False) else 'Inactivo'
                ).props('toggle-color=amber-8 color=grey-9 text-color=grey-4 toggle-text-color=white size=sm no-caps').classes('mt-1')
                def _on_ec_toggle(e):
                    state['ec_enabled'] = (e.value == 'Activo')
                ec_toggle.on_value_change(_on_ec_toggle)
            with ui.column().classes('flex-1 gap-0'):
                ui.label('Iniciar Operaciones (Drawdown >= %)').classes('text-xs text-gray-400 mb-1')
                ec_start_input = ui.number('Start DD %', value=state['ec_start_dd'], step=1.0, min=0.0).bind_value(state, 'ec_start_dd').classes('w-full')
            with ui.column().classes('flex-1 gap-0'):
                ui.label('Detener Operaciones (Ganancia >= %)').classes('text-xs text-gray-400 mb-1')
                ec_stop_input = ui.number('Stop Gain %', value=state['ec_stop_dd'], step=1.0, min=0.0).bind_value(state, 'ec_stop_dd').classes('w-full')

        # ── Fila 5: Filtro de Perdedoras Consecutivas ──
        if 'cl_enabled' not in state: state['cl_enabled'] = False
        if 'cl_start' not in state: state['cl_start'] = 3
        if 'cl_stop' not in state: state['cl_stop'] = 0

        with ui.row().classes('w-full gap-3 items-end mt-2 bg-slate-900/50 p-2 rounded border border-slate-800'):
            with ui.column().classes('flex-none gap-0 justify-center h-full'):
                ui.label('Filtro Perdedoras Consec.').classes('text-xs text-orange-400 font-bold mb-1')
                cl_toggle = ui.toggle(
                    ['Inactivo', 'Activo'],
                    value='Activo' if state.get('cl_enabled', False) else 'Inactivo'
                ).props('toggle-color=amber-8 color=grey-9 text-color=grey-4 toggle-text-color=white size=sm no-caps').classes('mt-1')
                def _on_cl_toggle(e):
                    state['cl_enabled'] = (e.value == 'Activo')
                cl_toggle.on_value_change(_on_cl_toggle)
            with ui.column().classes('flex-1 gap-0'):
                ui.label('Iniciar Operaciones (Perdidas Consec. >=)').classes('text-xs text-gray-400 mb-1')
                cl_start_input = ui.number('Start L', value=state['cl_start'], step=1.0, min=0.0).bind_value(state, 'cl_start').classes('w-full')
            with ui.column().classes('flex-1 gap-0'):
                ui.label('Detener Operaciones (Ganancia >= %)').classes('text-xs text-gray-400 mb-1')
                cl_stop_input = ui.number('Stop Gain %', value=state['cl_stop'], step=1.0, min=0.0).bind_value(state, 'cl_stop').classes('w-full')


        # -- Tipos de Órdenes de Ejecución --
        if 'entry_order_type' not in state: state['entry_order_type'] = 'MARKET'
        if 'exit_order_type' not in state: state['exit_order_type'] = 'MARKET'
        if 'sl_order_type' not in state: state['sl_order_type'] = 'LIMIT'
        if 'tp_order_type' not in state: state['tp_order_type'] = 'LIMIT'

        with ui.row().classes('w-full gap-3 items-center mt-2 bg-slate-900/60 p-2.5 rounded-xl border border-purple-900/40 flex-wrap'):
            with ui.row().classes('items-center gap-2 min-w-[200px]'):
                ui.icon('tune', size='1.2rem').classes('text-purple-400')
                with ui.column().classes('gap-0'):
                    ui.label('Tipos de Órdenes').classes('text-xs font-bold text-slate-200')
                    ui.label('Predeterminado: Señales a Market / SL-TP a Limit').classes('text-[10px] text-slate-400')
            
            with ui.grid(columns=4).classes('flex-1 gap-2 min-w-[320px]'):
                ui.select({'MARKET': '⚡ Entrada: Market', 'LIMIT': '🎯 Entrada: Limit'}, label='Entrada', value=state['entry_order_type']).bind_value(state, 'entry_order_type').classes('w-full text-xs')
                ui.select({'MARKET': '⚡ Salida: Market', 'LIMIT': '🎯 Salida: Limit'}, label='Salida', value=state['exit_order_type']).bind_value(state, 'exit_order_type').classes('w-full text-xs')
                ui.select({'LIMIT': '🛡️ SL: Limit (Predet.)', 'MARKET': '⚡ SL: Market'}, label='Stop Loss', value=state['sl_order_type']).bind_value(state, 'sl_order_type').classes('w-full text-xs')
                ui.select({'LIMIT': '🎯 TP: Limit (Predet.)', 'MARKET': '⚡ TP: Market'}, label='Take Profit', value=state['tp_order_type']).bind_value(state, 'tp_order_type').classes('w-full text-xs')

        # -- Nota informativa SL/TP intrabarra --
        with ui.row().classes('w-full items-start gap-2 mt-2 bg-blue-950/30 border border-blue-900/40 rounded-lg p-2.5'):
            ui.icon('info_outline', size='1rem').classes('text-sky-400 mt-0.5 flex-shrink-0')
            ui.label(
                'Nota de confiabilidad: Cuando en la misma vela el Low toca el SL y el High el TP, el motor prioriza el SL '
                '(escenario conservador). Esta limitacion es inherente a datos OHLC y es identica a TradingView Pine Script. '
                'Los resultados reales deben ser iguales o mejores.'
            ).classes('text-[11px] text-slate-400 leading-relaxed')
        # ── Fila 3: Botones ──
        with ui.row().classes('w-full mt-4 gap-3 items-center'):
            btn_run = ui.button(
                'EJECUTAR PRUEBA RETROSPECTIVA',
                on_click=lambda e: asyncio.create_task(run_backtest(e))
            ).classes('bg-blue-700 hover:bg-blue-800 text-white font-bold flex-1 py-3')
            
            if on_go_to_live:
                ui.button(
                    '▶ CORRER EN VIVO (PAPER TRADING)',
                    on_click=lambda: on_go_to_live(state.get('strategy_name'))
                ).classes('bg-amber-600 hover:bg-amber-700 text-white font-bold py-3 px-5 shadow')
            
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
        
        lbl_real_title = ui.label('Resumen de resultados (ESTRATEGIA ORIGINAL)').classes('text-xl font-bold')
        lbl_real_title.bind_text_from(state, 'has_virtual', lambda h: 'Resumen de resultados (REAL CON FILTRO)' if h else 'Resumen de resultados (ESTRATEGIA ORIGINAL)')
        with ui.row().classes('w-full gap-4 mt-4 flex-wrap'):
            with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                ui.label('CAGR').classes('text-sm text-slate-400')
                lbl_cagr = ui.label('-- %').classes('text-2xl font-bold text-green-500')
            with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                ui.label('Reducción máxima').classes('text-sm text-slate-400')
                lbl_maxdd = ui.label('-- %').classes('text-2xl font-bold text-rose-500')
            with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                ui.label('Coeficiente de Sharpe').classes('text-sm text-slate-400')
                lbl_sharpe = ui.label('--').classes('text-2xl font-bold text-blue-400')
            with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                ui.label('Profit Factor').classes('text-sm text-slate-400')
                lbl_profit_factor = ui.label('--').classes('text-2xl font-bold text-indigo-400')
            with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                ui.label('Comercios totales').classes('text-sm text-slate-400')
                lbl_total_trades = ui.label('--').classes('text-2xl font-bold text-slate-100')
            with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                ui.label('Ganadoras').classes('text-sm text-slate-400')
                lbl_win_trades = ui.label('--').classes('text-2xl font-bold text-green-500')
            with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                ui.label('Perdedoras').classes('text-sm text-slate-400')
                lbl_lose_trades = ui.label('--').classes('text-2xl font-bold text-rose-500')
            with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                ui.label('% Ganadoras').classes('text-sm text-slate-400')
                lbl_win_rate = ui.label('-- %').classes('text-2xl font-bold text-slate-100')
            with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                ui.label('Perdedoras consec.').classes('text-sm text-slate-400')
                lbl_max_cons_losers = ui.label('--').classes('text-2xl font-bold text-amber-500')
            with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                lbl_hdr_init_q = ui.label('Capital Inicial (CITA)').classes('text-sm text-slate-400')
                lbl_init_quote = ui.label('--').classes('text-2xl font-bold text-blue-400')
            with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                lbl_hdr_init_b = ui.label('Capital Inicial (BASE)').classes('text-sm text-slate-400')
                lbl_init_base = ui.label('--').classes('text-2xl font-bold text-amber-400')
            with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                lbl_hdr_bal_q = ui.label('Saldo final (CITA)').classes('text-sm text-slate-400')
                lbl_bal_quote = ui.label('--').classes('text-2xl font-bold text-slate-100')
            with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                lbl_hdr_bal_b = ui.label('Saldo final (BASE)').classes('text-sm text-slate-400')
                lbl_bal_base = ui.label('--').classes('text-2xl font-bold text-slate-100')
            with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                lbl_hdr_pnl_q = ui.label('Total P&L (CITA)').classes('text-sm text-slate-400')
                lbl_pnl_quote = ui.label('--').classes('text-2xl font-bold text-slate-100')
            with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                lbl_hdr_pnl_b = ui.label('Total P&L (BASE)').classes('text-sm text-slate-400')
                lbl_pnl_base = ui.label('--').classes('text-2xl font-bold text-slate-100')
            with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                ui.label('Deslizamiento Acumulado').classes('text-sm text-slate-400')
                lbl_cfg_slip = ui.label('--').classes('text-xl font-bold text-slate-300')
            with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                ui.label('Comisión Acumulada').classes('text-sm text-slate-400')
                lbl_cfg_comm = ui.label('--').classes('text-xl font-bold text-slate-300')
                
        v_summary_container = ui.column().classes('w-full').bind_visibility_from(state, 'has_virtual')
        with v_summary_container:
            ui.label('Resumen de resultados (VIRTUAL SIN FILTRO)').classes('text-xl font-bold mt-8')
            with ui.row().classes('w-full gap-4 mt-4 flex-wrap'):
                with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                    ui.label('CAGR').classes('text-sm text-slate-400')
                    v_lbl_cagr = ui.label('-- %').classes('text-2xl font-bold text-green-500')
                with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                    ui.label('Reducción máxima').classes('text-sm text-slate-400')
                    v_lbl_maxdd = ui.label('-- %').classes('text-2xl font-bold text-rose-500')
                with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                    ui.label('Coeficiente de Sharpe').classes('text-sm text-slate-400')
                    v_lbl_sharpe = ui.label('--').classes('text-2xl font-bold text-blue-400')
                with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                    ui.label('Profit Factor').classes('text-sm text-slate-400')
                    v_lbl_profit_factor = ui.label('--').classes('text-2xl font-bold text-indigo-400')
                with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                    ui.label('Comercios totales').classes('text-sm text-slate-400')
                    v_lbl_total_trades = ui.label('--').classes('text-2xl font-bold text-slate-100')
                with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                    ui.label('Ganadoras').classes('text-sm text-slate-400')
                    v_lbl_win_trades = ui.label('--').classes('text-2xl font-bold text-green-500')
                with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                    ui.label('Perdedoras').classes('text-sm text-slate-400')
                    v_lbl_lose_trades = ui.label('--').classes('text-2xl font-bold text-rose-500')
                with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                    ui.label('% Ganadoras').classes('text-sm text-slate-400')
                    v_lbl_win_rate = ui.label('-- %').classes('text-2xl font-bold text-slate-100')
                with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                    ui.label('Perdedoras consec.').classes('text-sm text-slate-400')
                    v_lbl_max_cons_losers = ui.label('--').classes('text-2xl font-bold text-amber-500')
                with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                    v_lbl_hdr_init_q = ui.label('Capital Inicial (CITA)').classes('text-sm text-slate-400')
                    v_lbl_init_quote = ui.label('--').classes('text-2xl font-bold text-blue-400')
                with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                    v_lbl_hdr_init_b = ui.label('Capital Inicial (BASE)').classes('text-sm text-slate-400')
                    v_lbl_init_base = ui.label('--').classes('text-2xl font-bold text-amber-400')
                with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                    v_lbl_hdr_bal_q = ui.label('Saldo final (CITA)').classes('text-sm text-slate-400')
                    v_lbl_bal_quote = ui.label('--').classes('text-2xl font-bold text-slate-100')
                with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                    v_lbl_hdr_bal_b = ui.label('Saldo final (BASE)').classes('text-sm text-slate-400')
                    v_lbl_bal_base = ui.label('--').classes('text-2xl font-bold text-slate-100')
                with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                    v_lbl_hdr_pnl_q = ui.label('Total P&L (CITA)').classes('text-sm text-slate-400')
                    v_lbl_pnl_quote = ui.label('--').classes('text-2xl font-bold text-slate-100')
                with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                    v_lbl_hdr_pnl_b = ui.label('Total P&L (BASE)').classes('text-sm text-slate-400')
                    v_lbl_pnl_base = ui.label('--').classes('text-2xl font-bold text-slate-100')
                with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                    ui.label('Deslizamiento Acumulado').classes('text-sm text-slate-400')
                    v_lbl_cfg_slip = ui.label('--').classes('text-xl font-bold text-slate-300')
                with ui.card().classes('flex-1 items-center p-4 bg-slate-900/80 border border-slate-800 rounded-xl min-w-[150px] shadow'):
                    ui.label('Comisión Acumulada').classes('text-sm text-slate-400')
                    v_lbl_cfg_comm = ui.label('--').classes('text-xl font-bold text-slate-300')

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
            'trade_filter': 'ALL',
            'show_rangeslider': False
        }

        with ui.card().classes('w-full bg-slate-900 text-white p-4 rounded-2xl shadow-xl mt-6 border border-slate-800'):
            with ui.row().classes('w-full justify-between items-center mb-2'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('show_chart', size='1.8rem').classes('text-emerald-400')
                    ui.label('Gráfico de Análisis Técnico & Backtest (TradingView / NT8)').classes('text-xl font-extrabold tracking-tight')
                with ui.row().classes('items-center gap-2'):
                    ui.button(icon='fullscreen', on_click=lambda: ui.run_javascript('document.getElementById("plotly-chart-fullscreen-container").requestFullscreen()')).props('flat round dense text-white').tooltip('Ver gráfico en pantalla completa')
                    ui.badge('Plotly Pro Engine', color='slate-8').classes('px-3 py-1 text-xs')

            def _set_cfg(k, v):
                chart_config[k] = v
                asyncio.create_task(render_tradingview_plotly())

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

                    # Fila 2: Subgráficos Osciladores & Navegación
                    ui.label('Osciladores y Herramientas de Navegación').classes('text-xs font-bold text-slate-400 uppercase tracking-wider mt-1')
                    with ui.row().classes('w-full gap-4 flex-wrap items-center bg-slate-900/50 p-2 rounded-lg'):
                        _make_toggle('rsi', 'RSI (14)')
                        _make_toggle('macd', 'MACD (12, 26, 9)')
                        _make_toggle('atr', 'ATR (14)')
                        _make_toggle('show_rangeslider', '🔍 Barra de Navegación Inferior (Range Slider)')

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
            plotly_chart_container = ui.column().classes('w-full overflow-hidden rounded-xl border border-slate-700/60 bg-slate-950 flex-1').props('id="plotly-chart-fullscreen-container"')
            plotly_chart_elem = None

        async def render_tradingview_plotly(zoom_dates=None, reset_zoom=False):
            nonlocal plotly_chart_elem
            df = state.get('last_df', pd.DataFrame())
            trades_df = state.get('last_trades_df')
            symbol = state.get('symbol', 'BTC/USDT')
            timeframe = state.get('timeframe', '1d')

            uirevision_val = str(pd.Timestamp.now()) if reset_zoom else f"{symbol}_{timeframe}"

            fig = await run.io_bound(
                build_tradingview_plotly_figure,
                df,
                trades_df,
                chart_config,
                symbol,
                timeframe,
                uirevision_val
            )

            if zoom_dates and len(zoom_dates) == 2:
                fig.update_xaxes(range=zoom_dates)

            if plotly_chart_elem is None:
                plotly_chart_container.clear()
                with plotly_chart_container:
                    plotly_chart_elem = ui.plotly(fig).props('config="{scrollZoom: true, responsive: true, displayModeBar: true}"').classes('w-full h-[820px]')
            else:
                plotly_chart_elem.update_figure(fig)

        # Renderizado inicial no bloqueante
        import asyncio
        ui.timer(0.1, lambda: asyncio.create_task(render_tradingview_plotly()), once=True)


        _b_init, _q_init = _parse_assets(state.get('symbol', 'BTC/USDT'))
        
        # Panel destacado para seleccionar en qué moneda medir Equity y Drawdown
        with ui.card().classes('w-full bg-slate-900/90 border border-slate-800 p-4 rounded-xl mt-6 shadow-lg'):
            with ui.row().classes('w-full justify-between items-center flex-wrap gap-3'):
                with ui.row().classes('items-center gap-3'):
                    with ui.row().classes('w-10 h-10 rounded-xl bg-indigo-500/20 border border-indigo-500/40 items-center justify-center text-indigo-400'):
                        ui.icon('currency_exchange', size='1.5rem')
                    with ui.column().classes('gap-0'):
                        ui.label('Moneda de Medición de Gráficos (Equity & Drawdown)').classes('text-base font-bold text-white')
                        ui.label('Selecciona en qué divisa se calculan conjuntamente la curva de capital y el porcentaje de Drawdown').classes('text-xs text-slate-400')
                
                with ui.row().classes('items-center gap-2'):
                    ui.label('Medir ambos gráficos en:').classes('text-xs font-bold text-slate-300')
                    equity_toggle = ui.toggle(
                        {
                            'QUOTE': f'💵 {_q_init} (Dólares / Quote)',
                            'BASE': f'🪙 {_b_init} (Satoshis / Base)'
                        },
                        value='QUOTE'
                    ).props('color=indigo-7 text-color=grey-4 toggle-color=indigo-6 toggle-text-color=white no-caps size=sm rounded')

        with ui.row().classes('w-full items-center justify-between mt-4 mb-1'):
            lbl_equity_title = ui.label(f'📈 Curva de Equity (en {_q_init})').classes('text-base font-bold text-slate-200')

        chart = ui.echart({
            'tooltip': {'trigger': 'axis'},
            'grid': {'left': '1.5%', 'right': '2.5%', 'top': '40px', 'bottom': '30px', 'containLabel': True},
            'xAxis': {
                'type': 'category',
                'data': [],
                'axisLine': {'lineStyle': {'color': '#334155'}},
                'axisLabel': {'color': '#94a3b8', 'fontSize': 11}
            },
            'yAxis': {
                'type': 'value',
                'scale': True,
                'axisLine': {'lineStyle': {'color': '#334155'}},
                'splitLine': {'lineStyle': {'color': '#1e293b'}},
                'axisLabel': {'color': '#94a3b8', 'fontSize': 11}
            },
            'series': [{
                'name': 'Equity (Real)',
                'type': 'line',
                'smooth': True,
                'symbol': 'none',
                'lineStyle': {'color': '#38bdf8', 'width': 2.5},
                'areaStyle': {
                    'color': {
                        'type': 'linear', 'x': 0, 'y': 0, 'x2': 0, 'y2': 1,
                        'colorStops': [
                            {'offset': 0, 'color': 'rgba(56, 189, 248, 0.35)'},
                            {'offset': 1, 'color': 'rgba(56, 189, 248, 0.01)'}
                        ]
                    }
                },
                'data': []
            }, {
                'name': 'Equity (Virtual)',
                'type': 'line',
                'smooth': True,
                'symbol': 'none',
                'lineStyle': {'color': '#f59e0b', 'width': 2, 'type': 'dashed'},
                'data': []
            }],
        }).classes('w-full h-[420px] border border-slate-700/60 rounded-xl p-2 bg-slate-950 mt-2 shadow-xl')

        with ui.row().classes('w-full items-center justify-between mt-6 mb-1'):
            lbl_drawdown_title = ui.label(f'📉 Gráfico de Drawdown (en {_q_init})').classes('text-base font-bold text-slate-200')

        def _on_equity_toggle_change(e):
            if not state.get('equity_dates'): return
            is_quote = (equity_toggle.value == 'QUOTE')
            chart.options['series'][0]['data'] = state['equity_quote_data'] if is_quote else state['equity_base_data']
            
            base_a = state['symbol'].split('/')[0] if '/' in state['symbol'] else 'BASE'
            quote_a = state['symbol'].split('/')[1] if '/' in state['symbol'] else 'QUOTE'
            sel_asset = quote_a if is_quote else base_a
            
            chart.options['series'][0]['name'] = f"Equity (Real {sel_asset})"
            lbl_equity_title.set_text(f"📈 Curva de Equity (en {sel_asset})")
            
            if state.get('has_virtual'):
                chart.options['series'][1]['data'] = state['v_equity_quote_data'] if is_quote else state['v_equity_base_data']
                chart.options['series'][1]['name'] = f"Equity (Virtual {sel_asset})"
                
            chart.update()
            
            if state.get('drawdown_quote_data'):
                drawdown_chart.options['series'][0]['data'] = state['drawdown_quote_data'] if is_quote else state['drawdown_base_data']
                drawdown_chart.options['series'][0]['name'] = f"Drawdown ({sel_asset})"
                lbl_drawdown_title.set_text(f"📉 Gráfico de Drawdown (en {sel_asset}{' - Volatilidad en Dólares' if is_quote else ' - Pérdida de Satoshis'})")
                drawdown_chart.update()
            
        equity_toggle.on_value_change(_on_equity_toggle_change)
        drawdown_chart = ui.echart({
            'tooltip': {
                'trigger': 'axis',
                'formatter': '{b}<br/>Drawdown: {c}%'
            },
            'grid': {'left': '1.5%', 'right': '2.5%', 'top': '35px', 'bottom': '30px', 'containLabel': True},
            'xAxis': {
                'type': 'category',
                'data': [],
                'axisLine': {'lineStyle': {'color': '#334155'}},
                'axisLabel': {'color': '#94a3b8', 'fontSize': 11}
            },
            'yAxis': {
                'type': 'value',
                'scale': True,
                'axisLine': {'lineStyle': {'color': '#334155'}},
                'splitLine': {'lineStyle': {'color': '#1e293b'}},
                'axisLabel': {'formatter': '{value}%', 'color': '#94a3b8', 'fontSize': 11}
            },
            'series': [{
                'name': 'Drawdown (Real)',
                'type': 'line',
                'smooth': True,
                'symbol': 'none',
                'lineStyle': {'color': '#f43f5e', 'width': 2},
                'areaStyle': {
                    'color': {
                        'type': 'linear', 'x': 0, 'y': 0, 'x2': 0, 'y2': 1,
                        'colorStops': [
                            {'offset': 0, 'color': 'rgba(244, 63, 94, 0.4)'},
                            {'offset': 1, 'color': 'rgba(244, 63, 94, 0.05)'}
                        ]
                    }
                },
                'data': []
            }, {
                'name': 'Drawdown (Virtual)',
                'type': 'line',
                'smooth': True,
                'symbol': 'none',
                'lineStyle': {'color': '#f59e0b', 'width': 2, 'type': 'dashed'},
                'data': []
            }],
        }).classes('w-full h-[340px] border border-slate-700/60 rounded-xl p-2 bg-slate-950 mt-2 shadow-xl')

        with ui.row().classes('w-full items-center justify-between mt-6 mb-2 flex-wrap gap-2'):
            ui.label('Ejecuciones Reales (Con Filtro)').classes('text-lg font-bold text-blue-500')
            with ui.row().classes('gap-2 items-center flex-wrap'):
                def export_trades_csv(is_virtual=False):
                    target_table = v_trades_table if is_virtual else trades_table
                    table_name = "virtuales" if is_virtual else "reales"
                    if not target_table.rows:
                        ui.notify(f'No hay ejecuciones {table_name} registradas para exportar.', type='warning')
                        return
                    
                    quote_a = state.get('quote_asset', 'USDT')
                    base_a = state.get('base_asset', 'BTC')
                    rows_data = []
                    
                    for r in target_table.rows:
                        rows_data.append({
                            'ID': r.get('id'),
                            'Fecha_Entrada': r.get('entry_time'),
                            'Lado': r.get('side'),
                            'Precio_Ejecucion_Entrada': r.get('entry_price'),
                            'Open_Dia_Entrada': r.get('entry_open') if r.get('entry_open') is not None else '',
                            'High_Dia_Entrada': r.get('entry_high') if r.get('entry_high') is not None else '',
                            'Low_Dia_Entrada': r.get('entry_low') if r.get('entry_low') is not None else '',
                            'Close_Dia_Entrada': r.get('entry_close') if r.get('entry_close') is not None else '',
                            'Volumen_Entrada': r.get('entry_volume') if r.get('entry_volume') is not None else '',
                            'Fecha_Salida': r.get('exit_time'),
                            'Razon_Salida': r.get('exit_reason'),
                            'Precio_Ejecucion_Salida': r.get('exit_price'),
                            'Open_Dia_Salida': r.get('exit_open') if r.get('exit_open') is not None else '',
                            'High_Dia_Salida': r.get('exit_high') if r.get('exit_high') is not None else '',
                            'Low_Dia_Salida': r.get('exit_low') if r.get('exit_low') is not None else '',
                            'Close_Dia_Salida': r.get('exit_close') if r.get('exit_close') is not None else '',
                            'Volumen_Salida': r.get('exit_volume') if r.get('exit_volume') is not None else '',
                            'PnL_Pct': r.get('pnl_pct'),
                            f'PnL_{quote_a}': r.get('pnl_quote'),
                            f'PnL_{base_a}': r.get('pnl_base'),
                            f'Acumulado_{quote_a}': r.get('cum_pnl_quote'),
                            f'Acumulado_{base_a}': r.get('cum_pnl_base'),
                            'Crecimiento_Cuenta_Pct': r.get('pnl_account_pct'),
                            f'Saldo_{quote_a}': r.get('balance_quote'),
                            f'Saldo_{base_a}': r.get('balance_base'),
                            'Drawdown': r.get('drawdown'),
                            'Fase_Real': r.get('real_status_marker', '')
                        })
                        
                    df_exp = pd.DataFrame(rows_data)
                    csv_bytes = df_exp.to_csv(index=False).encode('utf-8')
                    sym_clean = state.get('symbol', 'BTCUSDT').replace('/', '')
                    strat_clean = state.get('strategy_name', 'Strategy').replace(' ', '_')
                    filename = f"trades_{table_name}_{strat_clean}_{sym_clean}.csv"
                    ui.download(csv_bytes, filename=filename)
                    ui.notify(f"¡Exportación exitosa con precios de auditoría! {len(target_table.rows)} operaciones en {filename}", type='positive')

                def export_trades_nt8(is_virtual=False):
                    target_table = v_trades_table if is_virtual else trades_table
                    table_name = "virtuales" if is_virtual else "reales"
                    if not target_table.rows:
                        ui.notify(f'No hay ejecuciones {table_name} registradas para exportar.', type='warning')
                        return
                    sym = state.get('symbol', 'BTCUSDT')
                    sym_clean = sym.replace('/', '').replace(':', '')
                    lines = ["Entry Time;Exit Time;Instrument;Market Position;Entry Price;Exit Price;Profit"]
                    for r in target_table.rows:
                        try:
                            entry_dt = pd.to_datetime(r.get('entry_time'), dayfirst=True).strftime('%Y%m%d %H%M%S')
                        except Exception:
                            entry_dt = str(r.get('entry_time', ''))
                        try:
                            exit_dt = pd.to_datetime(r.get('exit_time'), dayfirst=True).strftime('%Y%m%d %H%M%S')
                        except Exception:
                            exit_dt = str(r.get('exit_time', ''))
                        pos = "Long" if str(r.get('side', '')).upper() == 'LONG' else "Short"
                        entry_p = str(r.get('entry_price', '')).replace(',', '')
                        exit_p = str(r.get('exit_price', '')).replace(',', '')
                        pnl = str(r.get('pnl_quote', '')).replace('+', '').replace(',', '')
                        lines.append(f"{entry_dt};{exit_dt};{sym_clean};{pos};{entry_p};{exit_p};{pnl}")
                    
                    nt8_bytes = "\n".join(lines).encode('utf-8')
                    strat_clean = state.get('strategy_name', 'Strategy').replace(' ', '_')
                    filename = f"trades_nt8_{table_name}_{strat_clean}_{sym_clean}.txt"
                    ui.download(nt8_bytes, filename=filename)
                    ui.notify(f"¡Exportación NinjaTrader 8 exitosa! {len(target_table.rows)} trades en {filename}", type='positive')

                def export_chart_prices_csv():
                    df_chart = state.get('last_df')
                    if df_chart is None or df_chart.empty:
                        ui.notify('No hay datos de precios de la gráfica disponibles. Ejecuta un backtest primero.', type='warning')
                        return
                    df_exp = df_chart.copy()
                    if 'timestamp' not in df_exp.columns and isinstance(df_exp.index, pd.DatetimeIndex):
                        df_exp = df_exp.reset_index()
                    csv_bytes = df_exp.to_csv(index=False).encode('utf-8')
                    sym_clean = state.get('symbol', 'BTCUSDT').replace('/', '')
                    tf_clean = state.get('timeframe', '1h')
                    filename = f"precios_grafica_{sym_clean}_{tf_clean}.csv"
                    ui.download(csv_bytes, filename=filename)
                    ui.notify(f"¡Exportación de precios exitosa! {len(df_exp)} barras en {filename}", type='positive')

                def export_chart_prices_nt8():
                    df_chart = state.get('last_df')
                    if df_chart is None or df_chart.empty:
                        ui.notify('No hay datos de precios de la gráfica disponibles. Ejecuta un backtest primero.', type='warning')
                        return
                    tf_val = state.get('timeframe', '1h')
                    sym = state.get('symbol', 'BTCUSDT')
                    clean_sym = sym.replace('/', '').replace(':', '_').replace(' ', '_')
                    txt_content = export_df_to_ninjatrader8(
                        df=df_chart,
                        timeframe=tf_val,
                        timestamp_mode='end_of_bar',
                        delimiter=';',
                        date_format_mode='auto',
                        volume_as_int=True
                    )
                    filename = f"precios_nt8_{clean_sym}_{tf_val}.txt"
                    ui.download(bytes(txt_content, 'utf-8'), filename=filename)
                    ui.notify(f"¡Exportación de precios NinjaTrader 8 exitosa! {len(df_chart)} barras en {filename}", type='positive')

                ui.button('Exportar CSV', icon='file_download', on_click=lambda: export_trades_csv(is_virtual=False)).props('outline size=sm color=cyan').classes('hover:bg-cyan-900/40 text-cyan-400 font-semibold px-3').tooltip('Exportar tabla de operaciones ejecutadas a CSV con precios OHLCV del día')
                ui.button('Exportar NinjaTrader 8', icon='sim_card_download', on_click=lambda: export_trades_nt8(is_virtual=False)).props('outline size=sm color=emerald').classes('hover:bg-emerald-900/40 text-emerald-400 font-semibold px-3').tooltip('Exportar operaciones en formato de ejecuciones NinjaTrader 8')
                ui.button('Precios Gráfica (CSV)', icon='table_chart', on_click=export_chart_prices_csv).props('outline size=sm color=amber').classes('hover:bg-amber-900/40 text-amber-400 font-semibold px-3').tooltip('Exportar todas las velas y precios OHLCV de la gráfica a CSV')
                ui.button('Precios Gráfica (NT8)', icon='bar_chart', on_click=export_chart_prices_nt8).props('outline size=sm color=purple').classes('hover:bg-purple-900/40 text-purple-400 font-semibold px-3').tooltip('Exportar velas históricas de la gráfica en formato NinjaTrader 8 (.txt)')

            lbl_trades_info = ui.label('').classes('text-sm text-gray-400 font-mono italic mt-1')

        trades_columns = []
        with ui.card().classes('w-full p-2 bg-slate-900/50 rounded-xl shadow-lg border border-slate-700/50 mt-2'):
            ui.html('''
                <div id="trades-table-top-scroll" class="w-full overflow-x-auto overflow-y-hidden mb-1" style="height: 14px;">
                    <div id="trades-table-top-dummy" style="height: 1px; width: 2000px;"></div>
                </div>
            ''')
            with ui.column().classes('w-full overflow-x-auto') as trades_container:
                trades_table = ui.table(columns=trades_columns, rows=[], row_key='id', pagination=50).props('dense flat rows-per-page-label="Operaciones por página:" :rows-per-page-options="[25, 50, 100, 500, 0]"').classes('w-full text-xs')

        with ui.row().classes('w-full items-center justify-between mt-6 mb-2 flex-wrap gap-2').bind_visibility_from(state, 'has_virtual'):
            ui.label('Ejecuciones Virtuales (Sin Filtro)').classes('text-lg font-bold text-amber-500')
            with ui.row().classes('gap-2 items-center flex-wrap'):
                ui.button('Exportar CSV', icon='file_download', on_click=lambda: export_trades_csv(is_virtual=True)).props('outline size=sm color=amber').classes('hover:bg-amber-900/40 text-amber-400 font-semibold px-3').tooltip('Exportar tabla de operaciones virtuales a CSV con precios OHLCV del día')
                ui.button('Exportar NinjaTrader 8', icon='sim_card_download', on_click=lambda: export_trades_nt8(is_virtual=True)).props('outline size=sm color=emerald').classes('hover:bg-emerald-900/40 text-emerald-400 font-semibold px-3').tooltip('Exportar operaciones virtuales en formato NinjaTrader 8')
            v_lbl_trades_info = ui.label('').classes('text-sm text-gray-400 font-mono italic')
        
        with ui.card().classes('w-full p-2 bg-slate-900/50 rounded-xl shadow-lg border border-slate-700/50 mt-2').bind_visibility_from(state, 'has_virtual'):
            with ui.column().classes('w-full overflow-x-auto') as v_trades_container:
                v_trades_table = ui.table(columns=trades_columns, rows=[], row_key='id', pagination=50).props('dense flat rows-per-page-label="Operaciones por página:" :rows-per-page-options="[25, 50, 100, 500, 0]"').classes('w-full text-xs')

        ui.add_body_html('''
            <script>
            (function() {
                const setupTableScrollSync = () => {
                    const topScroll = document.getElementById('trades-table-top-scroll');
                    const dummy = document.getElementById('trades-table-top-dummy');
                    if (!topScroll || !dummy) return;
                    
                    const container = topScroll.nextElementSibling;
                    if (!container) return;
                    
                    const updateWidth = () => {
                        const table = container.querySelector('table');
                        if (table) {
                            dummy.style.width = table.offsetWidth + 'px';
                        }
                    };
                    
                    updateWidth();
                    window.addEventListener('resize', updateWidth);
                    
                    let isSyncingTop = false;
                    let isSyncingBottom = false;

                    topScroll.addEventListener('scroll', () => {
                        if (!isSyncingBottom) {
                            isSyncingTop = true;
                            container.scrollLeft = topScroll.scrollLeft;
                        }
                        isSyncingBottom = false;
                    });

                    container.addEventListener('scroll', () => {
                        if (!isSyncingTop) {
                            isSyncingBottom = true;
                            topScroll.scrollLeft = container.scrollLeft;
                        }
                        isSyncingTop = false;
                    });

                    const observer = new MutationObserver(updateWidth);
                    observer.observe(container, { childList: true, subtree: true });
                };
                setTimeout(setupTableScrollSync, 600);
            })();
            </script>
        ''')
        
        for t in [trades_table, v_trades_table]:
            t.add_slot('body', '''
                <q-tr :props="props" :class="props.row.is_real_trade ? 'bg-cyan-900/30' : ''">
                    <q-td v-for="col in props.cols" :key="col.name" :props="props">
                        
                        <template v-if="col.name === 'id'">
                            <span class="text-xs text-gray-600 font-mono font-bold">{{ col.value }}</span>
                        </template>
                        
                        <template v-else-if="col.name === 'real_status'">
                            <q-badge v-if="props.row.real_status_marker === 'START'" color="positive" label="INICIA" class="px-2 py-1 text-xs font-bold text-white shadow-md" />
                            <q-badge v-if="props.row.real_status_marker === 'STOP'" color="negative" label="TERMINA" class="px-2 py-1 text-xs font-bold text-white shadow-md" />
                        </template>
                        
                        <template v-else-if="col.name === 'entry_time' || col.name === 'exit_time'">
                            <span class="text-xs font-mono text-slate-300">{{ col.value }}</span>
                        </template>
                        
                        <template v-else-if="col.name === 'side'">
                            <q-badge :color="col.value === 'LONG' ? 'blue-8' : 'purple-8'"
                                     :label="col.value === 'LONG' ? 'L' : col.value === 'SHORT' ? 'S' : col.value"
                                     class="px-2 py-1 text-xs font-bold text-white shadow-lg" />
                        </template>
                        
                        <template v-else-if="col.name === 'pnl_pct'">
                            <span :style="{ color: col.value >= 0 ? '#16a34a' : '#dc2626', fontWeight: 'bold' }">
                                {{ col.value > 0 ? '+' : '' }}{{ col.value != null ? Number(col.value).toFixed(2) + '%' : '0.00%' }}
                            </span>
                        </template>
                        
                        <template v-else-if="col.name === 'exit_reason'">
                            <q-badge
                                :color="col.value === 'TP' ? 'positive' : col.value === 'SL' ? 'negative' : 'primary'"
                                class="px-2.5 py-1 text-xs font-bold text-white shadow-lg"
                            >
                                {{ col.value === 'TP' ? 'TP' : col.value === 'SL' ? 'SL' : col.value === 'Signal' || col.value === 'Señal' ? 'Señal' : col.value }}
                            </q-badge>
                        </template>
                        
                        <template v-else>
                            {{ col.value }}
                        </template>
                        
                    </q-td>
                </q-tr>
            ''')

        def _sync_load_and_run(strategy_path, custom_params, symbol, timeframe, start_dt, end_dt, initial_capital, sizing_mode, comm_pct, slip_pct, fixed_quote_amt=None, account_mode="spot_cash", leverage=1.0, initial_base_capital=None, entry_on_next_open=False):
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
                    
                if 'Riesgo Fijo' in str(sizing_mode):
                    strategy.config['risk_management']['position_sizing'] = {'method': 'fixed_fractional', 'risk_per_trade_pct': 1.0}
                elif 'Monto Fijo' in str(sizing_mode):
                    f_val = fixed_quote_amt if fixed_quote_amt is not None else initial_capital
                    strategy.config['risk_management']['position_sizing'] = {'method': 'fixed_amount', 'value': f_val}
                else:
                    strategy.config['risk_management']['position_sizing'] = {'method': 'compounding', 'value': 100.0}
                    
                strategy.risk_manager = RiskManager(strategy.config['risk_management'])
                
                # Integrar el Filtro Equity Curve configurado en la UI o en los Parámetros (para el Optimizador)
                ec_config = strategy.config.get('equity_curve_management', {})
                
                # Drawdown Filter
                dd_enabled = bool(state.get('ec_enabled', False))
                # Consecutive Losers Filter
                cl_enabled = bool(state.get('cl_enabled', False))
                
                # Buscar en custom parameters primero (útil para el optimizador)
                params = strategy.parameters
                start_dd = params.get('DRAWDON INICIAL', params.get('start_dd', state.get('ec_start_dd', 30.0)))
                stop_dd = params.get('DRAWDON CIERRE', params.get('stop_dd', state.get('ec_stop_dd', 0.0)))
                
                ec_config['enabled'] = dd_enabled or cl_enabled
                ec_config['dd_enabled'] = dd_enabled
                ec_config['start_trading_at_dd_pct'] = float(start_dd) if start_dd is not None else 30.0
                ec_config['stop_trading_at_dd_pct'] = float(stop_dd) if stop_dd is not None else 0.0
                
                ec_config['cl_enabled'] = cl_enabled
                ec_config['cl_start'] = int(state.get('cl_start', 3))
                ec_config['cl_stop'] = float(state.get('cl_stop', 0))
                
                strategy.config['equity_curve_management'] = ec_config
                
                if ec_config.get('enabled', False):
                    backtester = EquityCurveBacktester(strategy, initial_capital=initial_capital, commission_pct=comm_pct, slippage_pct=slip_pct)
                else:
                    backtester = Backtester(
                        strategy,
                        initial_capital=initial_capital,
                        commission_pct=comm_pct,
                        slippage_pct=slip_pct,
                        account_mode=account_mode,
                        leverage=leverage,
                        initial_base_capital=initial_base_capital,
                        entry_on_next_open=entry_on_next_open
                    )
                    
                results = backtester.run(df)
                results['df'] = df
                
                # Extraer indicadores de la estrategia para graficarlos dinámicamente
                strategy_indicators = []
                params_dict = getattr(strategy, 'parameters', {}) or strategy.config.get('parameters', {})
                for rule_type in ['entry_conditions', 'exit_conditions']:
                    rules = strategy.config.get(rule_type, {}).get('rules', [])
                    for rule in rules:
                        if rule.get('type') == 'technical_indicator':
                            for prefix in ['1', '2', '_1', '_2']:
                                ind_data = rule.get(f'indicator{prefix}')
                                if isinstance(ind_data, dict):
                                    ind = ind_data.get('name')
                                    per = ind_data.get('period')
                                else:
                                    ind = ind_data
                                    per = rule.get(f'period{prefix}')
                                
                                if ind and str(ind).upper() not in ['PRICE', 'VOLUME', 'CLOSE', 'OPEN', 'HIGH', 'LOW'] and per is not None:
                                    try:
                                        period_val = params_dict.get(per, per)
                                        strategy_indicators.append((str(ind).lower(), int(float(period_val))))
                                    except (ValueError, TypeError):
                                        pass
                results['strategy_indicators'] = list(set(strategy_indicators))
                
                return results
            finally:
                db.close()

    # ════════════════════════════════════════════════════════
    # DIALOG DEL SIMULADOR DE PORTAFOLIO Y COMBINACIÓN
    # ════════════════════════════════════════════════════════
    with ui.dialog().props('maximized') as portfolio_dialog, \
         ui.card().classes('w-full h-full q-pa-md overflow-auto bg-[#0a0e17] text-slate-100 border border-slate-800'):

        with ui.row().classes('w-full justify-between items-center mb-4 bg-slate-900/90 text-white p-4 rounded-2xl border border-slate-800 shadow-xl'):
            with ui.row().classes('items-center gap-3'):
                with ui.row().classes('items-center justify-center w-12 h-12 rounded-xl bg-emerald-500/15 border border-emerald-500/30 text-emerald-400'):
                    ui.icon('pie_chart', size='2rem')
                with ui.column().classes('gap-0'):
                    ui.label('Simulador de Portafolio & Combinación de Estrategias').classes('text-2xl font-black text-white tracking-tight')
                    ui.label('Asigna pesos (%) a múltiples estrategias con sus parámetros individuales para simular una curva de capital conjunta.').classes('text-xs text-slate-400')
            ui.button(icon='close', on_click=portfolio_dialog.close).props('flat round text-color=white size=md').classes('hover:bg-slate-800')

        # Global inputs for Portfolio
        with ui.card().classes('w-full p-5 mb-4 rounded-2xl border border-slate-800 bg-slate-900/80 shadow-xl backdrop-blur'):
            with ui.row().classes('items-center gap-2 mb-3'):
                ui.icon('tune', size='1.2rem').classes('text-amber-400')
                ui.label('Configuración Global del Portafolio').classes('font-bold text-sm text-slate-200 uppercase tracking-wider')

            with ui.row().classes('w-full gap-4 items-center flex-wrap'):
                port_capital_input = ui.number('Capital Total Inicial ($)', value=10000.0, step=500.0, min=10.0).classes('flex-1 min-w-[200px]')
                port_start_input = ui.input('Fecha Inicio (DD/MM/AA)', value='01/01/20').classes('flex-1 min-w-[170px]')
                port_end_input = ui.input('Fecha Fin (DD/MM/AA)', value=format_date_display(datetime.now())).classes('flex-1 min-w-[170px]')
                port_comm_input = ui.number('Comisión (%)', value=0.1, step=0.01).classes('w-32')
                port_slip_input = ui.number('Slippage (%)', value=0.05, step=0.01).classes('w-32')

        # Table/List of Portfolio Items
        portfolio_state = {
            'items': [
                {'strategy': list(strategies.keys())[0] if strategies else '', 'symbol': available_symbols[0], 'timeframe': '1d', 'weight_pct': 50.0},
                {'strategy': list(strategies.keys())[0] if strategies else '', 'symbol': available_symbols[min(1, len(available_symbols)-1)], 'timeframe': '1d', 'weight_pct': 50.0}
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
                    cfg = yaml.safe_load(f) or {}
                raw_params = cfg.get('parameters', {}) or {}
                params = {}
                for k, v in raw_params.items():
                    try:
                        params[k] = float(v) if '.' in str(v) else int(v)
                    except (ValueError, TypeError):
                        params[k] = v
                return params
            except Exception:
                return {}

        def fetch_saved_runs():
            db = SessionLocal()
            try:
                runs = db.query(BacktestRun).order_by(BacktestRun.created_at.desc()).all()
                opts = {}
                runs_map = {}
                for r in runs:
                    dt_s = format_dt_display(r.created_at) if r.created_at else ''
                    cagr_s = f"CAGR: {r.cagr:.1f}%" if r.cagr is not None else ""
                    lbl = f"[#{r.run_id[:8]}] {r.strategy_name} ({r.symbol} {r.timeframe}) {cagr_s} | {dt_s}"
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

                    with ui.card().classes('w-full p-5 rounded-2xl border border-slate-800 bg-slate-900/90 shadow-xl mb-3'):
                        # 1. Selector de Backtest Guardado (Opcional)
                        if saved_opts:
                            with ui.row().classes('w-full mb-3 items-center gap-3 bg-slate-800/80 p-3 rounded-xl border border-slate-700/60'):
                                ui.icon('cloud_download', size='1.3rem').classes('text-blue-400')
                                with ui.column().classes('gap-0 flex-1'):
                                    ui.label('Cargar desde Backtest Guardado').classes('text-xs text-blue-300 font-bold')
                                    saved_sel = ui.select(
                                        saved_opts,
                                        label='Seleccionar corrida guardada',
                                        value=item.get('saved_run_id')
                                    ).classes('w-full text-xs')

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
                                            ui.notify(f"✅ Estrategia #{idx+1} cargada desde Historial (#{run_id[:8]})", type="positive")
                                    return on_saved_change

                                saved_sel.on_value_change(make_saved_load_handler(i, saved_map))

                        # 2. Configuración Principal de la Estrategia
                        with ui.row().classes('w-full gap-4 items-center mb-3 flex-wrap'):
                            with ui.row().classes('items-center gap-2 min-w-[130px]'):
                                ui.icon('memory', size='1.2rem').classes('text-emerald-400')
                                ui.label(f"Estrategia #{i+1}").classes('font-extrabold text-white text-base')

                            strat_sel = ui.select(list(strategies.keys()), label='Estrategia Base', value=item['strategy']).classes('flex-1 min-w-[220px]')
                            sym_sel = ui.select(available_symbols, label='Símbolo', value=item['symbol']).classes('w-44')
                            tf_sel = ui.select(['1m', '5m', '15m', '1h', '4h', '1d'], label='TF', value=item['timeframe']).classes('w-28')
                            weight_num = ui.number('Peso (%)', value=item['weight_pct'], min=0.0, max=100.0, step=5.0).classes('w-28')

                            def remove_item(idx_to_remove=i):
                                if len(portfolio_state['items']) <= 1:
                                    ui.notify("Debe existir al menos 1 estrategia en el portafolio", type="warning")
                                    return
                                portfolio_state['items'].pop(idx_to_remove)
                                refresh_portfolio_items_ui()

                            ui.button(icon='delete', on_click=lambda idx=i: remove_item(idx)).props('flat round color=negative size=md').tooltip('Eliminar esta estrategia del portafolio')

                        # 3. Resumen visual de parámetros actuales
                        cur_params = item.get('custom_params', {})
                        params_summary_str = ' | '.join(f"{k}: {v}" for k, v in cur_params.items()) if cur_params else "Parámetros predeterminados"

                        with ui.row().classes('w-full items-center justify-between mb-2 bg-slate-950/60 p-2.5 rounded-xl border border-slate-800 text-xs'):
                            with ui.row().classes('items-center gap-2 flex-1 overflow-hidden'):
                                ui.icon('tune', size='1rem').classes('text-purple-400')
                                ui.label('Parámetros configurados:').classes('text-slate-400 font-semibold flex-shrink-0')
                                ui.label(params_summary_str).classes('text-purple-300 font-mono truncate')

                            def reset_default_params(idx=i, s_name=item['strategy']):
                                portfolio_state['items'][idx]['custom_params'] = get_strategy_default_params(s_name).copy()
                                refresh_portfolio_items_ui()
                                ui.notify(f"Parámetros de Estrategia #{idx+1} restablecidos", type="info")

                            ui.button('🔄 Reset', on_click=lambda idx=i, sn=item['strategy']: reset_default_params(idx, sn)).props('flat dense size=sm color=grey-5').tooltip('Restablecer a valores por defecto del archivo YAML')

                        # 4. Panel desplegable para editar CADA parámetro individualmente
                        with ui.expansion('⚙️ Configurar / Editar Parámetros de esta Estrategia', icon='edit').classes('w-full bg-slate-800/40 border border-slate-700/50 rounded-xl p-2 text-slate-200'):
                            if not cur_params:
                                ui.label('No hay parámetros configurables para esta estrategia.').classes('text-xs text-slate-400 italic p-2')
                            else:
                                with ui.row().classes('w-full gap-4 flex-wrap items-center p-2'):
                                    for p_key, p_val in cur_params.items():
                                        with ui.column().classes('gap-0 min-w-[140px]'):
                                            ui.label(p_key).classes('text-xs font-bold text-slate-300 mb-1')
                                            if isinstance(p_val, (int, float)):
                                                p_num = ui.number(value=p_val, format='%.4g', step=1.0).classes('w-full')
                                                def make_change_handler(idx=i, pk=p_key):
                                                    def on_p_change(e):
                                                        try:
                                                            if e.value is not None:
                                                                portfolio_state['items'][idx]['custom_params'][pk] = float(e.value)
                                                        except Exception:
                                                            pass
                                                    return on_p_change
                                                p_num.on_value_change(make_change_handler(i, p_key))
                                            else:
                                                p_inp = ui.input(value=str(p_val)).classes('w-full')
                                                def make_change_str_handler(idx=i, pk=p_key):
                                                    def on_p_str_change(e):
                                                        portfolio_state['items'][idx]['custom_params'][pk] = e.value
                                                    return on_p_str_change
                                                p_inp.on_value_change(make_change_str_handler(i, p_key))

                        def on_strat_change(e, idx=i):
                            new_s = e.value
                            if new_s and new_s != portfolio_state['items'][idx]['strategy']:
                                portfolio_state['items'][idx]['strategy'] = new_s
                                portfolio_state['items'][idx]['saved_run_id'] = None
                                portfolio_state['items'][idx]['custom_params'] = get_strategy_default_params(new_s).copy()
                                refresh_portfolio_items_ui()

                        strat_sel.on_value_change(lambda e, idx=i: on_strat_change(e, idx))
                        sym_sel.on_value_change(lambda e, idx=i: portfolio_state['items'][idx].update({'symbol': e.value}))
                        tf_sel.on_value_change(lambda e, idx=i: portfolio_state['items'][idx].update({'timeframe': e.value}))
                        weight_num.on_value_change(lambda e, idx=i: portfolio_state['items'][idx].update({'weight_pct': float(e.value or 0.0)}))

        refresh_portfolio_items_ui()

        with ui.row().classes('w-full gap-3 mb-6 items-center flex-wrap'):
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

            ui.button('➕ Agregar Otra Estrategia al Portafolio', on_click=add_portfolio_item).classes('bg-blue-600 hover:bg-blue-700 text-white font-bold py-2.5 px-5 rounded-xl shadow')
            ui.button('⚖️ Distribuir Capital Equitativamente (100%)', on_click=rebalance_equal_weights).classes('bg-slate-800 hover:bg-slate-700 text-slate-200 font-bold py-2.5 px-5 rounded-xl border border-slate-700 shadow')

        # Action Button
        btn_run_portfolio = ui.button('⚡ EJECUTAR SIMULACIÓN DE PORTAFOLIO COMBINADO').classes('w-full bg-emerald-600 hover:bg-emerald-500 text-white font-extrabold py-4 text-lg rounded-2xl shadow-2xl transition-all')

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
                p_start = parse_flexible_date(port_start_input.value, default=datetime(2020, 1, 1, tzinfo=timezone.utc))
                p_end = parse_flexible_date(port_end_input.value, default=datetime.now(timezone.utc), is_end_of_day=True)
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
                            with ui.card().classes('flex-1 min-w-[150px] bg-slate-900/80 p-4 rounded-xl border border-slate-800 shadow-lg'):
                                ui.label('CAGR del Portafolio').classes('text-xs text-slate-400 uppercase font-bold')
                                ui.label(f"{res['cagr']:.2f}%").classes('text-2xl font-black text-emerald-400')

                            with ui.card().classes('flex-1 min-w-[150px] bg-slate-900/80 p-4 rounded-xl border border-slate-800 shadow-lg'):
                                ui.label('Max Drawdown Combinado').classes('text-xs text-slate-400 uppercase font-bold')
                                ui.label(f"{res['max_drawdown_pct']:.2f}%").classes('text-2xl font-black text-rose-400')

                            with ui.card().classes('flex-1 min-w-[150px] bg-slate-900/80 p-4 rounded-xl border border-slate-800 shadow-lg'):
                                ui.label('Profit Factor (PF)').classes('text-xs text-slate-400 uppercase font-bold')
                                ui.label(f"{res['profit_factor']:.2f}").classes('text-2xl font-black text-purple-400')

                            with ui.card().classes('flex-1 min-w-[150px] bg-slate-900/80 p-4 rounded-xl border border-slate-800 shadow-lg'):
                                ui.label('Trades Totales').classes('text-xs text-slate-400 uppercase font-bold')
                                ui.label(f"{res['total_trades']}").classes('text-2xl font-black text-sky-400')

                            with ui.card().classes('flex-1 min-w-[150px] bg-slate-900/80 p-4 rounded-xl border border-slate-800 shadow-lg'):
                                ui.label('Ganadoras / Perdedoras').classes('text-xs text-slate-400 uppercase font-bold')
                                with ui.row().classes('items-center gap-2 mt-1'):
                                    ui.label(f"🟢 {res['winning_trades']}").classes('text-lg font-bold text-emerald-400')
                                    ui.label(f"/ 🔴 {res['losing_trades']}").classes('text-lg font-bold text-rose-400')
                                    ui.label(f"({res['win_rate']:.1f}%)").classes('text-xs font-semibold text-slate-400')

                            with ui.card().classes('flex-1 min-w-[150px] bg-slate-900/80 p-4 rounded-xl border border-slate-800 shadow-lg'):
                                ui.label('Capital Final Portafolio').classes('text-xs text-slate-400 uppercase font-bold')
                                ui.label(f"${res['final_equity']:,.2f}").classes('text-2xl font-black text-white')

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
            if state.get('start_date'):
                port_start_input.value = state['start_date']
            if state.get('end_date'):
                port_end_input.value = state['end_date']
            if state.get('commission_pct') is not None:
                port_comm_input.value = float(state['commission_pct'])
            if state.get('slippage_pct') is not None:
                port_slip_input.value = float(state['slippage_pct'])

            if portfolio_state['items']:
                current_strat = state.get('strategy_name')
                if current_strat:
                    portfolio_state['items'][0]['strategy'] = current_strat
                    strat_defaults = get_strategy_default_params(current_strat)
                    main_params = state.get('custom_parameters') or {}
                    if main_params and all(k in strat_defaults for k in main_params.keys()):
                        merged = strat_defaults.copy()
                        merged.update(main_params)
                        portfolio_state['items'][0]['custom_params'] = merged
                    else:
                        portfolio_state['items'][0]['custom_params'] = strat_defaults.copy()

                if state.get('symbol'):
                    portfolio_state['items'][0]['symbol'] = state['symbol']
                if state.get('timeframe'):
                    portfolio_state['items'][0]['timeframe'] = state['timeframe']

            refresh_portfolio_items_ui()
            portfolio_dialog.open()

        btn_portfolio.on_click(on_go_to_portfolio if on_go_to_portfolio else open_portfolio_modal)

    def select_strategy(filename, symbol=None, timeframe=None, custom_params=None):
        if filename in strategies:
            state['strategy_name'] = filename
            strat_combo.value = filename
            if symbol:
                state['symbol'] = symbol
                sym_combo.value = symbol
            if timeframe:
                state['timeframe'] = timeframe
                tf_combo.value = timeframe
            if custom_params:
                state['custom_parameters'] = dict(custom_params)
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
