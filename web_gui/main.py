from nicegui import ui
from .pages.strategy_builder_page import render_strategy_builder
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
            
        with ui.row().classes('w-full justify-center q-pt-xl'):
            with ui.card().classes('w-full max-w-6xl q-pa-none'):
                
                with ui.tabs().classes('w-full bg-gray-100 text-gray-700 font-bold') as tabs:
                    tab_builder = ui.tab('Strategy Builder', icon='build')
                    tab_analyzer = ui.tab('Strategy Analyzer', icon='analytics')
                    tab_market = ui.tab('Market Analyzer', icon='dataset')
                    tab_ml = ui.tab('Machine Learning', icon='psychology')
                    
                ui.separator()
                    
                with ui.tab_panels(tabs, value=tab_builder).classes('w-full'):
                    with ui.tab_panel(tab_builder):
                        render_strategy_builder()
                        
                    with ui.tab_panel(tab_analyzer):
                        render_strategy_analyzer()
                        
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
