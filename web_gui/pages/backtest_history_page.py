import json
import os
from datetime import datetime
from nicegui import run, ui
from data_layer.storage import SessionLocal, BacktestRun
from data_layer.export_utils import format_dt_display, format_date_display

def get_available_strategies():
    strategies_dir = "config/strategies"
    strategies = {}
    if os.path.exists(strategies_dir):
        for f in os.listdir(strategies_dir):
            if f.endswith('.yaml') or f.endswith('.yml'):
                path = os.path.join(strategies_dir, f)
                strategies[f] = path
    return strategies

available_symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT', 'ADA/USDT', 'DOGE/USDT', 'AVAX/USDT', 'DOT/USDT', 'LINK/USDT', 'BNB/BTC', 'ETH/BTC']

def resolve_strategy_key(raw_name, strategies_dict, config_snapshot=None):
    if not strategies_dict:
        return ''
        
    # Check if config_snapshot explicitly contains strategy_filename or strategy_name
    if config_snapshot:
        try:
            cfg = json.loads(config_snapshot) if isinstance(config_snapshot, str) else config_snapshot
            if 'strategy_filename' in cfg and cfg['strategy_filename'] in strategies_dict:
                return cfg['strategy_filename']
            if 'strategy_name' in cfg and cfg['strategy_name'] in strategies_dict:
                return cfg['strategy_name']
        except Exception:
            pass

    if not raw_name:
        return list(strategies_dict.keys())[0]

    # 1. Direct exact match
    if raw_name in strategies_dict:
        return raw_name

    raw_str = str(raw_name).strip()
    raw_lower = raw_str.lower()

    # 2. Case-insensitive exact match
    for k in strategies_dict:
        if k.lower() == raw_lower:
            return k

    # 3. Add .yaml / .yml extension if missing
    if not (raw_lower.endswith('.yaml') or raw_lower.endswith('.yml')):
        with_yaml = raw_lower + '.yaml'
        for k in strategies_dict:
            if k.lower() == with_yaml:
                return k

    # 4. Slugified match (spaces/hyphens -> underscores)
    slug = raw_lower.replace(' ', '_').replace('-', '_')
    if not (slug.endswith('.yaml') or slug.endswith('.yml')):
        slug_yaml = slug + '.yaml'
    else:
        slug_yaml = slug

    for k in strategies_dict:
        k_slug = k.lower().replace(' ', '_').replace('-', '_')
        if k_slug == slug_yaml or k_slug == slug:
            return k

    # 5. Substring / partial match
    for k in strategies_dict:
        if raw_lower in k.lower() or k.lower() in raw_lower or slug in k.lower():
            return k

    # Default fallback to first strategy
    return list(strategies_dict.keys())[0]

