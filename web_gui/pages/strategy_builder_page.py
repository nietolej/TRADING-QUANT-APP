from nicegui import ui
import yaml
import os
import glob
import time

# Ruta absoluta a la raíz del proyecto (2 niveles arriba de este archivo)
_PAGE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(_PAGE_DIR, '..', '..'))
STRATEGIES_DIR = os.path.join(BASE_DIR, 'config', 'strategies')

def render_strategy_builder():
    with ui.column().classes('w-full q-pa-sm'):
        
        # 1. Header estilo "Hero"
        with ui.card().classes('w-full bg-slate-900 text-white rounded-2xl shadow-xl q-pa-lg mb-6'):
            with ui.row().classes('w-full justify-between items-center'):
                with ui.row().classes('items-center gap-4'):
                    ui.icon('query_stats', size='3rem').classes('text-blue-400')
                    with ui.column().classes('gap-0'):
                        ui.label('Strategy Builder').classes('text-3xl font-bold tracking-wide')
                        ui.label('Diseña, parametriza y guarda tus estrategias de trading cuantitativo.').classes('text-slate-400 text-sm')
                
                ui.button('Catálogo de Estrategias', on_click=lambda: load_catalog(), icon='view_list') \
                    .classes('bg-blue-600 hover:bg-blue-500 text-white font-bold rounded-full shadow-lg px-6 py-2 transition-all')

        # State
        state = {
            'strategy_name': 'MyStrategy',
            'description': '',
            'direction': 'Long',
            'tp_value': '5.0',
            'sl_value': '2.0',
            'parameters': [],
            'entry_rules': [],
            'exit_rules': []
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

            tp_parsed = _parse_val_local(state['tp_value'])
            tp_config = {"type": "percentage", "value": tp_parsed} if tp_parsed else {"type": "none", "value": 0.0}

            sl_parsed = _parse_val_local(state['sl_value'])
            sl_config = {"type": "percentage", "value": sl_parsed} if sl_parsed else {"type": "none", "value": 0.0}

            config = {
                "strategy_name": state['strategy_name'],
                "description": state['description'],
                "trade_direction": state['direction'],
                "parameters": params_dict,
                "risk_management": {
                    "take_profit": tp_config,
                    "stop_loss": sl_config
                },
                "entry_conditions": {
                    "logic": "AND",
                    "rules": state['entry_rules']
                },
                "exit_conditions": {
                    "logic": "OR",
                    "rules": state['exit_rules']
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
        active_table = [None]

        # 5. Modal for adding a rule
        with ui.dialog() as rule_dialog:
            with ui.card().classes('w-[600px] rounded-2xl shadow-2xl p-0 overflow-hidden'):
                # Header del Dialog
                with ui.row().classes('w-full bg-blue-600 text-white p-4 items-center'):
                    ui.icon('add_task', size='1.5rem')
                    ui.label('Añadir Condición').classes('text-xl font-bold flex-1')
                    ui.button(icon='close', on_click=rule_dialog.close).props('flat round dense text-color=white')
                
                # Contenido del Dialog
                with ui.column().classes('p-6 w-full gap-4'):
                    rule_type = ui.select(['technical_indicator', 'onchain_threshold'], label='Tipo de Regla', value='technical_indicator').classes('w-full')
                    
                    # Technical indicator fields container
                    tech_container = ui.column().classes('w-full bg-slate-50 p-4 rounded-xl border border-slate-200 gap-2')
                    with tech_container:
                        ui.label('Parámetros del Indicador Técnico').classes('text-sm font-bold text-slate-600 mb-2')
                        with ui.row().classes('w-full gap-4'):
                            ind1_select = ui.select(['Price', 'SMA', 'EMA', 'Volume', 'RSI', 'MACD'], label='Ind. Rápido (Ind 1)', value='EMA').classes('flex-1')
                            p1_input = ui.select(['20'], label='Período (P 1)', value='20', new_value_mode='add-unique').classes('flex-1')
                        
                        op_select = ui.select(['crosses_above', 'crosses_below', 'is_above', 'is_below', 'equals'], label='Operador', value='crosses_above').classes('w-full')
                        
                        with ui.row().classes('w-full gap-4'):
                            ind2_select = ui.select(['Price', 'SMA', 'EMA', 'Volume', 'Level'], label='Ind. Lento (Ind 2)', value='EMA').classes('flex-1')
                            p2_input = ui.select(['50'], label='Período / Valor (P 2)', value='50', new_value_mode='add-unique').classes('flex-1')
                        
                    # On-chain fields container
                    onchain_container = ui.column().classes('w-full bg-slate-50 p-4 rounded-xl border border-slate-200 gap-2')
                    with onchain_container:
                        ui.label('Métricas On-Chain').classes('text-sm font-bold text-slate-600 mb-2')
                        metric_input = ui.input('Nombre de Métrica', value='active_addresses').classes('w-full')
                        with ui.row().classes('w-full gap-4'):
                            cond_select = ui.select(['above', 'below', 'increasing', 'decreasing'], label='Condición', value='above').classes('flex-1')
                            val_input = ui.select(['0'], label='Valor / Días (Lookback)', value='0', new_value_mode='add-unique').classes('flex-1')
                        
                    # Bind visibility
                    tech_container.bind_visibility_from(rule_type, 'value', backward=lambda v: v == 'technical_indicator')
                    onchain_container.bind_visibility_from(rule_type, 'value', backward=lambda v: v == 'onchain_threshold')
                    
                    def _parse_val(v):
                        if not v: return 0
                        try:
                            num = float(v)
                            return int(num) if num.is_integer() else num
                        except ValueError:
                            return v

                    def save_rule():
                        new_rule = {
                            "name": f"rule_{int(time.time() * 1000)}",
                            "type": rule_type.value,
                        }
                        if rule_type.value == 'technical_indicator':
                            new_rule.update({
                                "indicator1": ind1_select.value,
                                "period1": _parse_val(p1_input.value),
                                "operator": op_select.value,
                                "indicator2": ind2_select.value,
                                "period2": _parse_val(p2_input.value)
                            })
                            new_rule['details'] = f"{new_rule.get('indicator1')}({new_rule.get('period1')}) {new_rule.get('operator')} {new_rule.get('indicator2')}({new_rule.get('period2')})"
                        else:
                            new_rule.update({
                                "metric": metric_input.value,
                                "condition": cond_select.value,
                                "value": _parse_val(val_input.value)
                            })
                            if cond_select.value in ['increasing', 'decreasing']:
                                new_rule["lookback_days"] = _parse_val(val_input.value)
                                new_rule["min_change_pct"] = 0
                            new_rule['details'] = f"OnChain({new_rule.get('metric')}) {new_rule.get('condition')} ({new_rule.get('value')})"
                        
                        active_target_list.append(new_rule)
                        if active_table[0]:
                            active_table[0].rows = active_target_list[:]
                            active_table[0].update()
                        rule_dialog.close()
                        ui.notify('Regla agregada', type='info')
                        
                    with ui.row().classes('w-full justify-end mt-4 gap-4'):
                        ui.button('Cancelar', on_click=rule_dialog.close).props('flat text-color=grey-8')
                        ui.button('Añadir Condición', on_click=save_rule).classes('bg-blue-600 text-white rounded-lg px-6')

        def add_rule(target_list, table):
            nonlocal active_target_list
            active_target_list = target_list
            active_table[0] = table
            
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
            
        # Contenedor Principal (Sombra y redondeado)
        with ui.card().classes('w-full bg-white rounded-2xl shadow-lg p-2'):
            # 2. Pestañas estilo Wizard con Iconos
            with ui.tabs().classes('w-full text-slate-700 font-semibold mb-4 bg-slate-100 rounded-xl p-1') as tabs:
                tab_general = ui.tab('General', icon='info')
                tab_params = ui.tab('Parameters', icon='tune')
                tab_rules = ui.tab('Rules', icon='rule')
                tab_risk = ui.tab('Risk Management', icon='security')
    
            with ui.tab_panels(tabs, value=tab_general).classes('w-full bg-transparent'):
                
                # PANEL GENERAL
                with ui.tab_panel(tab_general):
                    with ui.column().classes('w-full max-w-3xl mx-auto gap-6 q-pa-md'):
                        with ui.card().classes('w-full shadow-sm border border-slate-100 rounded-xl p-6'):
                            ui.label('Información Básica').classes('text-lg font-bold text-slate-800 mb-2')
                            ui.separator().classes('mb-4')
                            strat_name_input = ui.input('Nombre de la Estrategia', value=state['strategy_name'], autocomplete=strategy_names).bind_value(state, 'strategy_name').classes('w-full text-lg')
                            ui.input('Descripción Breve', value=state['description']).bind_value(state, 'description').classes('w-full mt-4')
                
                # PANEL PARAMETERS
                with ui.tab_panel(tab_params):
                    with ui.column().classes('w-full max-w-4xl mx-auto gap-4 q-pa-md'):
                        ui.label("Parámetros Dinámicos").classes('text-xl font-bold text-slate-800')
                        ui.label("Define parámetros ajustables (ej. variables de optimización) que podrás usar en las reglas.").classes('text-sm text-slate-500 mb-2')
                        
                        params_list_container = ui.column().classes('w-full gap-3')
                        
                        def render_params():
                            params_list_container.clear()
                            with params_list_container:
                                for idx, p in enumerate(state['parameters']):
                                    with ui.card().classes('w-full flex-row items-center justify-between shadow-sm border border-slate-200 rounded-lg p-2 bg-slate-50'):
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

                                            ui.input('Nombre (Variable)', value=p['name'], on_change=mk_name_handler()).classes('flex-1').on('blur', update_risk_options)
                                            ui.select(['Entero', 'Decimal', '%', 'Lógico'], label='Tipo', value=p.get('type', 'Decimal'), on_change=mk_type_handler()).classes('w-32')
                                            ui.input('Valor por Defecto', value=str(p['value']), on_change=mk_val_handler()).classes('flex-1')
                                        ui.button(icon='delete', on_click=lambda i=idx: remove_param(i)).props('flat round color=negative').classes('ml-4')
                            update_risk_options()
                        
                        def add_param():
                            state['parameters'].append({'name': f'param_{len(state["parameters"])+1}', 'type': 'Decimal', 'value': '0'})
                            render_params()
                            
                        def remove_param(idx):
                            state['parameters'].pop(idx)
                            render_params()
                            
                        with ui.row().classes('w-full mt-4'):
                            ui.button('Añadir Nuevo Parámetro', on_click=add_param, icon='add').classes('bg-slate-800 text-white rounded-full px-6 py-2 shadow-md hover:bg-slate-700 transition-all')
                        
                # PANEL RISK MANAGEMENT
                with ui.tab_panel(tab_risk):
                    with ui.column().classes('w-full max-w-3xl mx-auto gap-6 q-pa-md'):
                        with ui.card().classes('w-full shadow-sm border border-slate-100 rounded-xl p-6'):
                            ui.label("Gestión de Riesgo (Risk Management)").classes('text-lg font-bold text-slate-800 mb-2')
                            ui.separator().classes('mb-4')
                            
                            with ui.row().classes('w-full gap-6 items-center'):
                                ui.icon('trending_up', size='2rem').classes('text-green-500')
                                tp_risk_input = ui.select([state['tp_value']], label='Take Profit (Fijo % / Param)', value=state['tp_value'], new_value_mode='add-unique').bind_value(state, 'tp_value').classes('flex-1')
                            
                            with ui.row().classes('w-full gap-6 items-center mt-4'):
                                ui.icon('trending_down', size='2rem').classes('text-red-500')
                                sl_risk_input = ui.select([state['sl_value']], label='Stop Loss (Fijo % / Param)', value=state['sl_value'], new_value_mode='add-unique').bind_value(state, 'sl_value').classes('flex-1')

                def update_risk_options():
                    opts = [p['name'] for p in state['parameters'] if p['name']]
                    
                    tp_opts = opts.copy()
                    if state['tp_value'] not in tp_opts: tp_opts.append(state['tp_value'])
                    tp_risk_input.options = tp_opts
                    
                    sl_opts = opts.copy()
                    if state['sl_value'] not in sl_opts: sl_opts.append(state['sl_value'])
                    sl_risk_input.options = sl_opts
                    tp_risk_input.update()
                    sl_risk_input.update()

                render_params()
     
                # PANEL RULES
                with ui.tab_panel(tab_rules):
                    with ui.column().classes('w-full q-pa-md max-w-5xl mx-auto gap-6'):
                        # Dirección del Trade
                        with ui.card().classes('w-full bg-slate-50 border border-slate-200 rounded-xl p-4 shadow-sm'):
                            with ui.row().classes('w-full items-center gap-4'):
                                ui.icon('swap_vert', size='1.5rem').classes('text-slate-600')
                                ui.label("Dirección Principal del Trade:").classes('font-bold text-slate-700')
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
                                ui.button('Añadir Entrada', on_click=lambda: add_rule(state['entry_rules'], entry_table), icon='add').classes('bg-green-600 text-white rounded-lg px-4 shadow-sm hover:bg-green-500')
                            
                            entry_table = ui.table(columns=columns, rows=state['entry_rules'], row_key='name').classes('w-full rounded-lg shadow-sm border border-slate-200 bg-white')
                            entry_table.add_slot('body-cell-actions', '''
                                <q-td :props="props">
                                    <q-btn flat dense round icon="delete" color="negative" @click="() => {
                                        const idx = props.rowIndex;
                                        $parent.$emit('delete_entry', idx);
                                    }" />
                                </q-td>
                            ''')
                            entry_table.on('delete_entry', lambda e: (state['entry_rules'].pop(e.args), entry_table.update()))

                        ui.separator().classes('my-4')
                        
                        # Exit Rules
                        with ui.column().classes('w-full'):
                            with ui.row().classes('w-full items-center justify-between mb-2'):
                                ui.label("Condiciones de Salida (OR)").classes('text-lg font-bold text-red-700')
                                ui.button('Añadir Salida', on_click=lambda: add_rule(state['exit_rules'], exit_table), icon='add').classes('bg-red-600 text-white rounded-lg px-4 shadow-sm hover:bg-red-500')
                            
                            exit_table = ui.table(columns=columns, rows=state['exit_rules'], row_key='name').classes('w-full rounded-lg shadow-sm border border-slate-200 bg-white')
                            exit_table.add_slot('body-cell-actions', '''
                                <q-td :props="props">
                                    <q-btn flat dense round icon="delete" color="negative" @click="() => {
                                        const idx = props.rowIndex;
                                        $parent.$emit('delete_exit', idx);
                                    }" />
                                </q-td>
                            ''')
                            exit_table.on('delete_exit', lambda e: (state['exit_rules'].pop(e.args), exit_table.update()))

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
                    catalog_table = ui.table(columns=catalog_columns, rows=[], row_key='name').classes('w-full border border-slate-200 rounded-lg shadow-sm')
                    
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
                                        tp = data.get('risk_management', {}).get('take_profit', {}).get('value', 'N/A')
                                        sl = data.get('risk_management', {}).get('stop_loss', {}).get('value', 'N/A')
                                        rows.append({
                                            'name': data.get('strategy_name', os.path.basename(f_path)),
                                            'direction': data.get('trade_direction', 'N/A'),
                                            'tp': f"{tp}%" if isinstance(tp, (int, float)) else str(tp),
                                            'sl': f"{sl}%" if isinstance(sl, (int, float)) else str(sl),
                                            'description': data.get('description', '')[:50] + "..." if len(data.get('description', '')) > 50 else data.get('description', '')
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
                                    
                                    tp = data.get('risk_management', {}).get('take_profit', {}).get('value', '0')
                                    sl = data.get('risk_management', {}).get('stop_loss', {}).get('value', '0')
                                    state['tp_value'] = str(tp)
                                    state['sl_value'] = str(sl)
                                    
                                    state['entry_rules'] = data.get('entry_conditions', {}).get('rules', [])
                                    state['exit_rules'] = data.get('exit_conditions', {}).get('rules', [])
                                    
                                    params_dict = data.get('parameters', {})
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
                                ui.notify(f"Estrategia eliminada", type='positive')
                            
                            nonlocal strategy_names
                            strategy_files = glob.glob(os.path.join(STRATEGIES_DIR, '*.yaml'))
                            strategy_names = [os.path.basename(f).replace('.yaml', '') for f in strategy_files]
                            load_catalog() # refresh table
                        except Exception as ex:
                            ui.notify(f"Error eliminando: {str(ex)}", type='negative')

                    def edit_strategy(e):
                        row = e.args
                        load_strategy_data(row['name'])
                        catalog_dialog.close()

                    catalog_table.on('edit', edit_strategy)
                    catalog_table.on('delete', delete_strategy)
                    
                    with ui.row().classes('w-full justify-end mt-4'):
                        ui.button('Cerrar', on_click=catalog_dialog.close).props('flat text-color=grey-8')

        # Acción Principal (Guardar)
        with ui.row().classes('w-full justify-center mt-8 mb-8'):
            ui.button('Guardar Estrategia', on_click=save_strategy, icon='save') \
                .classes('bg-green-600 hover:bg-green-500 text-white font-bold text-lg rounded-full shadow-xl px-12 py-3 transition-all')

        return {'load_strategy_data': load_strategy_data}
