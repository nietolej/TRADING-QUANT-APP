from nicegui import ui
import yaml
import os
import glob
import time

# Ruta absoluta a la raíz del proyecto (2 niveles arriba de este archivo)
_PAGE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(_PAGE_DIR, '..', '..'))
STRATEGIES_DIR = os.path.join(BASE_DIR, 'config', 'strategies')

# Descripciones didácticas de cada tipo de Take Profit y Stop Loss
TP_EXPLANATIONS = {
    'fixed': '🎯 Take Profit Fijo (%): Cierra la posición cuando el precio alcanza un porcentaje fijo de ganancia desde el precio de entrada.',
    'risk_reward': '⚖️ Ratio Riesgo/Beneficio (R:R): Calcula el objetivo multiplicando la distancia del Stop Loss (ej. 2.0R = busca ganar 2 veces lo que arriesga).',
    'atr': '📊 Objetivo por Volatilidad ATR: Proyecta el beneficio a M múltiplos del Average True Range (ATR), adaptándose al rango del mercado.',
    'partial': '🪜 Salida Parcial / Escalonada: Toma beneficios tempranos en el primer objetivo (TP1) y permite dejar correr el resto.'
}

SL_EXPLANATIONS = {
    'fixed': '🛡️ Stop Loss Fijo (%): Cierra la operación si el precio retrocede un porcentaje exacto desde la entrada.',
    'trailing_percent': '📈 Trailing Stop (%): Sigue el punto más favorable alcanzado. Si el precio retrocede un X% desde el pico máximo, cierra la operación asegurando ganancias.',
    'break_even': '🔒 Break-Even + Trailing: Mueve el Stop Loss al precio de entrada (riesgo cero) al superar cierto % de ganancia, protegiendo el capital.',
    'atr': '📊 Stop por Volatilidad ATR: Sitúa el Stop a K múltiplos de ATR de la entrada, dando más margen en alta volatilidad y menos en baja.',
    'chandelier': '🕯️ Chandelier Exit (Trailing ATR): Trailing Stop cuantitativo que cuelga del máximo más alto de las últimas N velas menos (K * ATR).',
    'swing': '📉 Swing Low / High: Coloca el Stop Loss en el soporte o resistencia más reciente (mínimo/máximo de las últimas N velas).'
}


