from nicegui import ui
import yaml
import os
import glob

# Ruta absoluta a la raíz del proyecto (2 niveles arriba de este archivo)
_PAGE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(_PAGE_DIR, '..', '..'))
STRATEGIES_DIR = os.path.join(BASE_DIR, 'config', 'strategies')

def render_strategy_catalog(on_edit_strategy=None, on_select_strategy=None):
    with ui.column().classes('w-full q-pa-md'):
        ui.label('Catálogo de Estrategias').classes('text-2xl font-bold text-primary q-mb-md')
        
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
                <q-btn flat dense round icon="edit" color="primary" @click="() => $parent.$emit('edit', props.row)" title="Editar" />
                <q-btn flat dense round icon="play_arrow" color="positive" @click="() => $parent.$emit('analyze', props.row)" title="Analizar" />
                <q-btn flat dense round icon="delete" color="negative" @click="() => $parent.$emit('delete', props.row)" title="Eliminar" />
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
                                'filename': os.path.basename(f_path),
                                'direction': data.get('trade_direction', 'N/A'),
                                'tp': f"{tp}%" if isinstance(tp, (int, float)) else str(tp),
                                'sl': f"{sl}%" if isinstance(sl, (int, float)) else str(sl),
                                'description': data.get('description', '')
                            })
                except Exception:
                    pass
            catalog_table.rows = rows
            catalog_table.update()
            
        def handle_edit(e):
            row = e.args
            if on_edit_strategy:
                on_edit_strategy(row)
                
        def handle_analyze(e):
            row = e.args
            if on_select_strategy:
                on_select_strategy(row)

        def handle_delete(e):
            row = e.args
            strategy_name = row['name']
            filename_hint = row.get('filename', '')

            # 1. Buscar por filename exacto (dato más fiable que viene del catálogo)
            matched_file = None
            if filename_hint:
                candidate = os.path.join(STRATEGIES_DIR, filename_hint)
                if os.path.exists(candidate):
                    matched_file = candidate

            # 2. Buscar abriendo cada YAML y comparando strategy_name
            if not matched_file:
                for f_path in glob.glob(os.path.join(STRATEGIES_DIR, '*.yaml')):
                    try:
                        with open(f_path, 'r', encoding='utf-8') as f:
                            data = yaml.safe_load(f)
                            if data and data.get('strategy_name') == strategy_name:
                                matched_file = f_path
                                break
                    except Exception:
                        pass

            # 3. Fallback por convención de nombre de archivo
            if not matched_file:
                matched_file = os.path.join(STRATEGIES_DIR, f"{strategy_name.lower().replace(' ', '_')}.yaml")

            try:
                if os.path.exists(matched_file):
                    os.remove(matched_file)
                    ui.notify(f"Estrategia '{strategy_name}' eliminada correctamente", type='positive')
                else:
                    ui.notify(
                        f"No se encontró el archivo de la estrategia '{strategy_name}' "
                        f"(buscado en: {matched_file})",
                        type='warning'
                    )
                load_catalog()
            except Exception as ex:
                ui.notify(f"Error eliminando estrategia: {str(ex)}", type='negative')

        catalog_table.on('edit', handle_edit)
        catalog_table.on('analyze', handle_analyze)
        catalog_table.on('delete', handle_delete)
        
        load_catalog()
        
        with ui.row().classes('w-full mt-4 justify-end'):
            ui.button('Refrescar Lista', icon='refresh', on_click=load_catalog).classes('bg-gray-600 text-white')
