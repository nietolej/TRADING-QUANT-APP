import json
import os
from datetime import datetime
from nicegui import run, ui
from data_layer.storage import SessionLocal, BacktestRun
from .strategy_analyzer_page import _sync_run_portfolio_backtest

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
        # 2. SIMULADOR DE PORTAFOLIO Y COMBINACIÓN DE ESTRATEGIAS (INTEGRADO EN PÁGINA)
        # ════════════════════════════════════════════════════════
        with ui.card().classes('w-full bg-slate-900 text-white p-6 rounded-2xl shadow-xl mb-4') as portfolio_section_anchor:
            with ui.row().classes('items-center gap-4 w-full'):
                ui.icon('pie_chart', size='2.5rem').classes('text-emerald-400')
                with ui.column().classes('gap-0'):
                    ui.label('Simulador de Portafolio & Combinación de Estrategias').classes('text-2xl font-bold')
                    ui.label('Asigna porcentajes de capital a múltiples estrategias del historial para evaluar el rendimiento consolidado.').classes('text-xs text-slate-300')

        # Configuración Global del Portafolio
        with ui.card().classes('w-full p-4 mb-4 rounded-2xl border border-slate-700/50 shadow-xl bg-slate-900/50'):
            ui.label('Configuración Global del Portafolio').classes('font-bold text-slate-200 mb-2')
            with ui.row().classes('w-full gap-4 items-center'):
                port_capital_input = ui.number('Capital Total Inicial ($)', value=10000.0, step=500.0, min=10.0).classes('flex-1')
                port_start_input = ui.input('Fecha Inicio', value='2020-01-01').classes('flex-1')
                port_end_input = ui.input('Fecha Fin', value=datetime.now().strftime('%Y-%m-%d')).classes('flex-1')
                port_comm_input = ui.number('Comisión (%)', value=0.1, step=0.01).classes('w-32')
                port_slip_input = ui.number('Slippage (%)', value=0.05, step=0.01).classes('w-32')

        # Lista de Estrategias en Portafolio
        portfolio_state = {
            'items': [
                {'strategy': list(strategies.keys())[0] if strategies else '', 'symbol': 'BNB/BTC', 'timeframe': '1d', 'weight_pct': 50.0},
                {'strategy': list(strategies.keys())[0] if strategies else '', 'symbol': 'BTC/USDT', 'timeframe': '1d', 'weight_pct': 50.0}
            ]
        }

        port_items_container = ui.column().classes('w-full gap-3 mb-4')

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

        def refresh_portfolio_items_ui():
            port_items_container.clear()
            saved_opts, saved_map = fetch_saved_runs()

            with port_items_container:
                for i, item in enumerate(portfolio_state['items']):
                    # Resolve valid strategy key
                    item['strategy'] = resolve_strategy_key(item['strategy'], strategies)

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
                                            
                                            # Resolve strategy name/file
                                            strat_key = resolve_strategy_key(run_obj.strategy_name, strategies, run_obj.config_snapshot)
                                            portfolio_state['items'][idx]['strategy'] = strat_key
                                            
                                            if run_obj.symbol:
                                                portfolio_state['items'][idx]['symbol'] = run_obj.symbol
                                            if run_obj.timeframe:
                                                portfolio_state['items'][idx]['timeframe'] = run_obj.timeframe
                                            
                                            if run_obj.config_snapshot:
                                                try:
                                                    cfg = json.loads(run_obj.config_snapshot)
                                                    if 'custom_parameters' in cfg and cfg['custom_parameters']:
                                                        portfolio_state['items'][idx]['custom_params'] = cfg['custom_parameters'].copy()
                                                except Exception:
                                                    pass
                                                    
                                            if not portfolio_state['items'][idx].get('custom_params'):
                                                portfolio_state['items'][idx]['custom_params'] = get_strategy_default_params(strat_key).copy()

                                            if run_obj.start_date:
                                                port_start_input.value = run_obj.start_date.strftime("%Y-%m-%d")
                                            if run_obj.end_date:
                                                port_end_input.value = run_obj.end_date.strftime("%Y-%m-%d")

                                            refresh_portfolio_items_ui()
                                            ui.notify(f"✅ Estrategia #{idx+1} cargada con parámetros desde Historial #{run_id}", type="positive")
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

        def rebalance_equal_weights():
            n = len(portfolio_state['items'])
            if n > 0:
                w = round(100.0 / n, 1)
                for it in portfolio_state['items']:
                    it['weight_pct'] = w
                refresh_portfolio_items_ui()

        def add_portfolio_item():
            if len(portfolio_state['items']) >= 10:
                ui.notify("Máximo 10 estrategias por portafolio", type="warning")
                return
            default_strat = list(strategies.keys())[0] if strategies else ''
            portfolio_state['items'].append({
                'strategy': default_strat,
                'symbol': 'BTC/USDT',
                'timeframe': '1d',
                'weight_pct': 0.0,
                'custom_params': get_strategy_default_params(default_strat).copy()
            })
            rebalance_equal_weights()

        with ui.row().classes('w-full gap-3 mb-6'):
            ui.button('➕ Agregar Estrategia al Portafolio', icon='add', on_click=add_portfolio_item).props('outlined color=primary')
            ui.button('⚖️ Distribuir Capital Equitativamente', icon='balance', on_click=rebalance_equal_weights).props('flat color=secondary')

        btn_run_portfolio = ui.button('⚡ EJECUTAR SIMULACIÓN DE PORTAFOLIO COMBINADO', icon='bolt').props('rounded').classes('w-full bg-blue-700 hover:bg-blue-600 text-white font-black text-lg py-3 shadow-xl mb-6')

        port_results_container = ui.column().classes('w-full')

        async def run_portfolio_simulation():
            total_cap = float(port_capital_input.value or 10000.0)
            start_str = port_start_input.value or '2024-01-01'
            end_str = port_end_input.value or datetime.now().strftime('%Y-%m-%d')
            comm_pct = float(port_comm_input.value or 0.1) / 100.0
            slip_pct = float(port_slip_input.value or 0.05) / 100.0

            total_w = sum(float(it.get('weight_pct', 0.0)) for it in portfolio_state['items'])
            if abs(total_w - 100.0) > 1.0:
                ui.notify(f"La suma de los pesos es {total_w:.1f}%. Debe ser cercana al 100%.", type="warning")

            prep_items = []
            for it in portfolio_state['items']:
                s_name = it['strategy']
                s_path = strategies.get(s_name)
                if s_path:
                    prep_items.append({
                        'strategy_path': s_path,
                        'symbol': it['symbol'],
                        'timeframe': it['timeframe'],
                        'weight_pct': float(it['weight_pct']),
                        'custom_params': it.get('custom_params', {})
                    })

            if not prep_items:
                ui.notify("No hay estrategias válidas para simular", type="warning")
                return

            p_client = ui.context.client
            btn_run_portfolio.text = "⏳ Calculando Portafolio..."
            btn_run_portfolio.disable()
            
            try:
                from datetime import timezone
                s_dt = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                e_dt = datetime.strptime(end_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
            except Exception:
                ui.notify("Formato de fecha inválido. Usar AAAA-MM-DD", type="negative")
                btn_run_portfolio.enable()
                btn_run_portfolio.text = "⚡ EJECUTAR SIMULACIÓN DE PORTAFOLIO COMBINADO"
                return

            try:
                res = await run.io_bound(_sync_run_portfolio_backtest, prep_items, total_cap, s_dt, e_dt, comm_pct, slip_pct)

                with p_client:
                    if 'error' in res:
                        ui.notify(res['error'], type="negative")
                        return

                    port_results_container.clear()
                    with port_results_container:
                        ui.label('Resultados del Portafolio Combinado').classes('text-xl font-black text-slate-200 mt-2 mb-3')
                        
                        # 1. Metric Cards
                        with ui.row().classes('w-full gap-4 mb-4'):
                            with ui.card().classes('flex-1 bg-slate-900/50 p-4 rounded-xl border border-slate-700/50 shadow-lg'):
                                ui.label('CAGR del Portafolio').classes('text-xs text-slate-500 uppercase font-bold')
                                ui.label(f"{res['cagr']:.2f}%").classes(f"text-2xl font-black {'text-emerald-600' if res['cagr'] >= 0 else 'text-red-600'}")

                            with ui.card().classes('flex-1 bg-slate-900/50 p-4 rounded-xl border border-slate-700/50 shadow-lg'):
                                ui.label('Max Drawdown Combinado').classes('text-xs text-slate-500 uppercase font-bold')
                                ui.label(f"-{res['max_drawdown_pct']:.2f}%").classes('text-2xl font-black text-red-600')

                            with ui.card().classes('flex-1 bg-slate-900/50 p-4 rounded-xl border border-slate-700/50 shadow-lg'):
                                ui.label('Profit Factor (PF)').classes('text-xs text-slate-500 uppercase font-bold')
                                ui.label(f"{res['profit_factor']:.2f}").classes(f"text-2xl font-black {'text-purple-600' if res['profit_factor'] >= 1 else 'text-slate-300'}")

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
                                <q-badge :color="props.row.side === 'LONG' ? 'positive' : 'negative'" :label="props.row.side" />
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
                    btn_run_portfolio.text = "⚡ EJECUTAR SIMULACIÓN DE PORTAFOLIO COMBINADO"

        btn_run_portfolio.on_click(run_portfolio_simulation)

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

                    start_str = run.start_date.strftime("%Y-%m-%d") if run.start_date else ""
                    end_str = run.end_date.strftime("%Y-%m-%d") if run.end_date else ""
                    date_range_str = f"{start_str} -> {end_str}" if start_str else "N/A"

                    rows.append({
                        'run_id': run.run_id,
                        'created_at': run.created_at.strftime("%Y-%m-%d %H:%M") if run.created_at else "N/A",
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
                        'start_date': start_str or "2024-01-01",
                        'end_date': end_str or datetime.now().strftime("%Y-%m-%d")
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

        def add_row_to_portfolio(row):
            raw_strat = row.get('strategy_name')
            cfg_snap = row.get('config_snapshot')
            strat_key = resolve_strategy_key(raw_strat, strategies, cfg_snap)

            sym = row.get('symbol') or 'BTC/USDT'
            tf = row.get('timeframe') or '1d'
            custom_p = {}
            if cfg_snap:
                try:
                    cfg = json.loads(cfg_snap) if isinstance(cfg_snap, str) else cfg_snap
                    if 'custom_parameters' in cfg and cfg['custom_parameters']:
                        custom_p = cfg['custom_parameters'].copy()
                except Exception:
                    pass

            if not custom_p:
                custom_p = get_strategy_default_params(strat_key).copy()

            if row.get('start_date'):
                port_start_input.value = row['start_date']
            if row.get('end_date'):
                port_end_input.value = row['end_date']

            new_item = {
                'strategy': strat_key,
                'symbol': sym,
                'timeframe': tf,
                'weight_pct': 0.0,
                'custom_params': custom_p,
                'saved_run_id': row.get('run_id')
            }

            portfolio_state['items'].append(new_item)
            rebalance_equal_weights()
            refresh_portfolio_items_ui()

            ui.notify(f"✅ Estrategia '{strat_key}' ({sym} {tf}) añadida con todos sus parámetros al Portafolio", type="positive")

            # Desplazar suavemente a la sección de portafolio
            ui.run_javascript('window.scrollTo({top: document.body.scrollHeight, behavior: "smooth"})')

        def on_open_in_portfolio(e):
            row = e.args
            add_row_to_portfolio(row)

        def handle_top_open_portfolio():
            ui.run_javascript('window.scrollTo({top: document.body.scrollHeight, behavior: "smooth"})')

        btn_top_portfolio.on('click', handle_top_open_portfolio)

        history_table.on('load_to_analyzer', on_load_to_analyzer)
        history_table.on('open_in_portfolio', on_open_in_portfolio)
        history_table.on('delete_run', on_delete_run)
        btn_refresh.on('click', load_data)

        # Carga inicial
        load_data()
