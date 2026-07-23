from nicegui import ui
from .pages.strategy_builder_page import render_strategy_builder
from .pages.strategy_catalog_page import render_strategy_catalog
from .pages.market_analyzer_page import render_market_analyzer
from .pages.strategy_analyzer_page import render_strategy_analyzer
from .pages.ml_page import render_ml_page

def create_gui(app):
    """
    Integra la interfaz de NiceGUI en la aplicación FastAPI existente.
    """
    
    @ui.page('/')
    def dashboard():
        ui.colors(primary='#1e40af', secondary='#0f172a', accent='#f59e0b')
        
        with ui.header().classes('bg-secondary text-white justify-between items-center q-pa-md'):
            ui.label('Trading Quant App - Web Version').classes('text-2xl font-bold')
            ui.button('Configuración', icon='settings', color='transparent').classes('text-white')
            
        with ui.row().classes('w-full q-px-md q-pt-md'):
            with ui.card().classes('w-full q-pa-none'):
                
                with ui.tabs().classes('w-full bg-gray-100 text-gray-700 font-bold') as tabs:
                    tab_builder = ui.tab('Strategy Builder', icon='build')
                    tab_catalog = ui.tab('Estrategias Guardadas', icon='list')
                    tab_analyzer = ui.tab('Strategy Analyzer', icon='analytics')
                    tab_market = ui.tab('Datos Almacenados', icon='storage')
                    tab_ml = ui.tab('Machine Learning', icon='psychology')
                    
                ui.separator()
                    
                with ui.tab_panels(tabs, value=tab_builder).classes('w-full') as panels:
                    with ui.tab_panel(tab_builder):
                        builder_state = render_strategy_builder()
                        
                    with ui.tab_panel(tab_catalog):
                        def on_edit(row):
                            if builder_state and 'load_strategy_data' in builder_state:
                                builder_state['load_strategy_data'](row['name'])
                            panels.value = tab_builder

                        def on_analyze(row):
                            if analyzer_state and 'select_strategy' in analyzer_state:
                                analyzer_state['select_strategy'](row.get('filename'))
                            panels.value = tab_analyzer

                        render_strategy_catalog(on_edit_strategy=on_edit, on_select_strategy=on_analyze)

                    with ui.tab_panel(tab_analyzer):
                        analyzer_state = render_strategy_analyzer()
                        
                    with ui.tab_panel(tab_market):
                        render_market_analyzer()
                        
                    with ui.tab_panel(tab_ml):
                        render_ml_page()

    # Run NiceGUI over the existing FastAPI app
    ui.run_with(
        app,
        title='Trading Quant',
        favicon='📈',
        # El puerto se manejará desde Uvicorn en start.bat
    )
