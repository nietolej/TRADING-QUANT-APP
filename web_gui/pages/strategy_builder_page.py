from nicegui import ui
import yaml
import os

def render_strategy_builder():
    with ui.column().classes('w-full q-pa-md'):
        ui.label('Strategy Builder').classes('text-2xl font-bold text-primary q-mb-md')
        
        # State
        state = {
            'strategy_name': 'MyStrategy',
            'description': '',
            'symbol': 'BTC/USDT',
            'timeframe': '4h',
            'direction': 'Long',
            'tp_type': 'Percentage',
            'tp_value': 5.0,
            'sl_type': 'Percentage',
            'sl_value': 2.0,
            'entry_rules': [],
            'exit_rules': []
        }

        def save_strategy():
            config = {
                "strategy_name": state['strategy_name'],
                "description": state['description'],
                "symbol": state['symbol'],
                "timeframe": state['timeframe'],
                "trade_direction": state['direction'],
                "risk_management": {
                    "take_profit": {
                        "type": state['tp_type'].lower().replace(" ", "_"),
                        "value": state['tp_value']
                    },
                    "stop_loss": {
                        "type": state['sl_type'].lower().replace(" ", "_"),
                        "value": state['sl_value']
                    }
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
        def add_rule(target_list, table):
            target_list.append({
                "name": f"rule_{int(time.time() * 1000)}",
                "type": "technical_indicator",
                "indicator1": "EMA",
                "period1": 20,
                "operator": "crosses_above",
                "indicator2": "EMA",
                "period2": 50
            })
            table.rows = target_list[:]
            table.update()
            
        with ui.tabs() as tabs:
            tab_general = ui.tab('1. General')
            tab_risk = ui.tab('2. Risk Management')
            tab_rules = ui.tab('3. Rules')

        with ui.tab_panels(tabs, value=tab_general).classes('w-full border rounded-lg p-4 bg-gray-50'):
            with ui.tab_panel(tab_general):
                with ui.row().classes('w-full gap-4'):
                    ui.input('Strategy Name', value=state['strategy_name']).bind_value(state, 'strategy_name').classes('flex-1')
                    ui.input('Symbol', value=state['symbol']).bind_value(state, 'symbol').classes('flex-1')
                with ui.row().classes('w-full gap-4 mt-4'):
                    ui.select(['1m', '5m', '15m', '1h', '4h', '1d'], label='Timeframe', value=state['timeframe']).bind_value(state, 'timeframe').classes('flex-1')
                    ui.select(['Long', 'Short'], label='Direction', value=state['direction']).bind_value(state, 'direction').classes('flex-1')
                ui.input('Description', value=state['description']).bind_value(state, 'description').classes('w-full mt-4')

            with ui.tab_panel(tab_risk):
                with ui.row().classes('w-full gap-4'):
                    ui.select(['None', 'Percentage', 'Fixed Price'], label='Take Profit Type', value=state['tp_type']).bind_value(state, 'tp_type').classes('flex-1')
                    ui.number('Take Profit Value', value=state['tp_value']).bind_value(state, 'tp_value').classes('flex-1')
                with ui.row().classes('w-full gap-4 mt-4'):
                    ui.select(['None', 'Percentage', 'Trailing Percent', 'Chandelier'], label='Stop Loss Type', value=state['sl_type']).bind_value(state, 'sl_type').classes('flex-1')
                    ui.number('Stop Loss Value', value=state['sl_value']).bind_value(state, 'sl_value').classes('flex-1')

            with ui.tab_panel(tab_rules):
                ui.label("Entry Rules").classes('font-bold mt-2')
                # A simple representation of rules
                columns = [
                    {'name': 'type', 'label': 'Type', 'field': 'type'},
                    {'name': 'ind1', 'label': 'Ind 1', 'field': 'indicator1'},
                    {'name': 'op', 'label': 'Op', 'field': 'operator'},
                    {'name': 'ind2', 'label': 'Ind 2', 'field': 'indicator2'},
                ]
                entry_table = ui.table(columns=columns, rows=state['entry_rules'], row_key='name').classes('w-full')
                ui.button('Add Entry Rule', on_click=lambda: add_rule(state['entry_rules'], entry_table)).classes('mt-2')
                
                ui.separator().classes('my-4')
                ui.label("Exit Rules").classes('font-bold mt-2')
                exit_table = ui.table(columns=columns, rows=state['exit_rules'], row_key='name').classes('w-full')
                ui.button('Add Exit Rule', on_click=lambda: add_rule(state['exit_rules'], exit_table)).classes('mt-2')

        with ui.row().classes('w-full justify-end q-mt-lg'):
            ui.button('Save Strategy', on_click=save_strategy, color='positive').classes('px-8 py-2')