def render_strategy_builder():
    with ui.column().classes('w-full q-pa-sm'):
        
        # 1. Header estilo "Hero" (Compacto y Optimizado)
        with ui.card().classes('w-full bg-slate-900 text-white rounded-xl shadow border border-slate-800 px-4 py-2.5 mb-3'):
            with ui.row().classes('w-full justify-between items-center'):
                with ui.row().classes('items-center gap-2.5'):
                    with ui.row().classes('items-center justify-center w-8 h-8 rounded-lg bg-blue-500/15 border border-blue-500/30 text-blue-400'):
                        ui.icon('query_stats', size='1.25rem')
                    with ui.column().classes('gap-0'):
                        ui.label('Strategy Builder').classes('text-base font-bold tracking-tight text-white leading-tight')
                        ui.label('Diseña, parametriza y guarda tus estrategias de trading cuantitativo.').classes('text-slate-400 text-[11px] leading-tight')
                
                ui.button('Catálogo de Estrategias', on_click=lambda: load_catalog(), icon='view_list') \
                    .props('dense size=sm rounded').classes('bg-amber-600 hover:bg-amber-500 text-white font-bold px-3 py-1 text-xs transition-all shadow')

        # State
        state = {
            'strategy_name': 'MyStrategy',
            'description': '',
            'direction': 'Long',
            
            # Take Profit
            'tp_type': 'fixed',
            'tp_value': '5.0',
            'tp_rr_ratio': '2.0',
            'tp_atr_mult': '3.0',
            'tp_atr_period': '14',
            
            # Stop Loss
            'sl_type': 'fixed',
            'sl_value': '2.0',
            'sl_trailing_pct': '2.0',
            'sl_be_trigger': '1.5',
            'sl_atr_mult': '2.0',
            'sl_atr_period': '14',
            'sl_chandelier_mult': '2.5',
            'sl_chandelier_lookback': '14',
            'sl_swing_lookback': '10',
            
            'parameters': [],
            'entry_rules': [],
            'exit_rules': [],
            'ec_enabled': False,
            'ec_start_dd': '30.0',
            'ec_stop_dd': '0.0',
            
            # Tipos de Órdenes de Ejecución (Por defecto: Señales a MARKET, SL/TP a LIMIT)
            'entry_order_type': 'MARKET',
            'exit_order_type': 'MARKET',
            'sl_order_type': 'LIMIT',
            'tp_order_type': 'LIMIT'
        }
        
        strategy_files = glob.glob(os.path.join(STRATEGIES_DIR, '*.yaml'))
        strategy_names = [os.path.basename(f).replace('.yaml', '') for f in strategy_files]
        if state['strategy_name'] not in strategy_names:
            strategy_names.append(state['strategy_name'])

        def save_strategy():
            def _cast_param(v, t):
                try:
                    if t == 'Entero': return int(float(v))
                    if t == 'Decimal' or t == '%': return float(v)
                    if t == 'Lógico': return str(v).lower() in ['true', '1', 'yes', 't', 'verdadero', 'v']
                except:
                    pass
                return v

            params_dict = {p['name']: _cast_param(p['value'], p.get('type', 'Decimal')) for p in state['parameters'] if p['name']}
            
            def _parse_val_local(v):
                if not v: return 0
                try:
                    num = float(v)
                    return int(num) if num.is_integer() else num
                except ValueError:
                    return v

            # ── Take Profit Config ──
            tp_type_val = state.get('tp_type', 'fixed')
            if tp_type_val == 'fixed':
                tp_config = {"type": "percentage", "value": _parse_val_local(state['tp_value'])}
            elif tp_type_val == 'risk_reward':
                tp_config = {"type": "risk_reward", "risk_reward_ratio": _parse_val_local(state['tp_rr_ratio'])}
            elif tp_type_val == 'atr':
                tp_config = {
                    "type": "atr",
                    "atr_multiplier": _parse_val_local(state['tp_atr_mult']),
                    "atr_period": int(float(state.get('tp_atr_period', 14)))
                }
            elif tp_type_val == 'partial':
                tp_config = {"type": "partial", "value": _parse_val_local(state['tp_value'])}
            else:
                tp_config = {"type": "percentage", "value": _parse_val_local(state['tp_value'])}

            # ── Stop Loss Config ──
            sl_type_val = state.get('sl_type', 'fixed')
            if sl_type_val == 'fixed':
                sl_config = {"type": "percentage", "value": _parse_val_local(state['sl_value'])}
            elif sl_type_val == 'trailing_percent':
                sl_config = {"type": "trailing_percent", "value": _parse_val_local(state['sl_trailing_pct'])}
            elif sl_type_val == 'break_even':
                sl_config = {
                    "type": "break_even",
                    "value": _parse_val_local(state['sl_value']),
                    "be_trigger_pct": _parse_val_local(state['sl_be_trigger'])
                }
            elif sl_type_val == 'atr':
                sl_config = {
                    "type": "atr",
                    "atr_multiplier": _parse_val_local(state['sl_atr_mult']),
                    "atr_period": int(float(state.get('sl_atr_period', 14)))
                }
            elif sl_type_val == 'chandelier':
                sl_config = {
                    "type": "chandelier",
                    "atr_multiplier": _parse_val_local(state['sl_chandelier_mult']),
                    "lookback": int(float(state.get('sl_chandelier_lookback', 14))),
                    "atr_period": int(float(state.get('sl_atr_period', 14)))
                }
            elif sl_type_val == 'swing':
                sl_config = {
                    "type": "swing",
                    "lookback": int(float(state.get('sl_swing_lookback', 10)))
                }
            else:
                sl_config = {"type": "percentage", "value": _parse_val_local(state['sl_value'])}

            ec_start_parsed = state['ec_start_dd'] if not str(state['ec_start_dd']).replace('.', '', 1).isdigit() else _parse_val_local(state['ec_start_dd'])
            ec_stop_parsed = state['ec_stop_dd'] if not str(state['ec_stop_dd']).replace('.', '', 1).isdigit() else _parse_val_local(state['ec_stop_dd'])

            config = {
                "strategy_name": state['strategy_name'],
                "description": state['description'],
                "trade_direction": state['direction'],
                "parameters": params_dict,
                "risk_management": {
                    "take_profit": tp_config,
                    "stop_loss": sl_config
                },
                "execution": {
                    "entry_order_type": state.get('entry_order_type', 'MARKET'),
                    "exit_order_type": state.get('exit_order_type', 'MARKET'),
                    "stop_loss_order_type": state.get('sl_order_type', 'LIMIT'),
                    "take_profit_order_type": state.get('tp_order_type', 'LIMIT')
                },
                "entry_conditions": {
                    "logic": "AND",
                    "rules": state['entry_rules']
                },
                "exit_conditions": {
                    "logic": "OR",
                    "rules": state['exit_rules']
                },
                "equity_curve_management": {
                    "enabled": state['ec_enabled'],
                    "start_trading_at_dd_pct": ec_start_parsed,
                    "stop_trading_at_dd_pct": ec_stop_parsed
                }
            }
            
            filename = os.path.join(STRATEGIES_DIR, f"{state['strategy_name'].lower().replace(' ', '_')}.yaml")
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
                ui.notify(f"Estrategia guardada exitosamente en {filename}", type='positive', icon='check_circle')
            except Exception as e:
                ui.notify(f"Error guardando: {str(e)}", type='negative', icon='error')

        active_target_list = []
        active_target_table = None
        
        # Modal para añadir condición
        with ui.dialog() as rule_dialog:
            with ui.card().classes('w-[600px] max-w-4xl rounded-2xl shadow-2xl p-0 overflow-hidden bg-slate-900 border border-slate-700'):
                with ui.row().classes('w-full bg-blue-600 text-white p-4 items-center'):
                    ui.icon('add_task', size='1.5rem')
                    ui.label('Añadir Condición').classes('text-xl font-bold flex-1')
                    ui.button(icon='close', on_click=rule_dialog.close).props('flat round dense text-color=white')
                
                with ui.column().classes('p-6 w-full gap-4'):
                    rule_type = ui.select(['technical_indicator', 'onchain_threshold'], label='Tipo de Regla', value='technical_indicator').classes('w-full')
                    
                    tech_container = ui.column().classes('w-full bg-slate-800/50 p-4 rounded-xl border border-slate-700/50 gap-2')
                    with tech_container:
                        ui.label('Parámetros del Indicador Técnico').classes('text-sm font-bold text-slate-400 mb-2')
                        with ui.row().classes('w-full gap-4'):
                            ind1_select = ui.select(['Price', 'SMA', 'EMA', 'Volume', 'RSI', 'MACD'], label='Ind. Rápido (Ind 1)', value='EMA').classes('flex-1')
                            p1_input = ui.select(['20'], label='Período (P 1)', value='20', new_value_mode='add-unique').classes('flex-1')
                        
                        op_select = ui.select(['crosses_above', 'crosses_below', 'is_above', 'is_below', 'equals'], label='Operador', value='crosses_above').classes('w-full')
                        
                        with ui.row().classes('w-full gap-4'):
                            ind2_select = ui.select(['Price', 'SMA', 'EMA', 'Volume', 'Level'], label='Ind. Lento (Ind 2)', value='EMA').classes('flex-1')
                            p2_input = ui.select(['50'], label='Período / Valor (P 2)', value='50', new_value_mode='add-unique').classes('flex-1')
                        
                    onchain_container = ui.column().classes('w-full bg-slate-800/50 p-4 rounded-xl border border-slate-700/50 gap-2')
                    with onchain_container:
                        ui.label('Métricas On-Chain').classes('text-sm font-bold text-slate-400 mb-2')
                        metric_input = ui.input('Nombre de Métrica', value='active_addresses').classes('w-full')
                        with ui.row().classes('w-full gap-4'):
                            cond_select = ui.select(['above', 'below', 'increasing', 'decreasing'], label='Condición', value='above').classes('flex-1')
                            val_input = ui.select(['0'], label='Valor / Días (Lookback)', value='0', new_value_mode='add-unique').classes('flex-1')
                        
                    tech_container.bind_visibility_from(rule_type, 'value', backward=lambda v: v == 'technical_indicator')
                    onchain_container.bind_visibility_from(rule_type, 'value', backward=lambda v: v == 'onchain_threshold')
                    
                    def save_rule():
                        r_type = rule_type.value
                        def _parse_val(v):
                            try:
                                num = float(v)
                                return int(num) if num.is_integer() else num
                            except ValueError:
                                return v
                                
                        if r_type == 'technical_indicator':
                            p1_val = _parse_val(p1_input.value)
                            p2_val = _parse_val(p2_input.value)
                            rule_obj = {
                                "name": f"Rule_{len(active_target_list)+1}",
                                "type": "technical_indicator",
                                "indicator_1": {"name": ind1_select.value, "period": p1_val},
                                "operator": op_select.value,
                                "indicator_2": {"name": ind2_select.value, "period": p2_val if ind2_select.value != 'Level' else 0, "value": p2_val if ind2_select.value == 'Level' else 0},
                                "details": f"{ind1_select.value}({p1_input.value}) {op_select.value} {ind2_select.value}({p2_input.value})"
                            }
                        else:
                            val_parsed = _parse_val(val_input.value)
                            rule_obj = {
                                "name": f"Rule_{len(active_target_list)+1}",
                                "type": "onchain_threshold",
                                "metric": metric_input.value,
                                "condition": cond_select.value,
                                "value": val_parsed if cond_select.value in ['above', 'below'] else 0,
                                "lookback_days": val_parsed if cond_select.value in ['increasing', 'decreasing'] else 0,
                                "details": f"OnChain {metric_input.value} {cond_select.value} {val_input.value}"
                            }
                        
                        active_target_list.append(rule_obj)
                        active_target_table.rows = active_target_list
                        active_target_table.update()
                        rule_dialog.close()
                        
                    with ui.row().classes('w-full justify-end gap-2'):
                        ui.button('Cancelar', on_click=rule_dialog.close).classes('bg-slate-700 text-white')
                        ui.button('Guardar Condición', on_click=save_rule).classes('bg-blue-600 text-white font-bold')

        def add_rule(target_list, target_table):
            nonlocal active_target_list, active_target_table
            active_target_list = target_list
            active_target_table = target_table
            
            def _ensure_opt(val, opts_list):
                if val and val not in opts_list:
                    opts_list.append(val)
                return opts_list

            opts = [p['name'] for p in state['parameters'] if p['name']]
            p1_input.options = _ensure_opt(p1_input.value, opts.copy())
            p2_input.options = _ensure_opt(p2_input.value, opts.copy())
            val_input.options = _ensure_opt(val_input.value, opts.copy())
            p1_input.update()
            p2_input.update()
            val_input.update()
            
            rule_dialog.open()
            
        # Contenedor Principal
        with ui.card().classes('w-full bg-slate-900/50 rounded-xl shadow border border-slate-800/80 p-2'):
            # 2. Pestañas estilo Wizard con Iconos (Compactas)
            with ui.tabs().props('dense inline-label').classes('w-full text-slate-300 font-semibold mb-2 bg-slate-800/70 rounded-lg p-0.5') as tabs:
                tab_general = ui.tab('General', icon='info').props('dense')
                tab_params = ui.tab('Parameters', icon='tune').props('dense')
                tab_rules = ui.tab('Rules', icon='rule').props('dense')
                tab_risk = ui.tab('Risk Management', icon='security').props('dense')
                tab_equity = ui.tab('Equity Curve', icon='trending_down').props('dense')
    
            with ui.tab_panels(tabs, value=tab_general).classes('w-full bg-transparent'):
                
                # PANEL GENERAL
                with ui.tab_panel(tab_general):
                    with ui.column().classes('w-full max-w-3xl mx-auto gap-6 q-pa-md'):
                        with ui.card().classes('w-full shadow-lg border border-slate-700/50 rounded-xl p-6'):
                            ui.label('Información Básica').classes('text-lg font-bold text-slate-200 mb-2')
                            ui.separator().classes('mb-4')
                            strat_name_input = ui.input('Nombre de la Estrategia', value=state['strategy_name'], autocomplete=strategy_names).bind_value(state, 'strategy_name').classes('w-full text-lg')
                            ui.input('Descripción Breve', value=state['description']).bind_value(state, 'description').classes('w-full mt-4')
                
                # PANEL PARAMETERS
                with ui.tab_panel(tab_params):
                    with ui.column().classes('w-full max-w-4xl mx-auto gap-4 q-pa-md'):
                        ui.label("Parámetros Dinámicos").classes('text-xl font-bold text-slate-200')
                        ui.label("Define parámetros ajustables (ej. variables de optimización) que podrás usar en las reglas.").classes('text-sm text-slate-500 mb-2')
                        
                        params_list_container = ui.column().classes('w-full gap-3')
                        
                        def render_params():
                            params_list_container.clear()
                            with params_list_container:
                                for idx, p in enumerate(state['parameters']):
                                    with ui.card().classes('w-full flex-row items-center justify-between shadow-lg border border-slate-700/50 rounded-lg p-2 bg-slate-800/50'):
                                        with ui.row().classes('items-center gap-4 flex-1'):
                                            def mk_name_handler(item=p):
                                                def _on_chg(e): item['name'] = e.value or ''
                                                return _on_chg
                                            def mk_type_handler(item=p):
                                                def _on_chg(e): item['type'] = e.value or 'Decimal'
                                                return _on_chg
                                            def mk_val_handler(item=p):
                                                def _on_chg(e): item['value'] = e.value or ''
                                                return _on_chg

                                            ui.input('Nombre', value=p['name'], on_change=mk_name_handler(p)).classes('flex-1')
                                            ui.select(['Decimal', 'Entero', '%', 'Lógico'], label='Tipo', value=p['type'], on_change=mk_type_handler(p)).classes('w-36')
                                            ui.input('Valor por Defecto', value=p['value'], on_change=mk_val_handler(p)).classes('flex-1')
                                            
                                        ui.button(icon='delete', on_click=lambda i=idx: remove_param(i)).props('flat round dense color=negative')
                                        
                            update_risk_options()
                            
                        def add_param():
                            state['parameters'].append({'name': f"P_{len(state['parameters'])+1}", 'type': 'Decimal', 'value': '10'})
                            render_params()
                            
                        def remove_param(idx):
                            state['parameters'].pop(idx)
                            render_params()
                            
                        with ui.row().classes('w-full mt-4'):
                            ui.button('Añadir Nuevo Parámetro', on_click=add_param, icon='add').classes('bg-slate-800 text-white rounded-full px-6 py-2 shadow-xl hover:bg-slate-700 transition-all')
                        
                # ═════════════════════════════════════════════════════
                # PANEL RISK MANAGEMENT (AVANZADO)
                # ═════════════════════════════════════════════════════
                with ui.tab_panel(tab_risk):
                    with ui.column().classes('w-full max-w-4xl mx-auto gap-4 q-pa-sm'):
                        
                        # ── TARJETA 1: TAKE PROFIT ──
                        with ui.card().classes('w-full shadow-lg border border-emerald-900/50 bg-slate-900/80 rounded-xl p-5'):
                            with ui.row().classes('w-full items-center justify-between border-b border-slate-800 pb-3 mb-3'):
                                with ui.row().classes('items-center gap-2'):
                                    ui.icon('trending_up', size='1.75rem').classes('text-emerald-400')
                                    with ui.column().classes('gap-0'):
                                        ui.label('Take Profit (Toma de Beneficios)').classes('text-base font-bold text-white')
                                        ui.label('Configura la regla y objetivo de cierre en ganancias').classes('text-[11px] text-slate-400')
                            
                            # Selector de Tipo de TP
                            tp_type_select = ui.select(
                                {
                                    'fixed': 'Fijo / Porcentual (%)',
                                    'risk_reward': 'Ratio Riesgo / Beneficio (R:R)',
                                    'atr': 'Múltiplo de Volatilidad (ATR)',
                                    'partial': 'Salida Parcial / Escalonada'
                                },
                                label='Tipo de Lógica de Take Profit',
                                value=state['tp_type']
                            ).bind_value(state, 'tp_type').classes('w-full mb-3')
                            
                            # Explicación Didáctica
                            tp_desc_label = ui.label(TP_EXPLANATIONS.get(state['tp_type'], '')).classes(
                                'text-xs text-emerald-300/90 bg-emerald-950/40 p-2.5 rounded-lg border border-emerald-800/40 w-full mb-3 leading-relaxed'
                            )
                            
                            def _on_tp_type_change(e):
                                val = e.value if hasattr(e, 'value') else e
                                tp_desc_label.set_text(TP_EXPLANATIONS.get(val, ''))
                                # Visibilidad de campos
                                tp_fijo_row.set_visibility(val in ['fixed', 'partial'])
                                tp_rr_row.set_visibility(val == 'risk_reward')
                                tp_atr_row.set_visibility(val == 'atr')
                            
                            tp_type_select.on_value_change(_on_tp_type_change)

                            # Campos Contextuales TP
                            # 1. Fijo / Parcial
                            with ui.row().classes('w-full gap-4') as tp_fijo_row:
                                tp_risk_input = ui.select(
                                    [state['tp_value']],
                                    label='Take Profit (% / Parámetro)',
                                    value=state['tp_value'],
                                    new_value_mode='add-unique'
                                ).bind_value(state, 'tp_value').classes('flex-1')

                            # 2. Risk/Reward
                            with ui.row().classes('w-full gap-4') as tp_rr_row:
                                tp_rr_input = ui.select(
                                    [state['tp_rr_ratio'], '1.5', '2.0', '2.5', '3.0', '4.0'],
                                    label='Ratio R:R (Multiplicador de SL, ej: 2.0 = 2R)',
                                    value=state['tp_rr_ratio'],
                                    new_value_mode='add-unique'
                                ).bind_value(state, 'tp_rr_ratio').classes('flex-1')
                            tp_rr_row.set_visibility(state['tp_type'] == 'risk_reward')

                            # 3. ATR
                            with ui.row().classes('w-full gap-4') as tp_atr_row:
                                tp_atr_mult_input = ui.select(
                                    [state['tp_atr_mult'], '2.0', '3.0', '4.0', '5.0'],
                                    label='Multiplicador ATR (K)',
                                    value=state['tp_atr_mult'],
                                    new_value_mode='add-unique'
                                ).bind_value(state, 'tp_atr_mult').classes('flex-1')
                                tp_atr_period_input = ui.number('Período ATR', value=int(state['tp_atr_period']), min=1, step=1).bind_value(state, 'tp_atr_period').classes('w-36')
                            tp_atr_row.set_visibility(state['tp_type'] == 'atr')


                        # ── TARJETA 2: STOP LOSS ──
                        with ui.card().classes('w-full shadow-lg border border-rose-900/50 bg-slate-900/80 rounded-xl p-5'):
                            with ui.row().classes('w-full items-center justify-between border-b border-slate-800 pb-3 mb-3'):
                                with ui.row().classes('items-center gap-2'):
                                    ui.icon('shield', size='1.75rem').classes('text-rose-400')
                                    with ui.column().classes('gap-0'):
                                        ui.label('Stop Loss (Gestión de Pérdidas y Salidas Dinámicas)').classes('text-base font-bold text-white')
                                        ui.label('Protección de capital, trailing stops y límites de riesgo').classes('text-[11px] text-slate-400')
                            
                            # Selector de Tipo de SL
                            sl_type_select = ui.select(
                                {
                                    'fixed': 'Fijo / Porcentual (%)',
                                    'trailing_percent': 'Trailing Stop Porcentual (%)',
                                    'break_even': 'Break-Even Automático + Trailing',
                                    'atr': 'Volatilidad Dinámica (ATR)',
                                    'chandelier': 'Chandelier Exit (Trailing ATR)',
                                    'swing': 'Swing Low / High (Soporte/Resistencia)'
                                },
                                label='Tipo de Lógica de Stop Loss',
                                value=state['sl_type']
                            ).bind_value(state, 'sl_type').classes('w-full mb-3')
                            
                            # Explicación Didáctica
                            sl_desc_label = ui.label(SL_EXPLANATIONS.get(state['sl_type'], '')).classes(
                                'text-xs text-rose-300/90 bg-rose-950/40 p-2.5 rounded-lg border border-rose-800/40 w-full mb-3 leading-relaxed'
                            )
                            
                            def _on_sl_type_change(e):
                                val = e.value if hasattr(e, 'value') else e
                                sl_desc_label.set_text(SL_EXPLANATIONS.get(val, ''))
                                # Visibilidad de campos
                                sl_fijo_row.set_visibility(val == 'fixed')
                                sl_trailing_row.set_visibility(val == 'trailing_percent')
                                sl_be_row.set_visibility(val == 'break_even')
                                sl_atr_row.set_visibility(val == 'atr')
                                sl_chan_row.set_visibility(val == 'chandelier')
                                sl_swing_row.set_visibility(val == 'swing')
                            
                            sl_type_select.on_value_change(_on_sl_type_change)

                            # Campos Contextuales SL
                            # 1. Fijo
                            with ui.row().classes('w-full gap-4') as sl_fijo_row:
                                sl_risk_input = ui.select(
                                    [state['sl_value']],
                                    label='Stop Loss (% / Parámetro)',
                                    value=state['sl_value'],
                                    new_value_mode='add-unique'
                                ).bind_value(state, 'sl_value').classes('flex-1')

                            # 2. Trailing Porcentual
                            with ui.row().classes('w-full gap-4') as sl_trailing_row:
                                sl_trailing_input = ui.select(
                                    [state['sl_trailing_pct'], '1.0', '1.5', '2.0', '3.0', '5.0'],
                                    label='Distancia de Trailing Stop (% desde el pico más alto/bajo)',
                                    value=state['sl_trailing_pct'],
                                    new_value_mode='add-unique'
                                ).bind_value(state, 'sl_trailing_pct').classes('flex-1')
                            sl_trailing_row.set_visibility(state['sl_type'] == 'trailing_percent')

                            # 3. Break-Even + Trailing
                            with ui.row().classes('w-full gap-4') as sl_be_row:
                                sl_be_trigger_input = ui.select(
                                    [state['sl_be_trigger'], '1.0', '1.5', '2.0', '2.5'],
                                    label='Disparador Break-Even (+% de Ganancia para mover SL a entrada)',
                                    value=state['sl_be_trigger'],
                                    new_value_mode='add-unique'
                                ).bind_value(state, 'sl_be_trigger').classes('flex-1')
                                sl_be_initial_input = ui.select(
                                    [state['sl_value'], '1.5', '2.0', '3.0'],
                                    label='Stop Loss Inicial (% antes de Break-Even)',
                                    value=state['sl_value'],
                                    new_value_mode='add-unique'
                                ).bind_value(state, 'sl_value').classes('flex-1')
                            sl_be_row.set_visibility(state['sl_type'] == 'break_even')

                            # 4. Volatilidad ATR
                            with ui.row().classes('w-full gap-4') as sl_atr_row:
                                sl_atr_mult_input = ui.select(
                                    [state['sl_atr_mult'], '1.5', '2.0', '2.5', '3.0'],
                                    label='Multiplicador ATR (K)',
                                    value=state['sl_atr_mult'],
                                    new_value_mode='add-unique'
                                ).bind_value(state, 'sl_atr_mult').classes('flex-1')
                                sl_atr_period_input = ui.number('Período ATR', value=int(state['sl_atr_period']), min=1, step=1).bind_value(state, 'sl_atr_period').classes('w-36')
                            sl_atr_row.set_visibility(state['sl_type'] == 'atr')

                            # 5. Chandelier Exit
                            with ui.row().classes('w-full gap-4') as sl_chan_row:
                                sl_chan_mult_input = ui.select(
                                    [state['sl_chandelier_mult'], '2.0', '2.5', '3.0'],
                                    label='Multiplicador ATR (Chandelier)',
                                    value=state['sl_chandelier_mult'],
                                    new_value_mode='add-unique'
                                ).bind_value(state, 'sl_chandelier_mult').classes('flex-1')
                                sl_chan_lookback_input = ui.number('Velas Lookback (N)', value=int(state['sl_chandelier_lookback']), min=2, step=1).bind_value(state, 'sl_chandelier_lookback').classes('w-36')
                            sl_chan_row.set_visibility(state['sl_type'] == 'chandelier')

                            # 6. Swing Low / High
                            with ui.row().classes('w-full gap-4') as sl_swing_row:
                                sl_swing_lookback_input = ui.number(
                                    'Velas Lookback para Swing Low/High (N)',
                                    value=int(state['sl_swing_lookback']),
                                    min=2,
                                    step=1
                                ).bind_value(state, 'sl_swing_lookback').classes('flex-1')
                            sl_swing_row.set_visibility(state['sl_type'] == 'swing')

                        # ── TARJETA 3: TIPOS DE ÓRDENES DE EJECUCIÓN ──
                        with ui.card().classes('w-full shadow-lg border border-purple-900/50 bg-slate-900/80 rounded-xl p-5'):
                            with ui.row().classes('w-full items-center justify-between border-b border-slate-800 pb-3 mb-3'):
                                with ui.row().classes('items-center gap-2'):
                                    ui.icon('tune', size='1.75rem').classes('text-purple-400')
                                    with ui.column().classes('gap-0'):
                                        ui.label('Tipos de Órdenes de Ejecución (Execution Types)').classes('text-base font-bold text-white')
                                        ui.label('Configura el tipo de orden para señales (Market por defecto) y órdenes condicionales (Limit por defecto)').classes('text-[11px] text-slate-400')
                                ui.badge('DEFAULT: SEÑALES MARKET / SL-TP LIMIT', color='purple-9').props('rounded').classes('text-[10px] font-bold font-mono px-2 py-0.5')

                            with ui.grid(columns=2).classes('w-full gap-4'):
                                with ui.column().classes('gap-1'):
                                    ui.label('Orden de Entrada por Señal').classes('text-xs text-slate-400 font-semibold')
                                    ui.select(
                                        {'MARKET': '⚡ MARKET (A Mercado)', 'LIMIT': '🎯 LIMIT (Límite al Precio)'},
                                        value=state['entry_order_type']
                                    ).bind_value(state, 'entry_order_type').classes('w-full')
                                
                                with ui.column().classes('gap-1'):
                                    ui.label('Orden de Salida por Señal').classes('text-xs text-slate-400 font-semibold')
                                    ui.select(
                                        {'MARKET': '⚡ MARKET (A Mercado)', 'LIMIT': '🎯 LIMIT (Límite al Precio)'},
                                        value=state['exit_order_type']
                                    ).bind_value(state, 'exit_order_type').classes('w-full')

                                with ui.column().classes('gap-1'):
                                    ui.label('Orden de Stop Loss (SL)').classes('text-xs text-slate-400 font-semibold')
                                    ui.select(
                                        {'LIMIT': '🛡️ LIMIT (Stop Limit - Predeterminado)', 'MARKET': '⚡ MARKET (Stop Market)'},
                                        value=state['sl_order_type']
                                    ).bind_value(state, 'sl_order_type').classes('w-full')

                                with ui.column().classes('gap-1'):
                                    ui.label('Orden de Take Profit (TP)').classes('text-xs text-slate-400 font-semibold')
                                    ui.select(
                                        {'LIMIT': '🎯 LIMIT (Take Profit Limit - Predeterminado)', 'MARKET': '⚡ MARKET (Take Profit Market)'},
                                        value=state['tp_order_type']
                                    ).bind_value(state, 'tp_order_type').classes('w-full')

                def update_risk_options():
                    opts = [p['name'] for p in state['parameters'] if p['name']]
                    
                    def _sync_opts(widget, current_val):
                        if widget is not None:
                            w_opts = opts.copy()
                            if current_val and current_val not in w_opts:
                                w_opts.append(current_val)
                            widget.options = w_opts
                            widget.update()

                    try:
                        _sync_opts(tp_risk_input, state['tp_value'])
                        _sync_opts(tp_rr_input, state['tp_rr_ratio'])
                        _sync_opts(tp_atr_mult_input, state['tp_atr_mult'])
                        _sync_opts(sl_risk_input, state['sl_value'])
                        _sync_opts(sl_trailing_input, state['sl_trailing_pct'])
                        _sync_opts(sl_be_trigger_input, state['sl_be_trigger'])
                        _sync_opts(sl_be_initial_input, state['sl_value'])
                        _sync_opts(sl_atr_mult_input, state['sl_atr_mult'])
                        _sync_opts(sl_chan_mult_input, state['sl_chandelier_mult'])
                    except Exception:
                        pass

                    ec_start_opts = opts.copy()
                    if state['ec_start_dd'] not in ec_start_opts: ec_start_opts.append(state['ec_start_dd'])
                    try: ec_start.options = ec_start_opts; ec_start.update()
                    except: pass

                    ec_stop_opts = opts.copy()
                    if state['ec_stop_dd'] not in ec_stop_opts: ec_stop_opts.append(state['ec_stop_dd'])
                    try: ec_stop.options = ec_stop_opts; ec_stop.update()
                    except: pass

                render_params()

                # PANEL EQUITY CURVE
                with ui.tab_panel(tab_equity):
                    with ui.column().classes('w-full max-w-3xl mx-auto gap-6 q-pa-md'):
                        with ui.card().classes('w-full shadow-lg border border-slate-700/50 rounded-xl p-6 bg-slate-900/50'):
                            ui.label('Trading por Curva de Capital (Equity Curve)').classes('text-xl font-bold text-slate-200 mb-2')
                            ui.label('Activa operaciones reales solo cuando el Drawdown de la estrategia supere cierto umbral, deteniéndose al recuperarse.').classes('text-sm text-slate-400 mb-4')
                            ui.separator().classes('mb-4')
                            
                            ec_switch = ui.switch('Habilitar Equity Curve Trading', value=state['ec_enabled']).bind_value(state, 'ec_enabled').classes('text-md font-semibold text-blue-400 mb-4')
                            
                            with ui.row().classes('w-full gap-4'):
                                ec_start = ui.select([state['ec_start_dd']], label='Empezar a operar si DD (%) >= (Valor o Param)', value=state['ec_start_dd'], new_value_mode='add-unique').bind_value(state, 'ec_start_dd').classes('flex-1').props('outlined dark bg-color="transparent"')
                                ec_stop = ui.select([state['ec_stop_dd']], label='Dejar de operar si DD (%) <= (Valor o Param)', value=state['ec_stop_dd'], new_value_mode='add-unique').bind_value(state, 'ec_stop_dd').classes('flex-1').props('outlined dark bg-color="transparent"')
     
                # PANEL RULES
                with ui.tab_panel(tab_rules):
                    with ui.column().classes('w-full q-pa-md max-w-5xl mx-auto gap-6'):
                        # Dirección del Trade
                        with ui.card().classes('w-full bg-slate-800/50 border border-slate-700/50 rounded-xl p-4 shadow-lg'):
                            with ui.row().classes('w-full items-center gap-4'):
                                ui.icon('swap_vert', size='1.5rem').classes('text-slate-600')
                                ui.label("Dirección Principal del Trade:").classes('font-bold text-slate-300')
                                ui.select(['Long', 'Short'], value=state['direction']).bind_value(state, 'direction').classes('w-48')

                        columns = [
                            {'name': 'type', 'label': 'Tipo de Regla', 'field': 'type', 'sortable': True, 'align': 'left'},
                            {'name': 'details', 'label': 'Detalles / Definición Lógica', 'field': 'details', 'sortable': True, 'align': 'left'},
                            {'name': 'actions', 'label': '', 'field': 'actions', 'align': 'right'},
                        ]

                        # Entry Rules
                        with ui.column().classes('w-full'):
                            with ui.row().classes('w-full items-center justify-between mb-2'):
                                ui.label("Condiciones de Entrada (AND)").classes('text-lg font-bold text-green-700')
                                ui.button('Añadir Entrada', on_click=lambda: add_rule(state['entry_rules'], entry_table), icon='add').classes('bg-green-600 text-white rounded-lg px-4 shadow-lg hover:bg-green-500')
                            
                            entry_table = ui.table(columns=columns, rows=state['entry_rules'], row_key='name').classes('w-full rounded-lg shadow-lg border border-slate-700/50 bg-slate-900/50')
                            entry_table.add_slot('body-cell-actions', '''
                                <q-td :props="props">
                                    <q-btn flat dense round icon="delete" color="negative" @click="() => $parent.$emit('delete_entry', props.row.name)" />
                                </q-td>
                            ''')
                            def del_entry(e):
                                state['entry_rules'] = [r for r in state['entry_rules'] if r.get('name') != e.args]
                                entry_table.rows = state['entry_rules']
                                entry_table.update()
                            entry_table.on('delete_entry', del_entry)

                        ui.separator().classes('my-4')
                        
                        # Exit Rules
                        with ui.column().classes('w-full'):
                            with ui.row().classes('w-full items-center justify-between mb-2'):
                                ui.label("Condiciones de Salida (OR)").classes('text-lg font-bold text-red-700')
                                ui.button('Añadir Salida', on_click=lambda: add_rule(state['exit_rules'], exit_table), icon='add').classes('bg-red-600 text-white rounded-lg px-4 shadow-lg hover:bg-red-500')
                            
                            exit_table = ui.table(columns=columns, rows=state['exit_rules'], row_key='name').classes('w-full rounded-lg shadow-lg border border-slate-700/50 bg-slate-900/50')
                            exit_table.add_slot('body-cell-actions', '''
                                <q-td :props="props">
                                    <q-btn flat dense round icon="delete" color="negative" @click="() => $parent.$emit('delete_exit', props.row.name)" />
                                </q-td>
                            ''')
                            def del_exit(e):
                                state['exit_rules'] = [r for r in state['exit_rules'] if r.get('name') != e.args]
                                exit_table.rows = state['exit_rules']
                                exit_table.update()
                            exit_table.on('delete_exit', del_exit)

        # Modal Catálogo
        with ui.dialog() as catalog_dialog:
            with ui.card().classes('w-[900px] max-w-5xl rounded-2xl shadow-2xl p-0 overflow-hidden'):
                with ui.row().classes('w-full bg-slate-800 text-white p-4 items-center'):
                    ui.icon('auto_awesome_mosaic', size='1.5rem')
                    ui.label('Catálogo de Estrategias').classes('text-xl font-bold flex-1 ml-2')
                    ui.button(icon='close', on_click=catalog_dialog.close).props('flat round dense text-color=white')
                
                with ui.column().classes('p-6 w-full'):
                    catalog_columns = [
                        {'name': 'name', 'label': 'Nombre', 'field': 'name', 'sortable': True, 'align': 'left'},
                        {'name': 'direction', 'label': 'Dirección', 'field': 'direction', 'sortable': True, 'align': 'center'},
                        {'name': 'tp', 'label': 'T. Profit', 'field': 'tp', 'sortable': True, 'align': 'right'},
                        {'name': 'sl', 'label': 'S. Loss', 'field': 'sl', 'sortable': True, 'align': 'right'},
                        {'name': 'description', 'label': 'Descripción', 'field': 'description', 'sortable': True, 'align': 'left'},
                        {'name': 'actions', 'label': 'Acciones', 'field': 'actions', 'align': 'center'},
                    ]
                    catalog_table = ui.table(columns=catalog_columns, rows=[], row_key='name').classes('w-full border border-slate-700/50 rounded-lg shadow-lg')
                    
                    catalog_table.add_slot('body-cell-actions', '''
                        <q-td :props="props">
                            <q-btn flat dense round icon="edit" color="primary" @click="() => $parent.$emit('edit', props.row)" />
                            <q-btn flat dense round icon="delete" color="negative" @click="() => $parent.$emit('delete', props.row)" />
                        </q-td>
                    ''')
                    
                    def load_catalog():
                        rows = []
                        for f_path in glob.glob(os.path.join(STRATEGIES_DIR, '*.yaml')):
                            try:
                                with open(f_path, 'r', encoding='utf-8') as f:
                                    data = yaml.safe_load(f)
                                    if data:
                                        tp_c = data.get('risk_management', {}).get('take_profit', {})
                                        sl_c = data.get('risk_management', {}).get('stop_loss', {})
                                        
                                        tp_str = f"{tp_c.get('type', 'Fijo')}: {tp_c.get('value', tp_c.get('risk_reward_ratio', tp_c.get('atr_multiplier', 'N/A')))}"
                                        sl_str = f"{sl_c.get('type', 'Fijo')}: {sl_c.get('value', sl_c.get('atr_multiplier', sl_c.get('lookback', 'N/A')))}"
                                        
                                        rows.append({
                                            'name': data.get('strategy_name', os.path.basename(f_path)),
                                            'filename': os.path.basename(f_path),
                                            'direction': data.get('trade_direction', 'N/A'),
                                            'tp': tp_str,
                                            'sl': sl_str,
                                            'description': data.get('description', '')
                                        })
                            except:
                                pass
                        catalog_table.rows = rows
                        catalog_table.update()
                        catalog_dialog.open()
                        
                    def load_strategy_data(strategy_name):
                        matched_file = None
                        for f_path in glob.glob(os.path.join(STRATEGIES_DIR, '*.yaml')):
                            try:
                                with open(f_path, 'r', encoding='utf-8') as f:
                                    data = yaml.safe_load(f)
                                    if data and data.get('strategy_name') == strategy_name:
                                        matched_file = f_path
                                        break
                            except Exception:
                                pass
                        if not matched_file:
                            matched_file = os.path.join(STRATEGIES_DIR, f"{strategy_name.lower().replace(' ', '_')}.yaml")
                            
                        try:
                            with open(matched_file, 'r', encoding='utf-8') as f:
                                data = yaml.safe_load(f)
                                if data:
                                    state['strategy_name'] = data.get('strategy_name', strategy_name)
                                    state['description'] = data.get('description', '')
                                    state['direction'] = data.get('trade_direction', 'Long')
                                    
                                    # Cargar Take Profit
                                    tp_c = data.get('risk_management', {}).get('take_profit', {})
                                    tp_raw_type = tp_c.get('type', 'fixed')
                                    if tp_raw_type in ['percentage', 'fixed']:
                                        state['tp_type'] = 'fixed'
                                        state['tp_value'] = str(tp_c.get('value', '5.0'))
                                    elif tp_raw_type in ['risk_reward', 'rr']:
                                        state['tp_type'] = 'risk_reward'
                                        state['tp_rr_ratio'] = str(tp_c.get('risk_reward_ratio', tp_c.get('value', '2.0')))
                                    elif tp_raw_type == 'atr':
                                        state['tp_type'] = 'atr'
                                        state['tp_atr_mult'] = str(tp_c.get('atr_multiplier', '3.0'))
                                        state['tp_atr_period'] = str(tp_c.get('atr_period', '14'))
                                    elif tp_raw_type == 'partial':
                                        state['tp_type'] = 'partial'
                                        state['tp_value'] = str(tp_c.get('value', '5.0'))
                                    else:
                                        state['tp_type'] = 'fixed'
                                        state['tp_value'] = str(tp_c.get('value', '5.0'))

                                    # Cargar Stop Loss
                                    sl_c = data.get('risk_management', {}).get('stop_loss', {})
                                    sl_raw_type = sl_c.get('type', 'fixed')
                                    if sl_raw_type in ['percentage', 'fixed']:
                                        state['sl_type'] = 'fixed'
                                        state['sl_value'] = str(sl_c.get('value', '2.0'))
                                    elif sl_raw_type in ['trailing_percent', 'trailing']:
                                        state['sl_type'] = 'trailing_percent'
                                        state['sl_trailing_pct'] = str(sl_c.get('value', '2.0'))
                                    elif sl_raw_type in ['break_even', 'breakeven']:
                                        state['sl_type'] = 'break_even'
                                        state['sl_value'] = str(sl_c.get('value', '2.0'))
                                        state['sl_be_trigger'] = str(sl_c.get('be_trigger_pct', '1.5'))
                                    elif sl_raw_type in ['atr', 'volatility']:
                                        state['sl_type'] = 'atr'
                                        state['sl_atr_mult'] = str(sl_c.get('atr_multiplier', '2.0'))
                                        state['sl_atr_period'] = str(sl_c.get('atr_period', '14'))
                                    elif sl_raw_type in ['chandelier', 'chandelier_exit']:
                                        state['sl_type'] = 'chandelier'
                                        state['sl_chandelier_mult'] = str(sl_c.get('atr_multiplier', '2.5'))
                                        state['sl_chandelier_lookback'] = str(sl_c.get('lookback', '14'))
                                        state['sl_atr_period'] = str(sl_c.get('atr_period', '14'))
                                    elif sl_raw_type in ['swing', 'support_resistance']:
                                        state['sl_type'] = 'swing'
                                        state['sl_swing_lookback'] = str(sl_c.get('lookback', '10'))
                                    else:
                                        state['sl_type'] = 'fixed'
                                        state['sl_value'] = str(sl_c.get('value', '2.0'))
                                    
                                    state['entry_rules'] = data.get('entry_conditions', {}).get('rules', [])
                                    state['exit_rules'] = data.get('exit_conditions', {}).get('rules', [])
                                    
                                    ec_config = data.get('equity_curve_management') or {}
                                    state['ec_enabled'] = ec_config.get('enabled', False)
                                    state['ec_start_dd'] = str(ec_config.get('start_trading_at_dd_pct', '30.0'))
                                    state['ec_stop_dd'] = str(ec_config.get('stop_trading_at_dd_pct', '0.0'))
                                    
                                    params_dict = data.get('parameters') or {}
                                    def _guess_type(val):
                                        if isinstance(val, bool): return 'Lógico'
                                        if isinstance(val, int): return 'Entero'
                                        return 'Decimal'
                                    state['parameters'] = [{'name': k, 'type': _guess_type(v), 'value': str(v)} for k, v in params_dict.items()]
                                    
                                    render_params()
                                    entry_table.rows = state['entry_rules']
                                    entry_table.update()
                                    exit_table.rows = state['exit_rules']
                                    exit_table.update()
                                    strat_name_input.update()
                                    try:
                                        ec_switch.update()
                                        ec_start.update()
                                        ec_stop.update()
                                        tp_type_select.value = state['tp_type']
                                        sl_type_select.value = state['sl_type']
                                        _on_tp_type_change(state['tp_type'])
                                        _on_sl_type_change(state['sl_type'])
                                    except:
                                        pass
                                    
                                    ui.notify(f"Estrategia '{strategy_name}' cargada", type='info')
                        except Exception as ex:
                            ui.notify(f"Error cargando: {str(ex)}", type='negative')

                    def delete_strategy(e):
                        row = e.args
                        strategy_name = row['name']
                        matched_file = None
                        for f_path in glob.glob(os.path.join(STRATEGIES_DIR, '*.yaml')):
                            try:
                                with open(f_path, 'r', encoding='utf-8') as f:
                                    data = yaml.safe_load(f)
                                    if data and data.get('strategy_name') == strategy_name:
                                        matched_file = f_path
                                        break
                            except Exception:
                                pass
                        if not matched_file:
                            matched_file = os.path.join(STRATEGIES_DIR, f"{strategy_name.lower().replace(' ', '_')}.yaml")

                        try:
                            if os.path.exists(matched_file):
                                os.remove(matched_file)
                                ui.notify(f"Estrategia '{strategy_name}' eliminada correctamente", type='positive')
                            else:
                                ui.notify(f"No se encontró el archivo de la estrategia", type='warning')
                            
                            # Refrescar catálogo
                            load_catalog()
                            # Actualizar nombres en el autocomplete
                            nonlocal strategy_names
                            strategy_files = glob.glob(os.path.join(STRATEGIES_DIR, '*.yaml'))
                            strategy_names = [os.path.basename(f).replace('.yaml', '') for f in strategy_files]
                            strat_name_input.options = strategy_names
                            strat_name_input.update()
                        except Exception as ex:
                            ui.notify(f"Error eliminando: {str(ex)}", type='negative')

                    catalog_table.on('edit', lambda e: (load_strategy_data(e.args['name']), catalog_dialog.close()))
                    catalog_table.on('delete', delete_strategy)

        with ui.row().classes('w-full justify-end mt-4 gap-4'):
            ui.button('Guardar Estrategia', on_click=save_strategy, icon='save') \
                .classes('bg-blue-600 hover:bg-blue-500 text-white font-bold px-8 py-3 rounded-xl shadow-lg transition-all')

    return {
        'state': state,
        'load_strategy_data': load_strategy_data
    }
