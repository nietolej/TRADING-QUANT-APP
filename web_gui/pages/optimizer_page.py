from nicegui import ui, run
import pandas as pd
import numpy as np
import yaml
import glob
import os
from datetime import datetime, timezone
import asyncio
import plotly.graph_objects as go

from data_layer.storage import SessionLocal, OHLCV
from data_layer.market_data import MarketDataManager, normalize_timeframe
from backtest_engine.optimizer import run_grid_search, count_combinations, _build_range
from backtest_engine.robustness_analyzer import analyze_robustness
from sqlalchemy import func


def _parse_assets(symbol: str) -> tuple[str, str]:
    """Extrae (base_asset, quote_asset) de un string 'BTC/USDT'."""
    if symbol and '/' in symbol:
        parts = symbol.split('/')
        return parts[0].strip(), parts[1].strip()
    return 'BTC', 'USDT'


def render_optimizer_page(on_go_to_analyzer=None):
    """
    Página independiente para el Optimizador Cuantitativo (Grid Search)
    con Analizador Avanzado de Robustez, Sensibilidad de Parámetros y Mesetas.
    """
    
    # ── Estado de la página ──
    strategy_files = glob.glob("config/strategies/*.yaml")
    strategies = {os.path.basename(f): f for f in strategy_files}
    default_strategy = list(strategies.keys())[0] if strategies else ''
    
    state = {
        'strategy_name': default_strategy,
        'symbol': 'BTC/USDT',
        'timeframe': '1d',
        'start_date': '2020-01-01',
        'end_date': datetime.now().strftime('%Y-%m-%d'),
        'capital': 1.0,
        'capital_type': 'BASE',
        'capital_asset': 'BTC (BASE)',
        'sizing_mode': 'Interés Compuesto (100% Capital)',
        'fixed_amount': 1.0,
        'commission_pct': 0.1,
        'slippage_pct': 0.05,
        'ec_enabled': False,
        'ec_start_dd': 30.0,
        'ec_stop_dd': 0.0,
        'cl_enabled': False,
        'cl_start': 3,
        'cl_stop': 0.0,
        'optimize_metric': 'sharpe_ratio',
        'is_running': False,
        'last_results': [],
        'robustness_data': None
    }

    opt_ranges: dict = {}
    preview_labels: dict = {}

    with ui.column().classes('w-full q-pa-sm gap-3'):
        
        # ════════════════════════════════════════════════════════
        # 1. HEADER HERO (COMPACTO Y ELEGANTE)
        # ════════════════════════════════════════════════════════
        with ui.card().classes('w-full bg-slate-900 text-white rounded-xl shadow border border-slate-800 px-4 py-2.5 mb-1'):
            with ui.row().classes('w-full justify-between items-center'):
                with ui.row().classes('items-center gap-2.5'):
                    with ui.row().classes('items-center justify-center w-8 h-8 rounded-lg bg-purple-500/15 border border-purple-500/30 text-purple-400'):
                        ui.icon('tune', size='1.25rem')
                    with ui.column().classes('gap-0'):
                        ui.label('Optimizador de Estrategias y Análisis de Robustez').classes('text-base font-bold tracking-tight text-white leading-tight')
                        ui.label('Exploración masiva de parámetros, detección de mesetas óptimas y evaluación de sobreajuste (Overfitting).').classes('text-slate-400 text-[11px] leading-tight')
                
                with ui.row().classes('items-center gap-2'):
                    if on_go_to_analyzer:
                        ui.button('Ir a Strategy Analyzer', icon='analytics', on_click=lambda: on_go_to_analyzer(state.get('strategy_name'), state.get('symbol'), state.get('timeframe'))) \
                            .props('dense size=sm rounded').classes('bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs px-3 py-1')

        # ════════════════════════════════════════════════════════
        # 2. PANEL DE CONFIGURACIÓN DEL BACKTEST
        # ════════════════════════════════════════════════════════
        with ui.card().classes('w-full bg-slate-900/60 border border-slate-800 rounded-xl p-4 shadow'):
            ui.label('1. Parámetros del Entorno de Backtest').classes('text-sm font-bold text-slate-200 uppercase tracking-wider mb-2')
            
            # Fila 1: Estrategia + Símbolo + TF + Fechas
            with ui.row().classes('w-full gap-3 items-end'):
                # Selector de Estrategia
                with ui.column().classes('flex-1 min-w-[200px] gap-0'):
                    ui.label('Estrategia a Optimizar').classes('text-xs text-gray-400 mb-1')
                    def _on_strat_change(e):
                        state['strategy_name'] = e.value
                        _build_ranges_ui()
                    strat_combo = ui.select(list(strategies.keys()), value=state['strategy_name'], on_change=_on_strat_change).classes('w-full')

                # Selector de Símbolo
                with ui.column().classes('flex-1 min-w-[160px] gap-0'):
                    ui.label('Activo / Par').classes('text-xs text-gray-400 mb-1')
                    sym_combo = ui.select(
                        ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'BNB/BTC', 'XRP/USDT', 'ADA/USDT', 'DOGE/USDT', 'AVAX/USDT'],
                        value=state['symbol']
                    ).bind_value(state, 'symbol').classes('w-full')

                # Selector de Temporalidad
                with ui.column().classes('w-32 gap-0'):
                    ui.label('Temporalidad').classes('text-xs text-gray-400 mb-1')
                    tf_combo = ui.select(
                        ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w'],
                        value=state['timeframe']
                    ).bind_value(state, 'timeframe').classes('w-full')

                # Fecha Inicio
                with ui.column().classes('flex-1 min-w-[140px] gap-0'):
                    ui.label('Fecha de Inicio').classes('text-xs text-gray-400 mb-1')
                    start_date_input = ui.input('Inicio', value=state['start_date']).bind_value(state, 'start_date').classes('w-full')

                # Fecha Fin
                with ui.column().classes('flex-1 min-w-[140px] gap-0'):
                    ui.label('Fecha de Fin').classes('text-xs text-gray-400 mb-1')
                    end_date_input = ui.input('Fin', value=state['end_date']).bind_value(state, 'end_date').classes('w-full')

            # Fila 2: Capital + Activo de Inicio + Sizing + Fricción
            with ui.row().classes('w-full gap-3 items-end mt-3'):
                with ui.column().classes('flex-1 gap-0'):
                    ui.label('Capital Inicial').classes('text-xs text-gray-400 mb-1')
                    capital_input = ui.number('Capital', value=state['capital'], min=0.00001, step=1.0).bind_value(state, 'capital').classes('w-full')

                with ui.column().classes('w-44 gap-0'):
                    _init_base, _init_quote = _parse_assets(state['symbol'])
                    _asset_opts = [f'{_init_quote} (CITA)', f'{_init_base} (BASE)']
                    lbl_asset_hdr = ui.label(f'Activo inicial ({_init_base}/{_init_quote})').classes('text-xs text-gray-400 mb-1')
                    
                    def _get_active_asset_name():
                        b, q = _parse_assets(state.get('symbol', 'BTC/USDT'))
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

                    sym_combo.on('update:model-value', _update_asset_combo)

                with ui.column().classes('flex-1 gap-0'):
                    ui.label('Modo de Posición (Sizing)').classes('text-xs text-gray-400 mb-1')
                    sizing_combo = ui.select(
                        ['Interés Compuesto (100% Capital)', 'Monto Fijo por Operación', 'Riesgo Fijo (1% por trade)'],
                        label='Modo de Tamaño',
                        value=state['sizing_mode']
                    ).bind_value(state, 'sizing_mode').classes('w-full')

                with ui.column().classes('w-44 gap-0') as col_fixed_amt:
                    lbl_fixed_amt = ui.label(f"Monto por Trade ({_get_active_asset_name()})").classes('text-xs text-gray-400 mb-1')
                    fixed_amt_input = ui.number('Monto Fijo', value=state['fixed_amount'], min=0.000001, step=0.1).bind_value(state, 'fixed_amount').classes('w-full')

                def _update_sizing_ui(e=None):
                    cur_mode = str(state.get('sizing_mode', ''))
                    is_fixed = 'Monto Fijo' in cur_mode
                    col_fixed_amt.set_visibility(is_fixed)
                    lbl_fixed_amt.set_text(f"Monto por Trade ({_get_active_asset_name()})")

                sizing_combo.on_value_change(_update_sizing_ui)
                _update_sizing_ui()

                with ui.column().classes('w-32 gap-0'):
                    ui.label('Comisión (%)').classes('text-xs text-gray-400 mb-1')
                    comm_input = ui.number('Comisión', value=state['commission_pct'], step=0.01, min=0.0).bind_value(state, 'commission_pct').classes('w-full')

                with ui.column().classes('w-32 gap-0'):
                    ui.label('Slippage (%)').classes('text-xs text-gray-400 mb-1')
                    slip_input = ui.number('Slippage', value=state['slippage_pct'], step=0.01, min=0.0).bind_value(state, 'slippage_pct').classes('w-full')

            # Fila 3: Filtro Equity Curve (Drawdown & Pérdidas Consecutivas)
            with ui.row().classes('w-full gap-3 items-end mt-3 bg-slate-900/80 p-2.5 rounded-lg border border-slate-800'):
                with ui.column().classes('flex-none gap-0 justify-center'):
                    ui.label('Filtro Drawdown').classes('text-xs text-amber-400 font-bold mb-1')
                    ec_toggle = ui.toggle(
                        ['Inactivo', 'Activo'],
                        value='Activo' if state.get('ec_enabled', False) else 'Inactivo'
                    ).props('toggle-color=amber-8 color=grey-9 text-color=grey-4 toggle-text-color=white size=sm no-caps')
                    ec_toggle.on_value_change(lambda e: state.update({'ec_enabled': (e.value == 'Activo')}))

                with ui.column().classes('flex-1 gap-0'):
                    ui.label('Start DD % (>=)').classes('text-xs text-gray-400 mb-1')
                    ui.number('Start DD', value=state['ec_start_dd'], step=1.0, min=0.0).bind_value(state, 'ec_start_dd').classes('w-full')

                with ui.column().classes('flex-1 gap-0'):
                    ui.label('Stop DD Gain % (>=)').classes('text-xs text-gray-400 mb-1')
                    ui.number('Stop DD', value=state['ec_stop_dd'], step=1.0, min=0.0).bind_value(state, 'ec_stop_dd').classes('w-full')

                with ui.column().classes('flex-none gap-0 justify-center'):
                    ui.label('Filtro Pérdidas Consec.').classes('text-xs text-amber-400 font-bold mb-1')
                    cl_toggle = ui.toggle(
                        ['Inactivo', 'Activo'],
                        value='Activo' if state.get('cl_enabled', False) else 'Inactivo'
                    ).props('toggle-color=amber-8 color=grey-9 text-color=grey-4 toggle-text-color=white size=sm no-caps')
                    cl_toggle.on_value_change(lambda e: state.update({'cl_enabled': (e.value == 'Activo')}))

                with ui.column().classes('flex-1 gap-0'):
                    ui.label('Start Losers (>=)').classes('text-xs text-gray-400 mb-1')
                    ui.number('Start L', value=state['cl_start'], step=1.0, min=0.0).bind_value(state, 'cl_start').classes('w-full')

                with ui.column().classes('flex-1 gap-0'):
                    ui.label('Stop Losers Gain % (>=)').classes('text-xs text-gray-400 mb-1')
                    ui.number('Stop Gain %', value=state['cl_stop'], step=1.0, min=0.0).bind_value(state, 'cl_stop').classes('w-full')

        # ════════════════════════════════════════════════════════
        # 3. PANEL DE RANGOS DE PARÁMETROS DINÁMICOS
        # ════════════════════════════════════════════════════════
        with ui.card().classes('w-full bg-slate-900/60 border border-slate-800 rounded-xl p-4 shadow'):
            with ui.row().classes('w-full justify-between items-center mb-2'):
                ui.label('2. Definición de Rangos de Parámetros a Explorar').classes('text-sm font-bold text-slate-200 uppercase tracking-wider')
                lbl_combo_info = ui.label('ℹ️  Calculando combinaciones...').classes('text-xs font-semibold text-purple-400 bg-purple-950/40 px-3 py-1 rounded-full border border-purple-800/40')

            opt_ranges_container = ui.column().classes('w-full gap-2 mt-2')

            def _refresh_all_previews():
                """Actualiza las etiquetas de previsualización y el total de combinaciones."""
                try:
                    for pn, pl in preview_labels.items():
                        cfg = opt_ranges.get(pn, {})
                        mn = float(cfg.get('min', 0))
                        mx = float(cfg.get('max', 0))
                        st = float(cfg.get('step', 1))
                        vals = _build_range(mn, mx, st)
                        if len(vals) > 12:
                            disp = ', '.join(str(x) for x in vals[:10]) + f' ... (+{len(vals)-10})'
                        else:
                            disp = ', '.join(str(x) for x in vals)
                        pl.set_text(disp if disp else '--')

                    combo_count = count_combinations(opt_ranges)
                    lbl_combo_info.set_text(
                        f'ℹ️  Total combinaciones: {combo_count:,}  '
                        f'(tiempo estimado: ~{max(1, combo_count // 12)}s)'
                    )
                except Exception as ex:
                    lbl_combo_info.set_text(f'ℹ️  Combinaciones: {ex}')

            def _build_ranges_ui():
                opt_ranges_container.clear()
                opt_ranges.clear()
                preview_labels.clear()

                strat_file = strategies.get(state['strategy_name'])
                if not strat_file or not os.path.exists(strat_file):
                    with opt_ranges_container:
                        ui.label('Selecciona una estrategia válida para cargar sus parámetros.').classes('text-gray-400 text-sm')
                    return

                try:
                    with open(strat_file, 'r', encoding='utf-8') as fh:
                        data = yaml.safe_load(fh)
                    params = data.get('parameters', {}) if data else {}

                    if not params:
                        with opt_ranges_container:
                            ui.label('Esta estrategia no tiene parámetros configurables en su archivo YAML.').classes('text-gray-400 text-sm')
                        lbl_combo_info.set_text('0 combinaciones')
                        return

                    with opt_ranges_container:
                        # Encabezados
                        with ui.row().classes('w-full gap-3 text-xs font-bold text-slate-400 border-b border-slate-800 pb-2 px-1'):
                            ui.label('Parámetro').classes('w-36')
                            ui.label('Defecto').classes('w-20 text-center')
                            ui.label('Mínimo').classes('flex-1')
                            ui.label('Máximo').classes('flex-1')
                            ui.label('Paso (Step)').classes('flex-1')
                            ui.label('Valores a Probar').classes('w-72')

                        for p_name, p_val in params.items():
                            try:
                                p_num = float(p_val)
                            except (ValueError, TypeError):
                                continue  # Parámetro no numérico

                            default_min = p_num
                            default_max = p_num * 3 if p_num > 0 else 10.0
                            default_step = max(1.0, p_num)
                            
                            opt_ranges[p_name] = {'min': default_min, 'max': default_max, 'step': default_step}

                            with ui.row().classes('w-full gap-3 items-center py-1.5 border-b border-slate-800/50 hover:bg-slate-800/30 px-1 rounded'):
                                ui.label(p_name).classes('w-36 font-bold text-purple-400')
                                ui.label(str(int(p_num) if p_num == int(p_num) else p_num)).classes('w-20 text-center text-slate-300 text-xs font-mono')

                                def _make_input(pn, key):
                                    def _on_val_change(e):
                                        try:
                                            if e.value is not None:
                                                opt_ranges[pn][key] = float(e.value)
                                                _refresh_all_previews()
                                        except Exception:
                                            pass

                                    inp = ui.number(
                                        label=key.capitalize(),
                                        value=opt_ranges[pn][key],
                                        format='%.4g',
                                        on_change=_on_val_change
                                    ).classes('flex-1')
                                    return inp

                                _make_input(p_name, 'min')
                                _make_input(p_name, 'max')
                                _make_input(p_name, 'step')

                                plbl = ui.label('').classes('w-72 text-xs text-blue-400 font-mono self-center overflow-hidden')
                                preview_labels[p_name] = plbl

                    _refresh_all_previews()

                except Exception as ex:
                    with opt_ranges_container:
                        ui.label(f'Error cargando parámetros: {ex}').classes('text-red-500')

            _build_ranges_ui()

        # ════════════════════════════════════════════════════════
        # 4. PANEL DE CONTROL DE EJECUCIÓN
        # ════════════════════════════════════════════════════════
        with ui.card().classes('w-full bg-slate-900/60 border border-slate-800 rounded-xl p-4 shadow'):
            with ui.row().classes('w-full gap-4 items-center justify-between'):
                with ui.row().classes('items-center gap-3 flex-1'):
                    ui.label('Optimizar y Clasificar por:').classes('text-sm font-semibold text-slate-300')
                    opt_metric = ui.select(
                        {
                            'sharpe_ratio': 'Coeficiente de Sharpe',
                            'cagr': 'CAGR (%)',
                            'net_pnl': 'PnL Neto',
                            'profit_factor': 'Profit Factor',
                            'max_drawdown_pct': 'Menor Reducción Máxima (DD)',
                            'percent_profitable': 'Tasa de Acierto (Win Rate %)'
                        },
                        value=state['optimize_metric']
                    ).bind_value(state, 'optimize_metric').classes('w-64')

                btn_run_opt = ui.button(
                    '🚀 INICIAR OPTIMIZADOR (GRID SEARCH)',
                    on_click=lambda: asyncio.create_task(_run_optimizer())
                ).classes('bg-amber-500 hover:bg-amber-400 text-slate-950 font-extrabold px-8 py-3 rounded-xl shadow-lg transition-all text-sm tracking-wide')

            # Barra de Progreso
            opt_progress = ui.linear_progress(value=0).classes('w-full mt-4').props('color=amber-8')
            lbl_progress = ui.label('').classes('text-xs text-slate-400 mt-1')

        # ════════════════════════════════════════════════════════
        # 5. TABLA DE RESULTADOS DE LA OPTIMIZACIÓN
        # ════════════════════════════════════════════════════════
        with ui.card().classes('w-full bg-slate-900/60 border border-slate-800 rounded-xl p-4 shadow'):
            with ui.row().classes('w-full justify-between items-center mb-2'):
                ui.label('3. Clasificación de Resultados de Optimización').classes('text-sm font-bold text-slate-200 uppercase tracking-wider')
                lbl_res_info = ui.label('Esperando ejecución...').classes('text-xs text-slate-400')

            opt_result_cols = [
                {'name': 'rank',          'label': '#',           'field': 'rank',         'sortable': True,  'align': 'center'},
                {'name': 'params',        'label': 'Parámetros Probados', 'field': 'params', 'sortable': False, 'align': 'left'},
                {'name': 'sharpe',        'label': 'Sharpe',      'field': 'sharpe',       'sortable': True,  'align': 'right'},
                {'name': 'cagr',          'label': 'CAGR (%)',    'field': 'cagr',         'sortable': True,  'align': 'right'},
                {'name': 'maxdd',         'label': 'Max DD (%)',  'field': 'maxdd',        'sortable': True,  'align': 'right'},
                {'name': 'profit_factor', 'label': 'PF',          'field': 'profit_factor','sortable': True,  'align': 'right'},
                {'name': 'trades',        'label': 'Trades',      'field': 'trades',       'sortable': True,  'align': 'right'},
                {'name': 'win_rate',      'label': 'Win Rate',    'field': 'win_rate',     'sortable': True,  'align': 'right'},
                {'name': 'cons_losses',   'label': 'Max L-Seq',   'field': 'cons_losses',  'sortable': True,  'align': 'right'},
                {'name': 'final_eq',      'label': 'Balance Final','field': 'final_eq',   'sortable': True,  'align': 'right'},
                {'name': 'pnl',           'label': 'PnL Neto',    'field': 'pnl',          'sortable': True,  'align': 'right'},
                {'name': 'actions',       'label': 'Acciones',    'field': 'actions',      'sortable': False, 'align': 'center'},
            ]

            opt_result_table = ui.table(
                columns=opt_result_cols, rows=[], row_key='rank'
            ).props('dense flat').classes('w-full mt-2 text-sm font-medium')

            opt_result_table.add_slot('body-cell-rank', '''
                <q-td :props="props">
                    <q-badge :color="props.value === 1 ? 'amber-8' : props.value === 2 ? 'grey-5' : props.value === 3 ? 'orange-9' : 'purple-9'" 
                             :label="'#' + props.value" class="text-xs font-bold font-mono" />
                </q-td>
            ''')

            opt_result_table.add_slot('body-cell-sharpe', '''
                <q-td :props="props">
                    <span :style="{color: props.value >= 1 ? '#4ade80' : props.value >= 0 ? '#fbbf24' : '#f87171', fontWeight: 'bold'}">
                        {{ props.value }}
                    </span>
                </q-td>
            ''')

            opt_result_table.add_slot('body-cell-cagr', '''
                <q-td :props="props">
                    <span :style="{color: props.value >= 0 ? '#4ade80' : '#f87171', fontWeight: 'bold'}">
                        {{ props.value >= 0 ? '+' : '' }}{{ props.value }}%
                    </span>
                </q-td>
            ''')

            opt_result_table.add_slot('body-cell-pnl', '''
                <q-td :props="props">
                    <span :style="{color: props.value >= 0 ? '#4ade80' : '#f87171', fontWeight: 'bold'}">
                        {{ props.value >= 0 ? '+' : '' }}{{ Number(props.value).toLocaleString() }}
                    </span>
                </q-td>
            ''')

            opt_result_table.add_slot('body-cell-actions', '''
                <q-td :props="props">
                    <q-btn flat dense round icon="analytics" color="amber-8" 
                           @click="() => $parent.$emit('apply_params', props.row)" tooltip="Abrir en Strategy Analyzer" />
                </q-td>
            ''')

            def _on_apply_params(e):
                row = e.args
                raw_p = row.get('raw_params', {})
                if on_go_to_analyzer:
                    on_go_to_analyzer(state.get('strategy_name'), state.get('symbol'), state.get('timeframe'), raw_p)
                else:
                    ui.notify(f"Parámetros seleccionados: {raw_p}", type='positive')

            opt_result_table.on('apply_params', _on_apply_params)

        # ════════════════════════════════════════════════════════
        # 6. GRÁFICO COMPARATIVO TOP 5 ESTRATEGIAS
        # ════════════════════════════════════════════════════════
        chart_card = ui.card().classes('w-full bg-slate-900/60 border border-slate-800 rounded-xl p-4 shadow')
        chart_card.set_visibility(False)
        with chart_card:
            ui.label('4. Comparación Visual de Curvas de Capital (Top 5 Configuraciones)').classes('text-sm font-bold text-slate-200 uppercase tracking-wider mb-2')
            top_chart_plot = ui.plotly({}).classes('w-full h-80')

        # ════════════════════════════════════════════════════════
        # 7. ANALIZADOR DE ROBUSTEZ Y SENSIBILIDAD CUANTITATIVA
        # ════════════════════════════════════════════════════════
        robustness_card = ui.card().classes('w-full bg-slate-900/80 border border-purple-900/50 rounded-xl p-4 shadow-xl')
        robustness_card.set_visibility(False)
        
        with robustness_card:
            with ui.row().classes('w-full justify-between items-center mb-3'):
                with ui.row().classes('items-center gap-2.5'):
                    with ui.row().classes('items-center justify-center w-8 h-8 rounded-lg bg-emerald-500/15 border border-emerald-500/30 text-emerald-400'):
                        ui.icon('verified_user', size='1.25rem')
                    with ui.column().classes('gap-0'):
                        ui.label('5. Analizador Cuantitativo de Robustez & Mesetas de Parámetros').classes('text-base font-bold text-white tracking-tight leading-tight')
                        ui.label('Identificación de la meseta más confiable, sensibilidad relativa y riesgo de sobreajuste.').classes('text-slate-400 text-xs')

            # ── Subpanel A: Tarjetas Comparativas (Meseta Robusta vs Pico Máximo) ──
            with ui.row().classes('w-full gap-4 items-stretch mb-4'):
                
                # Card 1: Configuración Más Robusta (Recomendada)
                with ui.card().classes('flex-1 bg-gradient-to-br from-emerald-950/30 via-slate-900 to-slate-900 border-2 border-emerald-500/60 rounded-xl p-4 shadow-lg flex flex-col justify-between'):
                    with ui.row().classes('w-full justify-between items-center border-b border-emerald-500/30 pb-2 mb-2'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('shield', size='1.2rem').classes('text-emerald-400')
                            ui.label('CONFIGURACIÓN MÁS ROBUSTA (MESETA ÓPTIMA)').classes('text-xs font-extrabold text-emerald-400 tracking-wider uppercase font-mono')
                        ui.badge('RECOMENDADA', color='emerald-9').classes('text-[10px] font-bold px-2 py-0.5')

                    lbl_rob_params = ui.label('--').classes('text-sm font-mono font-bold text-white mb-2 bg-slate-950/60 p-2 rounded border border-slate-800')
                    
                    with ui.row().classes('w-full grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3'):
                        with ui.column().classes('gap-0 bg-slate-950/40 p-2 rounded border border-slate-800/80'):
                            ui.label('Sharpe Ratio').classes('text-[10px] text-slate-400')
                            lbl_rob_sharpe = ui.label('--').classes('text-sm font-bold text-emerald-400 font-mono')
                        with ui.column().classes('gap-0 bg-slate-950/40 p-2 rounded border border-slate-800/80'):
                            ui.label('PnL Neto').classes('text-[10px] text-slate-400')
                            lbl_rob_pnl = ui.label('--').classes('text-sm font-bold text-white font-mono')
                        with ui.column().classes('gap-0 bg-slate-950/40 p-2 rounded border border-slate-800/80'):
                            ui.label('CAGR (%)').classes('text-[10px] text-slate-400')
                            lbl_rob_cagr = ui.label('--').classes('text-sm font-bold text-slate-200 font-mono')
                        with ui.column().classes('gap-0 bg-slate-950/40 p-2 rounded border border-slate-800/80'):
                            ui.label('Max Drawdown').classes('text-[10px] text-slate-400')
                            lbl_rob_dd = ui.label('--').classes('text-sm font-bold text-rose-400 font-mono')

                    with ui.column().classes('w-full bg-emerald-950/20 border border-emerald-800/30 p-2.5 rounded text-xs gap-1 mb-3'):
                        lbl_rob_neighborhood = ui.label('--').classes('text-slate-300')
                        lbl_rob_exp = ui.label('Esta configuración tolera desviaciones del mercado porque todos sus parámetros vecinos mantienen alta rentabilidad con baja varianza.').classes('text-[11px] text-emerald-300 italic')

                    btn_apply_robust = ui.button('✅ Aplicar Configuración Robusta a Analyzer', icon='check_circle').classes('w-full bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-2 rounded-lg text-xs')

                # Card 2: Configuración Máxima (Pico Aislado / Overfit)
                with ui.card().classes('flex-1 bg-slate-900 border border-slate-700/80 rounded-xl p-4 shadow-lg flex flex-col justify-between'):
                    with ui.row().classes('w-full justify-between items-center border-b border-slate-800 pb-2 mb-2'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('bolt', size='1.2rem').classes('text-amber-400')
                            ui.label('CONFIGURACIÓN MÁXIMA (PICO AISLADO)').classes('text-xs font-extrabold text-amber-400 tracking-wider uppercase font-mono')
                        lbl_peak_badge = ui.badge('#1 EN GRID', color='amber-9').classes('text-[10px] font-bold px-2 py-0.5')

                    lbl_peak_params = ui.label('--').classes('text-sm font-mono font-bold text-white mb-2 bg-slate-950/60 p-2 rounded border border-slate-800')
                    
                    with ui.row().classes('w-full grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3'):
                        with ui.column().classes('gap-0 bg-slate-950/40 p-2 rounded border border-slate-800/80'):
                            ui.label('Sharpe Ratio').classes('text-[10px] text-slate-400')
                            lbl_peak_sharpe = ui.label('--').classes('text-sm font-bold text-amber-400 font-mono')
                        with ui.column().classes('gap-0 bg-slate-950/40 p-2 rounded border border-slate-800/80'):
                            ui.label('PnL Neto').classes('text-[10px] text-slate-400')
                            lbl_peak_pnl = ui.label('--').classes('text-sm font-bold text-white font-mono')
                        with ui.column().classes('gap-0 bg-slate-950/40 p-2 rounded border border-slate-800/80'):
                            ui.label('CAGR (%)').classes('text-[10px] text-slate-400')
                            lbl_peak_cagr = ui.label('--').classes('text-sm font-bold text-slate-200 font-mono')
                        with ui.column().classes('gap-0 bg-slate-950/40 p-2 rounded border border-slate-800/80'):
                            ui.label('Max Drawdown').classes('text-[10px] text-slate-400')
                            lbl_peak_dd = ui.label('--').classes('text-sm font-bold text-rose-400 font-mono')

                    with ui.column().classes('w-full bg-slate-950/40 border border-slate-800 p-2.5 rounded text-xs gap-1 mb-3'):
                        lbl_peak_desc = ui.label('--').classes('text-slate-300 text-[11px]')

                    btn_apply_peak = ui.button('⚡ Aplicar Configuración Pico', icon='flash_on').classes('w-full bg-slate-800 hover:bg-slate-700 text-slate-200 font-bold py-2 rounded-lg text-xs border border-slate-700')

            # ── Subpanel B: Diagnóstico Global e Importancia de Parámetros (% ANOVA) ──
            with ui.row().classes('w-full gap-4 items-stretch mb-4'):
                
                # Health Score Card
                with ui.card().classes('w-full lg:w-1/3 bg-slate-900 border border-slate-800 rounded-xl p-4 shadow flex flex-col justify-between'):
                    ui.label('Índice Global de Robustez').classes('text-xs font-bold text-slate-400 uppercase tracking-wider font-mono')
                    
                    with ui.row().classes('items-center justify-between my-2'):
                        lbl_robustness_score = ui.label('-- / 100').classes('text-3xl font-extrabold font-mono text-emerald-400')
                        lbl_health_badge = ui.badge('--', color='emerald-9').classes('text-xs font-bold px-2.5 py-1')

                    lbl_health_desc = ui.label('--').classes('text-xs text-slate-300 leading-relaxed mb-3')
                    
                    with ui.column().classes('w-full gap-1.5 pt-2 border-t border-slate-800 text-xs font-mono'):
                        with ui.row().classes('w-full justify-between'):
                            ui.label('% Combos Rentables (PnL > 0):').classes('text-slate-400')
                            lbl_profit_rate = ui.label('--%').classes('font-bold text-white')
                        with ui.row().classes('w-full justify-between'):
                            ui.label('% Sharpe Positivo (> 0):').classes('text-slate-400')
                            lbl_sharpe_rate = ui.label('--%').classes('font-bold text-white')
                        with ui.row().classes('w-full justify-between'):
                            ui.label('Max Drawdown Promedio:').classes('text-slate-400')
                            lbl_avg_dd = ui.label('--%').classes('font-bold text-rose-400')

                # Sensitivity / Importance Chart
                with ui.card().classes('w-full lg:flex-1 bg-slate-900 border border-slate-800 rounded-xl p-4 shadow flex flex-col justify-between'):
                    with ui.row().classes('w-full justify-between items-center mb-1'):
                        ui.label('Sensibilidad e Importancia de Parámetros (% Influencia)').classes('text-xs font-bold text-slate-400 uppercase tracking-wider font-mono')
                        lbl_influential_summary = ui.label('--').classes('text-xs font-bold text-purple-400')

                    param_importance_plot = ui.plotly({}).classes('w-full h-44')

            # ── Subpanel C: Análisis Detallado de Media y Dispersión por Parámetro ──
            with ui.card().classes('w-full bg-slate-900 border border-slate-800 rounded-xl p-4 shadow'):
                with ui.row().classes('w-full justify-between items-center mb-3 flex-wrap gap-2'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('query_stats', size='1.2rem').classes('text-blue-400')
                        ui.label('Media, Desviación Estándar y Dispersión por Parámetro').classes('text-xs font-bold text-slate-200 uppercase tracking-wider font-mono')
                    
                    with ui.row().classes('items-center gap-3'):
                        ui.label('Seleccionar Parámetro:').classes('text-xs text-slate-400')
                        param_selector = ui.select(['--'], value='--', on_change=lambda e: _update_param_stats_ui(e.value)).classes('w-44')

                # Tabla de Estadísticas del Parámetro Seleccionado
                stats_table_cols = [
                    {'name': 'val',           'label': 'Valor del Parámetro', 'field': 'val',           'sortable': True,  'align': 'center'},
                    {'name': 'count',         'label': 'Simulaciones',         'field': 'count',         'sortable': True,  'align': 'center'},
                    {'name': 'pnl_mean',      'label': 'PnL Medio (μ)',        'field': 'pnl_mean',      'sortable': True,  'align': 'right'},
                    {'name': 'pnl_std',       'label': 'Desv. Estándar (σ)',   'field': 'pnl_std',       'sortable': True,  'align': 'right'},
                    {'name': 'pnl_cv',        'label': 'Coef. Dispersión (CV)','field': 'pnl_cv',        'sortable': True,  'align': 'right'},
                    {'name': 'sharpe_mean',   'label': 'Sharpe Medio',         'field': 'sharpe_mean',   'sortable': True,  'align': 'right'},
                    {'name': 'dd_mean',       'label': 'DD Medio (%)',         'field': 'dd_mean',       'sortable': True,  'align': 'right'},
                    {'name': 'profitable_pct','label': '% Rentable',          'field': 'profitable_pct','sortable': True,  'align': 'right'},
                ]
                
                param_stats_table = ui.table(columns=stats_table_cols, rows=[], row_key='val').props('dense flat').classes('w-full text-xs mb-4')
                
                param_stats_table.add_slot('body-cell-pnl_mean', '''
                    <q-td :props="props">
                        <span :style="{color: props.value >= 0 ? '#4ade80' : '#f87171', fontWeight: 'bold'}">
                            {{ props.value >= 0 ? '+' : '' }}{{ Number(props.value).toLocaleString() }}
                        </span>
                    </q-td>
                ''')

                param_stats_table.add_slot('body-cell-sharpe_mean', '''
                    <q-td :props="props">
                        <span :style="{color: props.value >= 1 ? '#4ade80' : props.value >= 0 ? '#fbbf24' : '#f87171', fontWeight: 'bold'}">
                            {{ props.value }}
                        </span>
                    </q-td>
                ''')

                # Gráficos Plotly: Curva de Sensibilidad con Banda ±1σ y Box Plot
                with ui.row().classes('w-full gap-4 items-stretch'):
                    with ui.column().classes('w-full lg:flex-1 gap-1'):
                        ui.label('Curva de Tendencia y Banda de Dispersión (Media ± 1 Desv. Estándar)').classes('text-[11px] font-bold text-slate-400 font-mono')
                        sensitivity_band_plot = ui.plotly({}).classes('w-full h-64')

                    with ui.column().classes('w-full lg:flex-1 gap-1'):
                        ui.label('Distribución y Cuartiles de PnL por Valor (Diagrama de Caja / Box Plot)').classes('text-[11px] font-bold text-slate-400 font-mono')
                        box_distribution_plot = ui.plotly({}).classes('w-full h-64')

                # ── Panel de Interpretación y Guía Cuantitativa ──
                with ui.card().classes('w-full bg-slate-950/70 border border-slate-800/80 rounded-xl p-3.5 mt-3'):
                    with ui.row().classes('w-full items-center gap-2 mb-2 pb-1.5 border-b border-slate-800'):
                        ui.icon('lightbulb', size='1.2rem').classes('text-amber-400')
                        ui.label('Interpretación Cuantitativa de los Gráficos').classes('text-xs font-bold text-amber-300 font-mono uppercase tracking-wider')

                    with ui.row().classes('w-full grid grid-cols-1 md:grid-cols-3 gap-3 text-xs'):
                        # Guía 1: Por qué el parámetro es influyente
                        with ui.column().classes('gap-1 bg-slate-900/60 p-2.5 rounded border border-slate-800'):
                            ui.label('💡 ¿Por qué un Parámetro Domina la Influencia?').classes('font-bold text-purple-400')
                            lbl_param_insight = ui.label(
                                'El Stop Loss (SL) suele ser el más influyente porque determina la asimetría de pérdidas y si la posición es sacada prematuramente por ruido de mercado.'
                            ).classes('text-[11px] text-slate-300 leading-relaxed')

                        # Guía 2: Curva de Tendencia y Banda
                        with ui.column().classes('gap-1 bg-slate-900/60 p-2.5 rounded border border-slate-800'):
                            ui.label('📈 Banda de Dispersión (μ ± 1σ)').classes('font-bold text-blue-400')
                            ui.label(
                                '• Línea Central (μ): Rendimiento promedio esperado al fijar este parámetro.\n'
                                '• Banda Sombreada (±1σ): Mide la incertidumbre. Una banda estrecha indica alta consistencia y bajo riesgo de sobreajuste.'
                            ).classes('text-[11px] text-slate-300 leading-relaxed')

                        # Guía 3: Diagrama de Caja
                        with ui.column().classes('gap-1 bg-slate-900/60 p-2.5 rounded border border-slate-800'):
                            ui.label('📦 Diagrama de Caja (Box Plot)').classes('font-bold text-emerald-400')
                            ui.label(
                                '• Caja: El 50% central de todos los resultados (Rango Intercuartílico).\n'
                                '• Línea interna: Mediana real.\n'
                                '• Cuanto más elevada y compacta esté la caja sobre el eje positivo, más confiable es ese valor.'
                            ).classes('text-[11px] text-slate-300 leading-relaxed')

            def _update_param_stats_ui(selected_p):
                rob = state.get('robustness_data')
                if not rob or not selected_p or selected_p not in rob.get('param_stats', {}):
                    return

                stats_list = rob['param_stats'][selected_p]
                
                # Actualizar Tabla
                param_stats_table.rows = [
                    {
                        'val': s['val'],
                        'count': s['count'],
                        'pnl_mean': s['pnl_mean'],
                        'pnl_std': s['pnl_std'],
                        'pnl_cv': s['pnl_cv'],
                        'sharpe_mean': s['sharpe_mean'],
                        'dd_mean': f"-{s['dd_mean']:.2f}%",
                        'profitable_pct': f"{s['profitable_pct']:.1f}%",
                    }
                    for s in stats_list
                ]
                param_stats_table.update()

                # Actualizar Explicación Contextual según el parámetro
                p_upper = str(selected_p).upper()
                imp_pct = dict(rob.get('param_importance_pct', [])).get(selected_p, 0)
                if 'SL' in p_upper or 'STOP' in p_upper:
                    lbl_param_insight.set_text(
                        f"El parámetro '{selected_p}' ({imp_pct}% de influencia) controla el tamaño del riesgo por trade y la tasa de whipsaws. "
                        "Un SL demasiado ajustado genera salidas prematuras por volatilidad, mientras que un SL adecuado permite capturar la tendencia completa."
                    )
                elif 'TP' in p_upper or 'PROFIT' in p_upper:
                    lbl_param_insight.set_text(
                        f"El parámetro '{selected_p}' ({imp_pct}% de influencia) determina el ratio Riesgo/Beneficio y cuándo asegurar ganancias. "
                        "Afecta directamente la esperanza matemática de cada operación ganadora."
                    )
                elif 'FAST' in p_upper or 'RAPIDA' in p_upper or 'LOW' in p_upper or 'LENTA' in p_upper or 'PERIOD' in p_upper:
                    lbl_param_insight.set_text(
                        f"El parámetro '{selected_p}' ({imp_pct}% de influencia) define la velocidad de reacción a las tendencias del mercado. "
                        "Valores más bajos aumentan las operaciones pero capturan más ruido; valores más altos filtran mejor pero aumentan el rezago."
                    )
                else:
                    lbl_param_insight.set_text(
                        f"El parámetro '{selected_p}' explica el {imp_pct}% de la varianza total del rendimiento. "
                        "Los valores con mayor PnL medio y menor dispersión (CV bajo) representan las opciones más confiables."
                    )

                # Gráfico 1: Curva de Sensibilidad con Banda de Dispersión
                x_vals = [s['val'] for s in stats_list]
                y_means = [s['pnl_mean'] for s in stats_list]
                y_stds = [s['pnl_std'] for s in stats_list]
                y_upper = [m + s for m, s in zip(y_means, y_stds)]
                y_lower = [m - s for m, s in zip(y_means, y_stds)]

                fig_band = go.Figure()
                fig_band.add_trace(go.Scatter(
                    x=x_vals + x_vals[::-1],
                    y=y_upper + y_lower[::-1],
                    fill='toself',
                    fillcolor='rgba(168, 85, 247, 0.15)',
                    line=dict(color='rgba(255,255,255,0)'),
                    hoverinfo="skip",
                    showlegend=True,
                    name='Banda ±1σ (Dispersión)'
                ))
                fig_band.add_trace(go.Scatter(
                    x=x_vals,
                    y=y_means,
                    mode='lines+markers',
                    line=dict(color='#a855f7', width=3),
                    marker=dict(size=8, color='#f59e0b'),
                    name=f'PnL Medio (μ) vs {selected_p}'
                ))
                fig_band.update_layout(
                    template='plotly_dark',
                    paper_bgcolor='#0a0e17',
                    plot_bgcolor='#111827',
                    margin=dict(l=40, r=20, t=25, b=25),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    xaxis=dict(gridcolor='#1e293b', title=f'Valor del Parámetro {selected_p}'),
                    yaxis=dict(gridcolor='#1e293b', title='PnL Medio')
                )
                sensitivity_band_plot.update_figure(fig_band)

                # Gráfico 2: Box Plot de PnL
                fig_box = go.Figure()
                for s in stats_list:
                    fig_box.add_trace(go.Box(
                        y=s['raw_pnl_list'],
                        name=str(s['val']),
                        boxpoints='outliers',
                        marker_color='#10b981',
                        line=dict(width=1.5)
                    ))
                fig_box.update_layout(
                    template='plotly_dark',
                    paper_bgcolor='#0a0e17',
                    plot_bgcolor='#111827',
                    margin=dict(l=40, r=20, t=25, b=25),
                    showlegend=False,
                    xaxis=dict(gridcolor='#1e293b', title=f'Valor de {selected_p}'),
                    yaxis=dict(gridcolor='#1e293b', title='Distribución de PnL')
                )
                box_distribution_plot.update_figure(fig_box)

        # ════════════════════════════════════════════════════════
        # FUNCIÓN PRINCIPAL DE EJECUCIÓN DEL OPTIMIZADOR
        # ════════════════════════════════════════════════════════
        async def _run_optimizer():
            if not state['strategy_name']:
                ui.notify('No hay estrategia seleccionada', type='warning')
                return
            if not opt_ranges:
                ui.notify('No hay parámetros de rango definidos para optimizar', type='warning')
                return

            file_path = strategies.get(state['strategy_name'])
            if not file_path:
                ui.notify('No se encontró el archivo de estrategia', type='warning')
                return

            total_combos = count_combinations(opt_ranges)
            if total_combos > 5000:
                ui.notify(
                    f'El grid tiene {total_combos:,} combinaciones. Se recomienda ajustar los pasos para optimizar el tiempo.',
                    type='warning', timeout=5000
                )

            btn_run_opt.set_text(f'⏳ Optimizando... (0/{total_combos})')
            btn_run_opt.props('disabled')
            opt_progress.value = 0
            lbl_progress.set_text(f'Iniciando simulación de {total_combos:,} combinaciones...')
            opt_result_table.rows = []
            opt_result_table.update()
            robustness_card.set_visibility(False)
            chart_card.set_visibility(False)

            # 1. Cargar datos de mercado
            try:
                start_dt = datetime.strptime(state['start_date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
                end_dt = datetime.strptime(state['end_date'], '%Y-%m-%d').replace(hour=23, minute=59, tzinfo=timezone.utc)
                db = SessionLocal()
                market_mgr = MarketDataManager(db)
                df_opt = market_mgr.get_data(state['symbol'], state['timeframe'], start_dt, end_dt)
                if df_opt.empty:
                    market_mgr.update_historical_data(state['symbol'], state['timeframe'], start_dt, end_dt)
                    df_opt = market_mgr.get_data(state['symbol'], state['timeframe'], start_dt, end_dt)
                db.close()
            except Exception as ex:
                ui.notify(f'Error cargando datos: {ex}', type='negative')
                btn_run_opt.set_text('🚀 INICIAR OPTIMIZADOR (GRID SEARCH)')
                btn_run_opt.props(remove='disabled')
                return

            if df_opt.empty:
                ui.notify('No hay datos históricos disponibles para el activo/período seleccionado.', type='warning')
                btn_run_opt.set_text('🚀 INICIAR OPTIMIZADOR (GRID SEARCH)')
                btn_run_opt.props(remove='disabled')
                return

            # 2. Capital y sizing
            start_price = df_opt.iloc[0]['open'] if not df_opt.empty else 1.0
            cap_val = float(state.get('capital', 1.0) or 1.0)
            if state.get('capital_type', 'QUOTE') == 'BASE':
                initial_cap_quote = cap_val * start_price
            else:
                initial_cap_quote = cap_val

            # Sizing config
            sizing_mode = state.get('sizing_mode', 'Interés Compuesto (100% Capital)')
            if 'Riesgo Fijo' in str(sizing_mode):
                sizing_cfg = {'method': 'fixed_fractional', 'risk_per_trade_pct': 1.0}
            elif 'Monto Fijo' in str(sizing_mode):
                raw_fixed = float(state.get('fixed_amount', cap_val) or cap_val)
                fixed_val = raw_fixed * start_price if state.get('capital_type', 'QUOTE') == 'BASE' else raw_fixed
                sizing_cfg = {'method': 'fixed_amount', 'value': fixed_val}
            else:
                sizing_cfg = {'method': 'compounding', 'value': 100.0}

            # Equity curve filter config
            ec_cfg = {
                'enabled': bool(state.get('ec_enabled', False) or state.get('cl_enabled', False)),
                'dd_enabled': bool(state.get('ec_enabled', False)),
                'start_trading_at_dd_pct': float(state.get('ec_start_dd', 30.0)),
                'stop_trading_at_dd_pct': float(state.get('ec_stop_dd', 0.0)),
                'cl_enabled': bool(state.get('cl_enabled', False)),
                'cl_start': int(state.get('cl_start', 3)),
                'cl_stop': float(state.get('cl_stop', 0.0))
            }

            comm_pct = float(state.get('commission_pct', 0.1) or 0.1)
            slip_pct = float(state.get('slippage_pct', 0.05) or 0.05)
            metric_key = state.get('optimize_metric', 'sharpe_ratio')

            done_counter = [0]
            def _progress_cb(done, total):
                done_counter[0] = done

            import copy
            param_ranges_copy = copy.deepcopy(opt_ranges)

            # Ejecutar de forma asíncrona segura sin bloquear NiceGUI
            opt_task = asyncio.create_task(
                run.io_bound(
                    lambda: run_grid_search(
                        file_path,
                        df_opt,
                        initial_cap_quote,
                        param_ranges_copy,
                        metric_key,
                        _progress_cb,
                        comm_pct,
                        slip_pct,
                        ec_cfg,
                        sizing_cfg
                    )
                )
            )

            # Loop de actualización en el hilo principal de NiceGUI (100% seguro)
            while not opt_task.done():
                current_done = done_counter[0]
                pct = current_done / total_combos if total_combos > 0 else 0
                opt_progress.value = pct
                lbl_progress.set_text(f'{current_done} / {total_combos} combinaciones probadas ({pct*100:.1f}%)')
                btn_run_opt.set_text(f'⏳ Optimizando... ({current_done}/{total_combos})')
                await asyncio.sleep(0.2)

            try:
                results = await opt_task
            except Exception as task_ex:
                ui.notify(f"Error durante la optimización: {task_ex}", type='negative')
                btn_run_opt.set_text('🚀 INICIAR OPTIMIZADOR (GRID SEARCH)')
                btn_run_opt.props(remove='disabled')
                return

            state['last_results'] = results

            # Formatear y mostrar resultados
            rows = []
            for i, r in enumerate(results, 1):
                param_str = ' | '.join(f"{k}={v}" for k, v in r['params'].items())
                rows.append({
                    'rank': i,
                    'params': param_str,
                    'raw_params': r['params'],
                    'sharpe': r['sharpe_ratio'],
                    'cagr': r['cagr'],
                    'maxdd': f"{r['max_drawdown_pct']:.2f}%",
                    'profit_factor': r.get('profit_factor', 0),
                    'trades': r['total_trades'],
                    'win_rate': f"{r.get('percent_profitable', 0):.2f}%",
                    'cons_losses': r.get('max_consecutive_losers', 0),
                    'final_eq': f"{r.get('final_equity', 0):,.2f}",
                    'pnl': r['net_pnl'],
                    'equity_curve': r.get('equity_curve', [])
                })

            opt_result_table.rows = rows
            opt_result_table.update()
            opt_progress.value = 1.0
            lbl_progress.set_text(f'{len(rows)} combinaciones completadas')
            lbl_res_info.set_text(f"Total: {len(rows)} configuraciones evaluadas con éxito")
            btn_run_opt.set_text('🚀 INICIAR OPTIMIZADOR (GRID SEARCH)')
            btn_run_opt.props(remove='disabled')

            # Renderizar gráfico Plotly para el Top 5
            top_5 = [r for r in results[:5] if r.get('equity_curve')]
            if top_5:
                fig = go.Figure()
                colors = ['#f59e0b', '#10b981', '#3b82f6', '#ec4899', '#8b5cf6']
                for idx, t in enumerate(top_5):
                    p_label = f"#{idx+1}: " + ' '.join(f"{k}={v}" for k, v in t['params'].items())
                    eq_data = t.get('equity_curve', [])
                    fig.add_trace(go.Scatter(
                        y=eq_data,
                        mode='lines',
                        name=p_label,
                        line=dict(color=colors[idx % len(colors)], width=2.5 if idx == 0 else 1.5)
                    ))
                fig.update_layout(
                    template='plotly_dark',
                    paper_bgcolor='#0a0e17',
                    plot_bgcolor='#111827',
                    margin=dict(l=40, r=20, t=30, b=30),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    xaxis=dict(gridcolor='#1e293b', title='Progreso de Simulación'),
                    yaxis=dict(gridcolor='#1e293b', title='Capital de la Cuenta')
                )
                top_chart_plot.update_figure(fig)
                chart_card.set_visibility(True)

            # ─────────────────────────────────────────────────────────────
            # EJECUTAR Y RENDERIZAR ANÁLISIS DE ROBUSTEZ Y MESETA
            # ─────────────────────────────────────────────────────────────
            try:
                rob = analyze_robustness(results, param_ranges_copy, metric_key)
                if rob.get('status') == 'success':
                    state['robustness_data'] = rob
                    
                    # 1. Rellenar Health Score
                    lbl_robustness_score.set_text(f"{rob['global_score']} / 100")
                    lbl_health_badge.set_text(rob['health_status'])
                    lbl_health_desc.set_text(rob['health_desc'])
                    lbl_profit_rate.set_text(f"{rob['profit_rate']}%")
                    lbl_sharpe_rate.set_text(f"{rob['sharpe_positive_rate']}%")
                    lbl_avg_dd.set_text(f"-{rob['mean_dd']}%")

                    # 2. Rellenar Meseta Robusta (Recomendada)
                    best_r = rob['best_robust']
                    rob_param_str = ' | '.join(f"{k}={v}" for k, v in best_r['params'].items())
                    lbl_rob_params.set_text(rob_param_str)
                    lbl_rob_sharpe.set_text(str(best_r['self_sharpe']))
                    lbl_rob_pnl.set_text(f"+{best_r['self_pnl']:,.2f}" if best_r['self_pnl'] >= 0 else f"{best_r['self_pnl']:,.2f}")
                    lbl_rob_cagr.set_text(f"{best_r['self_cagr']:+.2f}%")
                    lbl_rob_dd.set_text(f"{best_r['self_dd']:.2f}%")
                    lbl_rob_neighborhood.set_text(
                        f"📊 Vecindario ({best_r['neighbors_count']} combos): Sharpe Medio = {best_r['neigh_metric_mean']:.3f} (±{best_r['neigh_metric_std']:.3f}) | PnL Medio = {best_r['neigh_pnl_mean']:+,.2f}"
                    )
                    btn_apply_robust.on('click', lambda: _on_apply_params({'args': {'raw_params': best_r['params']}}))

                    # 3. Rellenar Configuración Pico
                    best_p = rob['best_peak']
                    peak_param_str = ' | '.join(f"{k}={v}" for k, v in best_p['params'].items())
                    lbl_peak_params.set_text(peak_param_str)
                    lbl_peak_sharpe.set_text(str(best_p['sharpe']))
                    lbl_peak_pnl.set_text(f"+{best_p['pnl']:,.2f}" if best_p['pnl'] >= 0 else f"{best_p['pnl']:,.2f}")
                    lbl_peak_cagr.set_text(f"{best_p['cagr']:+.2f}%")
                    lbl_peak_dd.set_text(f"{best_p['dd']:.2f}%")
                    
                    if rob['is_same_as_peak']:
                        lbl_peak_desc.set_text("✅ La configuración pico coincide exactamente con el centro de la meseta más robusta. Excelente estabilidad.")
                    else:
                        lbl_peak_desc.set_text("⚠️ Es el pico con mayor métrica aislada, pero sus vecinos sufren mayor degradación. Mayor riesgo de sobreajuste.")
                    btn_apply_peak.on('click', lambda: _on_apply_params({'args': {'raw_params': best_p['params']}}))

                    # 4. Gráfico de Importancia de Parámetros (% ANOVA)
                    imp_data = rob['param_importance_pct']
                    imp_keys = [x[0] for x in imp_data][::-1]
                    imp_vals = [x[1] for x in imp_data][::-1]

                    fig_imp = go.Figure(go.Bar(
                        x=imp_vals,
                        y=imp_keys,
                        orientation='h',
                        marker=dict(
                            color=imp_vals,
                            colorscale='Viridis',
                            showscale=False
                        ),
                        text=[f"{v}%" for v in imp_vals],
                        textposition='auto',
                        textfont=dict(color='white', size=11)
                    ))
                    fig_imp.update_layout(
                        template='plotly_dark',
                        paper_bgcolor='#0a0e17',
                        plot_bgcolor='#111827',
                        margin=dict(l=60, r=20, t=10, b=20),
                        xaxis=dict(gridcolor='#1e293b', title='% Influencia sobre Rendimiento', range=[0, max(100, max(imp_vals)+10)]),
                        yaxis=dict(gridcolor='#1e293b')
                    )
                    param_importance_plot.update_figure(fig_imp)
                    lbl_influential_summary.set_text(
                        f"Parámetro Más Influyente: {rob['most_influential_param']} ({rob['most_influential_pct']}%) | Menos Influyente: {rob['least_influential_param']} ({rob['least_influential_pct']}%)"
                    )

                    # 5. Selector y Gráficos de Parámetros
                    p_keys = list(rob['param_stats'].keys())
                    param_selector.options = p_keys
                    param_selector.value = p_keys[0] if p_keys else ''
                    param_selector.update()
                    if p_keys:
                        _update_param_stats_ui(p_keys[0])

                    robustness_card.set_visibility(True)

            except Exception as rob_ex:
                ui.notify(f"Aviso de robustez: {rob_ex}", type='warning')

            ui.notify(f"✅ Optimización y Análisis de Robustez finalizados. {len(results)} combinaciones evaluadas.", type='positive', timeout=4.0)

    return {
        'state': state,
        'refresh_strategies': _build_ranges_ui
    }
