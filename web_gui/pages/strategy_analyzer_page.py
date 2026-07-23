import glob
import os
import asyncio
from datetime import datetime, timezone
from nicegui import ui
import pandas as pd

from strategy_engine.base_strategy import BaseStrategy
from backtest_engine.backtester import Backtester
from backtest_engine.optimizer import run_grid_search, count_combinations
from data_layer.market_data import MarketDataManager, normalize_timeframe
from data_layer.storage import SessionLocal, OHLCV
from backtest_engine.metrics import calculate_metrics, calculate_equity_curve_metrics
import yaml

def render_strategy_analyzer():
    with ui.column().classes('w-full q-pa-md'):
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

        with ui.row().classes('w-full justify-between items-center q-mb-md'):
            ui.label('Análisis de estrategia e historial de pruebas retrospectivas').classes('text-2xl font-bold text-primary')
            ui.button('VER CATÁLOGO DE ESTRATEGIAS', on_click=load_catalog, icon='list').classes('bg-blue-600 text-white font-bold')
        
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
            # Reactive display strings for overview cards
            'init_quote_str': '--',
            'init_base_str': '--',
            'bal_quote_str': '--',
            'bal_base_str': '--',
            'pnl_quote_str': '--',
            'pnl_base_str': '--',
        }

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
                state['capital_asset'] = _asset_opts[0]   # default: CITA/QUOTE
                state['capital_type'] = 'QUOTE'

                lbl_asset_hdr = ui.label(f'Activo inicial ({_init_base}/{_init_quote})').classes('text-xs text-gray-500 mb-1')
                capital_type = ui.select(
                    _asset_opts,
                    label='Activo de inicio',
                    value=_asset_opts[0]
                ).classes('w-full')

                def _on_asset_change(e):
                    val = e.value or _asset_opts[0]
                    state['capital_asset'] = val
                    state['capital_type'] = 'QUOTE' if '(CITA)' in val else 'BASE'

                capital_type.on('update:model-value', _on_asset_change)

                def _update_asset_combo(e=None):
                    sym = state['symbol']
                    b, q = _parse_assets(sym)
                    opts = [f'{q} (CITA)', f'{b} (BASE)']
                    capital_type.options = opts
                    capital_type.value = opts[0]
                    state['capital_asset'] = opts[0]
                    state['capital_type'] = 'QUOTE'
                    lbl_asset_hdr.set_text(f'Activo inicial ({b}/{q})')
                    capital_type.update()

                sym_combo.on('update:model-value', _update_asset_combo)

        # ── Fila 3: Botones ──
        with ui.row().classes('w-full mt-4 gap-4'):
            btn_run = ui.button(
                'EJECUTAR PRUEBA RETROSPECTIVA',
                on_click=lambda: asyncio.create_task(run_backtest())
            ).classes('bg-blue-700 text-white font-bold flex-1 py-3')
            btn_optimizer = ui.button(
                'EJECUTAR OPTIMIZADOR (BÚSQUEDA EN CUADRÍCULA)',
                on_click=lambda: open_optimizer()
            ).classes('bg-purple-600 text-white font-bold flex-1 py-3')

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
                {'name': 'trades',  'label': 'Trades',   'field': 'trades',  'sortable': True},
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

                            preview_lbl = ui.label('').classes('w-48 text-xs text-blue-700 font-mono self-center')

                            def _update_preview(pn=p_name, pl=preview_lbl):
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

                                def _mk_field(key, pn=p_name, pl=preview_lbl):
                                    inp = ui.number(
                                        label=key.capitalize(),
                                        value=opt_ranges[pn][key],
                                        format='%.4g'
                                    ).classes('flex-1')
                                    def _on_change(e, k=key, n=pn, pl2=pl):
                                        try:
                                            opt_ranges[n][k] = float(e.value)
                                            _update_preview(n, pl2)
                                        except Exception:
                                            pass
                                    inp.on('update:model-value', _on_change)
                                    return inp

                                _mk_field('min')
                                _mk_field('max')
                                _mk_field('step')
                                opt_ranges_container.add(preview_lbl)  # no-op, already placed

                            _update_preview(p_name, preview_lbl)

                except Exception as ex:
                    with opt_ranges_container:
                        ui.label(f'Error cargando parámetros: {ex}').classes('text-red-500')

            def open_optimizer():
                _build_opt_ranges_ui()
                optimizer_dialog.open()

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

                btn_run_opt.text = f'Calculando... (0/{total_combos})'
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
                    btn_run_opt.text = 'INICIAR OPTIMIZADOR'
                    btn_run_opt.props(remove='disabled')
                    return

                if df_opt.empty:
                    ui.notify('No hay datos para el par/periodo seleccionado. Descarga primero los datos.', type='warning')
                    btn_run_opt.text = 'INICIAR OPTIMIZADOR'
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
                loop = asyncio.get_event_loop()

                import copy
                param_ranges_copy = copy.deepcopy(opt_ranges)
                metric_key = opt_metric.value

                results = await loop.run_in_executor(
                    None,
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
                        'trades': r['total_trades'],
                        'pnl': r['net_pnl'],
                    })

                opt_result_table.rows = rows
                opt_result_table.update()
                opt_progress.value = 1.0
                lbl_progress.set_text(f'{len(results)} combinaciones completadas — ordenadas por {metric_key}')
                btn_run_opt.text = 'INICIAR OPTIMIZADOR'
                btn_run_opt.props(remove='disabled')
                ui.notify(f'Optimización completa: {len(results)} combinaciones', type='positive')

            btn_run_opt.on('click', lambda: asyncio.ensure_future(_run_optimizer()))

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
                ui.label('Comercios totales').classes('text-sm text-gray-500')
                lbl_total_trades = ui.label('--').classes('text-2xl font-bold')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                lbl_hdr_init_q = ui.label('Capital Inicial (CITA)').classes('text-sm text-gray-500')
                lbl_init_quote = ui.label('--').classes('text-2xl font-bold text-black')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                lbl_hdr_init_b = ui.label('Capital Inicial (BASE)').classes('text-sm text-gray-500')
                lbl_init_base = ui.label('--').classes('text-2xl font-bold text-black')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                lbl_hdr_bal_q = ui.label('Saldo final (CITA)').classes('text-sm text-gray-500')
                lbl_bal_quote = ui.label('--').classes('text-2xl font-bold text-black')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                lbl_hdr_bal_b = ui.label('Saldo final (BASE)').classes('text-sm text-gray-500')
                lbl_bal_base = ui.label('--').classes('text-2xl font-bold text-black')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                lbl_hdr_pnl_q = ui.label('Total de pérdidas y ganancias (CITA)').classes('text-sm text-gray-500')
                lbl_pnl_quote = ui.label('--').classes('text-2xl font-bold text-black')
            with ui.card().classes('flex-1 items-center p-4 bg-gray-50 min-w-[150px]'):
                lbl_hdr_pnl_b = ui.label('Total de pérdidas y ganancias (BASE)').classes('text-sm text-gray-500')
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

        ui.label('Ejecuciones (Operaciones)').classes('text-lg font-bold mt-6')
        trades_columns = []
        trades_table = ui.table(columns=trades_columns, rows=[], row_key='entry_time').classes('w-full')
        trades_table.add_slot('body-cell-result', '''
            <q-td :props="props">
                <span :style="{ color: props.value === 'W' ? '#16a34a' : '#dc2626',
                                fontWeight: 'bold', fontSize: '1rem' }">
                    {{ props.value }}
                </span>
            </q-td>
        ''')

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
                strategy.timeframe = normalize_timeframe(state['timeframe'])
                
                db = SessionLocal()
                market_mgr = MarketDataManager(db)
                df = market_mgr.get_data(
                    strategy.symbol, 
                    strategy.timeframe, 
                    start_dt,
                    end_dt
                )
                
                if df.empty:
                    ui.notify(f"Buscando y descargando datos en vivo para {strategy.symbol} ({strategy.timeframe})...", type="info")
                    try:
                        market_mgr.fetch_and_store_ohlcv(
                            symbol=strategy.symbol,
                            timeframe=strategy.timeframe,
                            start_date=start_dt,
                            end_date=end_dt
                        )
                        df = market_mgr.get_data(
                            strategy.symbol, 
                            strategy.timeframe, 
                            start_dt,
                            end_dt
                        )
                    except Exception as fetch_ex:
                        ui.notify(f"No se pudieron obtener datos automáticamente: {fetch_ex}", type="warning")
                db.close()
                
                if df.empty:
                    ui.notify(f"No hay datos históricos disponibles para {strategy.symbol} en {strategy.timeframe}. Por favor descarga datos desde 'Datos Almacenados'.", type="warning")
                    btn_run.text = "Run Backtest"
                    return
                    
                start_price = df.iloc[0]['open'] if not df.empty else 1.0
                
                if state.get('capital_type', 'QUOTE') == 'BASE':
                    initial_cap_quote = state['capital'] * start_price
                else:
                    initial_cap_quote = state['capital']
                
                initial_cap_base = initial_cap_quote / start_price if start_price > 0 else 0
                    
                backtester = Backtester(strategy, initial_capital=initial_cap_quote)
                # Ejecutar en executor para NO bloquear el event loop de NiceGUI
                loop = asyncio.get_event_loop()
                results = await loop.run_in_executor(None, backtester.run, df)
                await asyncio.sleep(0)  # Ceder el control al event loop
                
                trades_df = results.get("trades")
                equity_curve = results.get("equity_curve")
                
                import traceback
                # Normalize equity_curve index to DatetimeIndex if it's a column
                if equity_curve is not None and not equity_curve.empty:
                    if 'timestamp' in equity_curve.columns:
                        equity_curve = equity_curve.set_index('timestamp')
                    equity_curve.index = pd.to_datetime(equity_curve.index)

                if equity_curve is not None and not equity_curve.empty:
                    try:
                        trade_metrics = calculate_metrics(trades_df, initial_cap_quote) if trades_df is not None and not trades_df.empty else {'total_trades': 0}
                        eq_metrics = calculate_equity_curve_metrics(equity_curve['equity'])

                        def safe_num(v, default=0.0):
                            if v is None or pd.isna(v) or np.isnan(v) or np.isinf(v):
                                return default
                            try:
                                return float(v)
                            except:
                                return default

                        cagr_val = safe_num(eq_metrics.get('cagr', 0.0))
                        maxdd_val = safe_num(eq_metrics.get('max_drawdown_pct', 0.0))
                        sharpe_val = safe_num(eq_metrics.get('sharpe_ratio', 0.0))
                        n_trades = int(safe_num(trade_metrics.get('total_trades', 0)))

                        lbl_cagr.set_text(f"{cagr_val:.2f}%")
                        lbl_maxdd.set_text(f"{maxdd_val:.2f}%")
                        lbl_sharpe.set_text(f"{sharpe_val:.2f}")
                        lbl_total_trades.set_text(str(n_trades))
                        # Update colors
                        lbl_cagr.classes(remove='text-green-600 text-red-600', add='text-green-600' if cagr_val >= 0 else 'text-red-600')
                        lbl_maxdd.classes(remove='text-green-600 text-red-600', add='text-red-600')
                        lbl_sharpe.classes(remove='text-green-600 text-red-600 text-blue-600', add='text-blue-600' if sharpe_val >= 0 else 'text-red-600')
                        
                        # Calculate drawdown series (needed for trade table too)
                        roll_max = equity_curve['equity'].cummax()
                        drawdown = (equity_curve['equity'] - roll_max) / roll_max * 100

                        dates_eq = equity_curve.index.strftime('%Y-%m-%d').tolist()
                        chart.options['xAxis']['data'] = dates_eq
                        chart.options['series'][0]['data'] = equity_curve['equity'].round(6).tolist()
                        chart.update()

                        drawdown_chart.options['xAxis']['data'] = dates_eq
                        drawdown_chart.options['series'][0]['data'] = drawdown.round(4).tolist()
                        drawdown_chart.update()
                        
                        dates_price = df.index.strftime('%Y-%m-%d %H:%M').tolist()
                        price_chart.options['xAxis']['data'] = dates_price
                        price_chart.options['series'][0]['data'] = df[['open', 'close', 'low', 'high']].values.tolist()
                        price_chart.update()
                    except Exception as metrics_err:
                        tb = traceback.format_exc()
                        ui.notify(f"Error en métricas: {metrics_err}\n{tb[:400]}", type='negative', timeout=15000)
                        drawdown = pd.Series(dtype=float)
                else:
                    drawdown = pd.Series(dtype=float)
                    lbl_cagr.set_text("0.00%")
                    lbl_maxdd.set_text("0.00%")
                    lbl_sharpe.set_text("0.00")
                    lbl_total_trades.set_text("0")
                    
                    chart.options['xAxis']['data'] = df.index.strftime('%Y-%m-%d').tolist()
                    chart.options['series'][0]['data'] = [round(initial_cap_quote, 6)] * len(df)
                    chart.update()
                    
                    drawdown_chart.options['xAxis']['data'] = df.index.strftime('%Y-%m-%d').tolist()
                    drawdown_chart.options['series'][0]['data'] = [0] * len(df)
                    drawdown_chart.update()
                    
                    price_chart.options['xAxis']['data'] = df.index.strftime('%Y-%m-%d %H:%M').tolist()
                    price_chart.options['series'][0]['data'] = df[['open', 'close', 'low', 'high']].values.tolist()
                    price_chart.update()
                    ui.notify("El backtest no generó curva de equity (sin operaciones o error en señales)", type='warning')


                # Dynamically configure trades table columns with base and quote assets
                base_asset = state['symbol'].split('/')[0] if '/' in state['symbol'] else 'BTC'
                quote_asset = state['symbol'].split('/')[1] if '/' in state['symbol'] else 'USDT'
                
                state['quote_asset'] = quote_asset
                state['base_asset'] = base_asset
                
                columns = [
                    {'name': 'result', 'label': 'W/L', 'field': 'result', 'sortable': True},
                    {'name': 'entry_time', 'label': 'Fecha entrada (UTC)', 'field': 'entry_time', 'sortable': True},
                    {'name': 'exit_time', 'label': 'Fecha salida (UTC)', 'field': 'exit_time', 'sortable': True},
                    {'name': 'side', 'label': 'Lado', 'field': 'side', 'sortable': True},
                    {'name': 'entry_price', 'label': 'Precio de entrada', 'field': 'entry_price', 'sortable': True},
                    {'name': 'exit_price', 'label': 'Precio de salida', 'field': 'exit_price', 'sortable': True},
                    {'name': 'pnl_quote', 'label': f'P&L ({quote_asset})', 'field': 'pnl_quote', 'sortable': True},
                    {'name': 'pnl_base', 'label': f'P&L ({base_asset})', 'field': 'pnl_base', 'sortable': True},
                    {'name': 'cum_pnl_quote', 'label': f'P&L acumulado ({quote_asset})', 'field': 'cum_pnl_quote', 'sortable': True},
                    {'name': 'cum_pnl_base', 'label': f'P&L acumulado ({base_asset})', 'field': 'cum_pnl_base', 'sortable': True},
                    {'name': 'balance_quote', 'label': f'Saldo ({quote_asset})', 'field': 'balance_quote', 'sortable': True},
                    {'name': 'balance_base', 'label': f'Saldo ({base_asset})', 'field': 'balance_base', 'sortable': True},
                    {'name': 'drawdown', 'label': 'Reducción máxima (%)', 'field': 'drawdown', 'sortable': True},
                    {'name': 'exit_reason', 'label': 'Razón', 'field': 'exit_reason', 'sortable': True},
                ]
                trades_table.columns = columns
                
                # ── Smart price formatting (handles crypto pairs like BNB/BTC where prices < 1) ──
                def fmt_price(p):
                    """Auto-precision price formatter."""
                    if p is None: return 'N/A'
                    if abs(p) >= 1000:   return f"{p:,.2f}"
                    if abs(p) >= 1:      return f"{p:.4f}"
                    if abs(p) >= 0.01:   return f"{p:.6f}"
                    return f"{p:.8f}"

                def fmt_pnl(p, precision=8):
                    """Auto-precision PnL formatter."""
                    if p is None: return 'N/A'
                    if abs(p) >= 1000:   return f"{p:+,.2f}"
                    if abs(p) >= 1:      return f"{p:+.4f}"
                    if abs(p) >= 0.01:   return f"{p:+.6f}"
                    return f"{p:+.8f}"

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
                            'result': 'W' if pnl_quote > 0 else 'L',
                            'entry_time': str(row.get('entry_time', ''))[:19],
                            'exit_time': str(row.get('exit_time', ''))[:19],
                            'side': str(row.get('side', '')).upper(),
                            'entry_price': fmt_price(row.get('entry_price', 0)),
                            'exit_price': fmt_price(row.get('exit_price', 0)),
                            'pnl_quote': fmt_pnl(pnl_quote),
                            'pnl_base': fmt_pnl(pnl_base),
                            'cum_pnl_quote': fmt_pnl(cum_q),
                            'cum_pnl_base': fmt_pnl(cum_b),
                            'balance_quote': fmt_price(balance_quote),
                            'balance_base': fmt_price(balance_base),
                            'drawdown': f"{dd_val:.2f}%",
                            'exit_reason': str(row.get('exit_reason', ''))
                        })
                trades_table.rows = trades_rows
                trades_table.update()
                
                # ── Update asset header labels ──
                lbl_hdr_init_q.set_text(f'Capital Inicial ({quote_asset})')
                lbl_hdr_init_b.set_text(f'Capital Inicial ({base_asset})')
                lbl_hdr_bal_q.set_text(f'Balance Final ({quote_asset})')
                lbl_hdr_bal_b.set_text(f'Balance Final ({base_asset})')
                lbl_hdr_pnl_q.set_text(f'P&L Total ({quote_asset})')
                lbl_hdr_pnl_b.set_text(f'P&L Total ({base_asset})')

                lbl_init_quote.set_text(f"{fmt_price(initial_cap_quote)}")
                lbl_init_base.set_text(f"{fmt_price(initial_cap_base)}")
                lbl_bal_quote.set_text(f"{fmt_price(balance_quote)}")
                lbl_bal_base.set_text(f"{fmt_price(balance_base)}")
                lbl_pnl_quote.set_text(f"{fmt_pnl(cum_q)}")
                lbl_pnl_base.set_text(f"{fmt_pnl(cum_b)}")

                def update_color(lbl, is_positive):
                    lbl.classes(remove='text-black text-green-600 text-red-600', add='text-green-600' if is_positive else 'text-red-600')
                    
                update_color(lbl_bal_quote, balance_quote >= initial_cap_quote)
                update_color(lbl_bal_base, balance_base >= initial_cap_base)
                update_color(lbl_pnl_quote, cum_q >= 0)
                update_color(lbl_pnl_base, cum_b >= 0)
                        
                ui.notify("Backtest completado correctamente.", type="positive")
                
            except Exception as e:
                ui.notify(f"Error during backtest: {e}", type="negative")
            finally:
                btn_run.text = "Run Backtest"

        def select_strategy(filename):
            if filename in strategies:
                state['strategy_name'] = filename
                strat_combo.value = filename
                update_parameters_ui()

        return {'select_strategy': select_strategy}