def render_backtest_history_page(on_load_in_analyzer=None, on_open_portfolio=None):
    strategies = get_available_strategies()

    with ui.column().classes('w-full q-pa-sm'):
        
        # ════════════════════════════════════════════════════════
        # 1. HEADER COMPACTO Y OPTIMIZADO
        # ════════════════════════════════════════════════════════
        with ui.card().classes('w-full bg-slate-900 text-white rounded-xl shadow border border-slate-800 px-4 py-2.5 mb-3'):
            with ui.row().classes('items-center justify-between w-full'):
                with ui.row().classes('items-center gap-2.5'):
                    with ui.row().classes('items-center justify-center w-8 h-8 rounded-lg bg-emerald-500/15 border border-emerald-500/30 text-emerald-400'):
                        ui.icon('history', size='1.25rem')
                    with ui.column().classes('gap-0'):
                        ui.label('Historial de Backtests').classes('text-base font-bold tracking-tight text-white leading-tight')
                        ui.label('Consulta, evalúa y recupera simulaciones cuantitativas guardadas').classes('text-slate-400 text-[11px] leading-tight')
                
                with ui.row().classes('items-center gap-2'):
                    def _confirm_clear_all():
                        with ui.dialog() as dlg, ui.card().classes('bg-slate-900 border border-slate-700 p-5 rounded-2xl w-96 text-white'):
                            ui.label('⚠️ Confirmar Eliminación').classes('text-base font-bold text-rose-400 mb-2')
                            ui.label('¿Estás seguro de que deseas eliminar TODOS los backtests guardados en el historial? Esta acción no se puede deshacer.').classes('text-xs text-slate-300 mb-4')
                            with ui.row().classes('w-full justify-end gap-2'):
                                ui.button('Cancelar', on_click=dlg.close).props('flat text-color=grey')
                                def _do_clear():
                                    db = SessionLocal()
                                    try:
                                        db.query(BacktestRun).delete()
                                        db.commit()
                                        ui.notify('🗑️ Todo el historial de backtests ha sido eliminado', type='positive')
                                        load_data()
                                    except Exception as ex:
                                        db.rollback()
                                        ui.notify(f'Error al vaciar historial: {ex}', type='negative')
                                    finally:
                                        db.close()
                                    dlg.close()
                                ui.button('Borrar Todo', icon='delete_forever', on_click=_do_clear).classes('bg-rose-600 hover:bg-rose-500 font-bold text-white px-3 py-1 text-xs rounded-xl')
                        dlg.open()

                    btn_clear_all = ui.button('Borrar Todo', icon='delete_sweep', on_click=_confirm_clear_all).props('dense size=sm rounded').classes('bg-rose-900/60 hover:bg-rose-700 text-rose-200 border border-rose-700 font-bold px-3 py-1 text-xs shadow transition-all')
                    btn_top_portfolio = ui.button('💼 SIMULAR PORTAFOLIO', icon='pie_chart').props('dense size=sm rounded').classes('bg-blue-600 hover:bg-blue-500 text-white font-bold px-3 py-1 text-xs shadow transition-all')
                    btn_refresh = ui.button('Actualizar Lista', icon='refresh').props('dense size=sm rounded').classes('bg-emerald-600 hover:bg-emerald-500 text-white font-bold px-3 py-1 text-xs shadow transition-all')

        # Tarjetas de resumen rápido del historial (Compactas)
        with ui.row().classes('w-full gap-3 mb-3'):
            with ui.card().classes('flex-1 bg-slate-900/50 border border-slate-700/50 p-2.5 rounded-xl shadow'):
                ui.label('Total de Backtests').classes('text-[10px] font-medium text-slate-500 uppercase tracking-wider')
                lbl_total_runs = ui.label('0').classes('text-lg font-black text-slate-200')

            with ui.card().classes('flex-1 bg-slate-900/50 border border-slate-700/50 p-2.5 rounded-xl shadow'):
                ui.label('Mejor CAGR').classes('text-[10px] font-medium text-slate-500 uppercase tracking-wider')
                lbl_best_cagr = ui.label('-- %').classes('text-lg font-black text-emerald-500')

            with ui.card().classes('flex-1 bg-slate-900/50 border border-slate-700/50 p-2.5 rounded-xl shadow'):
                ui.label('Mejor Profit Factor').classes('text-[10px] font-medium text-slate-500 uppercase tracking-wider')
                lbl_best_pf = ui.label('--').classes('text-lg font-black text-blue-400')

            with ui.card().classes('flex-1 bg-slate-900/50 border border-slate-700/50 p-2.5 rounded-xl shadow'):
                ui.label('Promedio % Ganadoras').classes('text-[10px] font-medium text-slate-500 uppercase tracking-wider')
                lbl_avg_wr = ui.label('-- %').classes('text-lg font-black text-purple-400')

        # Tabla del Historial
        with ui.card().classes('w-full bg-slate-900/50 p-4 rounded-2xl shadow-xl border border-slate-700/50 mb-8'):
            with ui.row().classes('w-full items-center justify-between mb-4'):
                ui.label('Simulaciones Registradas').classes('text-lg font-bold text-slate-200')
                search_input = ui.input(placeholder='Buscar por estrategia o símbolo...').props('dense outlined clearable icon=search').classes('w-72')

            history_columns = [
                {'name': 'created_at', 'label': 'Fecha Ejecución', 'field': 'created_at', 'sortable': True},
                {'name': 'strategy_name', 'label': 'Estrategia', 'field': 'strategy_name', 'sortable': True},
                {'name': 'symbol', 'label': 'Símbolo', 'field': 'symbol', 'sortable': True},
                {'name': 'timeframe', 'label': 'TF', 'field': 'timeframe', 'sortable': True},
                {'name': 'date_range', 'label': 'Rango de Fechas', 'field': 'date_range'},
                {'name': 'params_str', 'label': 'Parámetros', 'field': 'params_str'},
                {'name': 'cagr', 'label': 'CAGR (%)', 'field': 'cagr', 'sortable': True},
                {'name': 'max_dd', 'label': 'Max DD (%)', 'field': 'max_dd', 'sortable': True},
                {'name': 'pf', 'label': 'Profit Factor', 'field': 'pf', 'sortable': True},
                {'name': 'win_rate', 'label': '% Ganadoras', 'field': 'win_rate', 'sortable': True},
                {'name': 'trades', 'label': 'Trades', 'field': 'trades', 'sortable': True},
                {'name': 'actions', 'label': 'Acciones', 'field': 'actions'},
            ]

            history_table = ui.table(
                columns=history_columns, 
                rows=[], 
                row_key='run_id',
                pagination={'rowsPerPage': 15}
            ).classes('w-full')
            
            history_table.bind_filter(search_input, 'value')

            history_table.add_slot('body-cell-actions', '''
                <q-td :props="props">
                    <q-btn flat dense color="primary" label="Cargar en Analizador" icon="analytics" @click="() => $parent.$emit('load_to_analyzer', props.row)" />
                    <q-btn flat dense color="secondary" label="Combinar en Portafolio" icon="pie_chart" @click="() => $parent.$emit('open_in_portfolio', props.row)" />
                    <q-btn flat dense round icon="delete" color="negative" @click="() => $parent.$emit('delete_run', props.row)" />
                </q-td>
            ''')

        # ════════════════════════════════════════════════════════
        # LÓGICA DE INTERACCIÓN TABLA HISTORIAL -> PORTAFOLIO
        # ════════════════════════════════════════════════════════
        def load_data():
            db = SessionLocal()
            try:
                runs = db.query(BacktestRun).order_by(BacktestRun.created_at.desc()).all()
                rows = []
                cagrs = []
                pfs = []
                wrs = []

                for run in runs:
                    params_summary = ""
                    if run.config_snapshot:
                        try:
                            cfg = json.loads(run.config_snapshot)
                            custom_p = cfg.get('custom_parameters', {})
                            params_summary = ", ".join([f"{k}:{v}" for k, v in custom_p.items()])
                        except:
                            params_summary = str(run.config_snapshot)[:40]

                    cagr_val = run.cagr if run.cagr is not None else 0.0
                    pf_val = run.profit_factor if run.profit_factor is not None else 0.0
                    wr_val = run.win_rate if run.win_rate is not None else 0.0

                    cagrs.append(cagr_val)
                    pfs.append(pf_val)
                    wrs.append(wr_val)

                    start_str = format_date_display(run.start_date) if run.start_date else ""
                    end_str = format_date_display(run.end_date) if run.end_date else ""
                    date_range_str = f"{start_str} -> {end_str}" if start_str else "N/A"

                    rows.append({
                        'run_id': run.run_id,
                        'created_at': format_dt_display(run.created_at) if run.created_at else "N/A",
                        'strategy_name': run.strategy_name or "",
                        'symbol': run.symbol or "",
                        'timeframe': run.timeframe or "",
                        'date_range': date_range_str,
                        'params_str': params_summary or "Estándar",
                        'cagr': f"{cagr_val:.2f}%",
                        'max_dd': f"{run.max_drawdown_pct or 0.0:.2f}%",
                        'pf': f"{pf_val:.2f}",
                        'win_rate': f"{wr_val:.2f}%",
                        'trades': run.total_trades or 0,
                        'config_snapshot': run.config_snapshot,
                        'start_date': start_str or "01/01/20",
                        'end_date': end_str or format_date_display(datetime.now())
                    })

                history_table.rows = rows
                history_table.update()

                # Actualizar tarjetas superiores
                lbl_total_runs.set_text(str(len(runs)))
                lbl_best_cagr.set_text(f"{max(cagrs):.2f}%" if cagrs else "-- %")
                lbl_best_pf.set_text(f"{max(pfs):.2f}" if pfs else "--")
                avg_wr = sum(wrs) / len(wrs) if wrs else 0.0
                lbl_avg_wr.set_text(f"{avg_wr:.2f}%" if wrs else "-- %")

            except Exception as ex:
                ui.notify(f"Error al cargar historial: {ex}", type="negative")
            finally:
                db.close()

        def on_load_to_analyzer(e):
            row = e.args
            if on_load_in_analyzer:
                on_load_in_analyzer(row)

        def on_delete_run(e):
            row = e.args
            run_id = row.get('run_id')
            if run_id:
                db = SessionLocal()
                try:
                    db.query(BacktestRun).filter(BacktestRun.run_id == run_id).delete()
                    db.commit()
                    ui.notify("🗑️ Registro de backtest eliminado", type="positive")
                    load_data()
                except Exception as ex:
                    db.rollback()
                    ui.notify(f"Error al eliminar registro: {ex}", type="negative")
                finally:
                    db.close()

        def on_open_in_portfolio(e):
            row = e.args
            if on_open_portfolio:
                on_open_portfolio(row)

        def handle_top_open_portfolio():
            if on_open_portfolio:
                on_open_portfolio()

        btn_top_portfolio.on('click', handle_top_open_portfolio)

        history_table.on('load_to_analyzer', on_load_to_analyzer)
        history_table.on('open_in_portfolio', on_open_in_portfolio)
        history_table.on('delete_run', on_delete_run)
        btn_refresh.on('click', load_data)

        # Carga inicial
        load_data()
