from nicegui import ui
from .pages.strategy_builder_page import render_strategy_builder
from .pages.strategy_catalog_page import render_strategy_catalog
from .pages.market_analyzer_page import render_market_analyzer
from .pages.strategy_analyzer_page import render_strategy_analyzer
from .pages.backtest_history_page import render_backtest_history_page
from .pages.ml_page import render_ml_page
from .pages.live_monitor_page import render_live_monitor_page

def create_gui(app):
    """
    Integra la interfaz de NiceGUI en la aplicación FastAPI existente.
    """
    
    @ui.page('/')
    def dashboard():
        ui.colors(primary='#1e40af', secondary='#0f172a', accent='#f59e0b')
        
        # Header oscuro premium
        with ui.header().classes('bg-slate-900 text-white justify-between items-center px-6 py-4 shadow-md border-b border-slate-800'):
            with ui.row().classes('items-center gap-3'):
                ui.icon('rocket_launch', size='2rem').classes('text-blue-500')
                ui.label('Trading Quant App').classes('text-2xl font-bold tracking-tight')
            ui.button('Configuración', icon='settings').props('flat round text-color=white').classes('hover:bg-slate-800 transition-colors')
            
        with ui.column().classes('w-full q-pa-sm'):
            # Contenedor principal de pestañas flotantes
            with ui.card().classes('w-full bg-white rounded-2xl shadow-lg p-3'):
                
                with ui.tabs().classes('w-full text-slate-700 font-semibold mb-2 bg-slate-100 rounded-xl p-1 gap-2') as tabs:
                    tab_builder = ui.tab('Strategy Builder', icon='build')
                    tab_catalog = ui.tab('Estrategias Guardadas', icon='list')
                    tab_analyzer = ui.tab('Strategy Analyzer', icon='analytics')
                    tab_history = ui.tab('Historial de Backtests', icon='history')
                    tab_market = ui.tab('Datos Almacenados', icon='storage')
                    tab_ml = ui.tab('Machine Learning', icon='psychology')
                    tab_live = ui.tab('Live Monitor', icon='play_circle')
                    
                with ui.tab_panels(tabs, value=tab_builder).classes('w-full bg-transparent p-0') as panels:
                    with ui.tab_panel(tab_builder).classes('p-0 mt-4'):
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
                        def on_back_to_builder():
                            panels.value = tab_builder
                        analyzer_state = render_strategy_analyzer(on_back_to_builder=on_back_to_builder)

                    with ui.tab_panel(tab_history):
                        def on_load_to_analyzer(row):
                            if analyzer_state and 'load_from_history' in analyzer_state:
                                analyzer_state['load_from_history'](row)
                            panels.value = tab_analyzer

                        def on_open_portfolio(row=None):
                            if analyzer_state:
                                if row and 'load_from_history' in analyzer_state:
                                    analyzer_state['load_from_history'](row)
                                if 'open_portfolio_modal' in analyzer_state:
                                    analyzer_state['open_portfolio_modal']()
                            panels.value = tab_analyzer

                        render_backtest_history_page(
                            on_load_in_analyzer=on_load_to_analyzer,
                            on_open_portfolio=on_open_portfolio
                        )
                        
                    with ui.tab_panel(tab_market):
                        render_market_analyzer()
                        
                    with ui.tab_panel(tab_ml):
                        render_ml_page()

                    with ui.tab_panel(tab_live):
                        render_live_monitor_page()

    # Run NiceGUI over the existing FastAPI app
    ui.run_with(
        app,
        title='Trading Quant',
        favicon='📈',
        # El puerto se manejará desde Uvicorn en start.bat
    )
