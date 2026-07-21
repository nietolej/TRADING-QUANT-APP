from nicegui import ui
import yaml
import os
import glob

def render_strategy_builder():
    with ui.column().classes('w-full q-pa-md'):
        ui.label('Strategy Builder').classes('text-2xl font-bold text-primary q-mb-md')
        
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
        
        strategy_files = glob.glob("config/strategies/*.yaml")
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
            
            filename = f"config/strategies/{state['strategy_name'].lower().replace(' ', '_')}.yaml"
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            try:
                with open(filename, 'w') as f:
                    yaml.dump(config, f, default_flow_style=False, sort_keys=False)
                    

                ui.notify(f"Estrategia guardada en {filename}", type='positive')
            except Exception as e:
                ui.notify(f"Error guardando: {str(e)}", type='negative')

        import time
        active_target_list = []
        active_table = [None]

        # Dialog for adding a rule
        with ui.dialog() as rule_dialog, ui.card().classes('w-[500px] q-pa-md'):
            ui.label('Add Condition').classes('text-xl font-bold q-mb-md')
            
            rule_type = ui.select(['technical_indicator', 'onchain_threshold'], label='Type', value='technical_indicator').classes('w-full mt-2')
            
            # Technical indicator fields container
            tech_container = ui.column().classes('w-full')
            with tech_container:
                ind1_select = ui.select(['Price', 'SMA', 'EMA', 'Volume'], label='Fast Indicator (Ind 1)', value='EMA').classes('w-full mt-1')
                p1_input = ui.select(['20'], label='Fast Period (Period 1)', value='20', new_value_mode='add-unique').classes('w-full mt-1')
                op_select = ui.select(['crosses_above', 'crosses_below', 'is_above', 'is_below'], label='Operator', value='crosses_above').classes('w-full mt-1')
                ind2_select = ui.select(['Price', 'SMA', 'EMA', 'Volume'], label='Slow Indicator (Ind 2)', value='EMA').classes('w-full mt-1')
                p2_input = ui.select(['50'], label='Slow Period (Period 2)', value='50', new_value_mode='add-unique').classes('w-full mt-1')
                
            # On-chain fields container
            onchain_container = ui.column().classes('w-full')
            with onchain_container:
                metric_input = ui.input('Metric Name', value='active_addresses').classes('w-full mt-1')
                cond_select = ui.select(['above', 'below', 'increasing'], label='Condition', value='above').classes('w-full mt-1')
                val_input = ui.select(['0'], label='Value / Lookback Days', value='0', new_value_mode='add-unique').classes('w-full mt-1')
                
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
                    if cond_select.value == 'increasing':
                        new_rule["lookback_days"] = _parse_val(val_input.value)
                        new_rule["min_change_pct"] = 0
                    new_rule['details'] = f"OnChain({new_rule.get('metric')}) {new_rule.get('condition')} ({new_rule.get('value')})"
                
                active_target_list.append(new_rule)
                if active_table[0]:
                    active_table[0].rows = active_target_list[:]
                    active_table[0].update()
                rule_dialog.close()
                
            ui.button('Add Condition', on_click=save_rule).classes('w-full mt-4 bg-blue-600 text-white font-bold')
            ui.button('Cancel', on_click=rule_dialog.close).classes('w-full mt-2')

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
            
        with ui.tabs() as tabs:
            tab_general = ui.tab('1. General')
            tab_params = ui.tab('2. Parameters')
            tab_rules = ui.tab('3. Rules')
            tab_risk = ui.tab('4. Risk Management')
 
        with ui.tab_panels(tabs, value=tab_general).classes('w-full border rounded-lg p-4 bg-gray-50'):
            with ui.tab_panel(tab_general):
                with ui.row().classes('w-full gap-4'):
                    strat_name_input = ui.input('Strategy Name', value=state['strategy_name'], autocomplete=strategy_names).bind_value(state, 'strategy_name').classes('w-full')
                ui.input('Description', value=state['description']).bind_value(state, 'description').classes('w-full mt-4')
 
            with ui.tab_panel(tab_params):
                ui.label("Strategy Parameters").classes('font-bold mt-2')
                ui.label("Define parameters to be used in your rules.").classes('text-sm text-gray-600 mb-4')
                
                params_list_container = ui.column().classes('w-full gap-2')
                
                def render_params():
                    params_list_container.clear()
                    with params_list_container:
                        for idx, p in enumerate(state['parameters']):
                            with ui.row().classes('w-full items-center gap-4'):
                                ui.input('Parameter Name', value=p['name']).bind_value(p, 'name').classes('flex-1').on('blur', update_risk_options)
                                ui.select(['Entero', 'Decimal', '%', 'Lógico'], label='Type', value=p.get('type', 'Decimal')).bind_value(p, 'type').classes('w-32')
                                ui.input('Default Value', value=str(p['value'])).bind_value(p, 'value').classes('flex-1')
                                ui.button(icon='delete', color='negative', on_click=lambda i=idx: remove_param(i)).props('flat round size=sm')
                    update_risk_options()
                
                def add_param():
                    state['parameters'].append({'name': f'param_{len(state["parameters"])+1}', 'type': 'Decimal', 'value': '0'})
                    render_params()
                    
                def remove_param(idx):
                    state['parameters'].pop(idx)
                    render_params()
                    
                ui.button('Add Parameter', on_click=add_param).classes('mt-4')
                
                # We defer render_params() to be called after risk tab is defined so we can update its options

            with ui.tab_panel(tab_risk):
                ui.label("Fixed Risk Settings").classes('font-bold mt-2')
                with ui.row().classes('w-full gap-4 mt-4'):
                    tp_risk_input = ui.select([state['tp_value']], label='Take Profit (%)', value=state['tp_value'], new_value_mode='add-unique').bind_value(state, 'tp_value').classes('flex-1')
                    sl_risk_input = ui.select([state['sl_value']], label='Stop Loss (%)', value=state['sl_value'], new_value_mode='add-unique').bind_value(state, 'sl_value').classes('flex-1')

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

            # Render params now that risk elements exist
            render_params()
 
            with ui.tab_panel(tab_rules):
                with ui.row().classes('w-full items-center gap-4 mb-4'):
                    ui.label("Dirección / Acción:").classes('font-bold')
                    ui.select(['Long', 'Short'], value=state['direction']).bind_value(state, 'direction').classes('w-32')

                ui.label("Entry Rules").classes('font-bold mt-2')
                columns = [
                    {'name': 'type', 'label': 'Type', 'field': 'type', 'sortable': True},
                    {'name': 'details', 'label': 'Details / Definition', 'field': 'details', 'sortable': True},
                ]
                entry_table = ui.table(columns=columns, rows=state['entry_rules'], row_key='name').classes('w-full')
                ui.button('Add Entry Rule', on_click=lambda: add_rule(state['entry_rules'], entry_table)).classes('mt-2')
                
                ui.separator().classes('my-4')
                ui.label("Exit Rules").classes('font-bold mt-2')
                exit_table = ui.table(columns=columns, rows=state['exit_rules'], row_key='name').classes('w-full')
                ui.button('Add Exit Rule', on_click=lambda: add_rule(state['exit_rules'], exit_table)).classes('mt-2')

        with ui.dialog() as catalog_dialog, ui.card().classes('w-[800px] max-w-4xl q-pa-md'):
            ui.label('Catálogo de Estrategias').classes('text-xl font-bold q-mb-md')
            catalog_columns = [
                {'name': 'name', 'label': 'Nombre', 'field': 'name', 'sortable': True},
                {'name': 'direction', 'label': 'Dirección', 'field': 'direction', 'sortable': True},
                {'name': 'tp', 'label': 'Take Profit', 'field': 'tp', 'sortable': True},
                {'name': 'sl', 'label': 'Stop Loss', 'field': 'sl', 'sortable': True},
                {'name': 'description', 'label': 'Descripción', 'field': 'description', 'sortable': True},
                {'name': 'actions', 'label': 'Acciones', 'field': 'actions'},
            ]
            catalog_table = ui.table(columns=catalog_columns, rows=[], row_key='name').classes('w-full')
            
            catalog_table.add_slot('body-cell-actions', '''
                <q-td :props="props">
                    <q-btn flat dense round icon="edit" color="primary" @click="() => $parent.$emit('edit', props.row)" />
                    <q-btn flat dense round icon="delete" color="negative" @click="() => $parent.$emit('delete', props.row)" />
                </q-td>
            ''')
            
            def load_catalog():
                rows = []
                for f_path in glob.glob("config/strategies/*.yaml"):
                    try:
                        with open(f_path, 'r') as f:
                            data = yaml.safe_load(f)
                            if data:
                                tp = data.get('risk_management', {}).get('take_profit', {}).get('value', 'N/A')
                                sl = data.get('risk_management', {}).get('stop_loss', {}).get('value', 'N/A')
                                rows.append({
                                    'name': data.get('strategy_name', os.path.basename(f_path)),
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
                
            def edit_strategy(e):
                row = e.args
                strategy_name = row['name']
                f_path = f"config/strategies/{strategy_name.lower().replace(' ', '_')}.yaml"
                try:
                    with open(f_path, 'r') as f:
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
                            
                            ui.notify(f"Estrategia {strategy_name} cargada para editar", type='info')
                            catalog_dialog.close()
                except Exception as ex:
                    ui.notify(f"Error cargando estrategia: {str(ex)}", type='negative')

            def delete_strategy(e):
                row = e.args
                strategy_name = row['name']
                f_path = f"config/strategies/{strategy_name.lower().replace(' ', '_')}.yaml"
                try:
                    if os.path.exists(f_path):
                        os.remove(f_path)
                        ui.notify(f"Estrategia {strategy_name} eliminada", type='positive')
                        load_catalog()
                except Exception as ex:
                    ui.notify(f"Error eliminando estrategia: {str(ex)}", type='negative')

            catalog_table.on('edit', edit_strategy)
            catalog_table.on('delete', delete_strategy)
            
            ui.button('Cerrar', on_click=catalog_dialog.close).classes('mt-4')

        with ui.row().classes('w-full justify-between q-mt-lg'):
            ui.button('Ver Estrategias Configuradas', on_click=load_catalog, icon='list').classes('px-8 py-2 bg-blue-500 text-white')
            ui.button('Save Strategy', on_click=save_strategy, color='positive').classes('px-8 py-2')
