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
        # Dark Quant Terminal Aesthetic
        ui.dark_mode().enable()
        ui.colors(primary='#06b6d4', secondary='#0f172a', accent='#f59e0b') # Cyan-500, Slate-900, Amber-500
        
        # Header oscuro premium con glassmorphism
        with ui.header().classes('bg-slate-900/80 backdrop-blur-md text-white justify-between items-center px-6 py-4 shadow-md border-b border-slate-700'):
            with ui.row().classes('items-center gap-3'):
                ui.icon('rocket_launch', size='2rem').classes('text-cyan-400')
                ui.label('Trading Quant Terminal').classes('text-2xl font-bold tracking-tight')
            ui.button('Configuración', icon='settings').props('flat round text-color=white').classes('hover:bg-slate-800 transition-colors')
            
        # Contenedores de las páginas (mantienen estado al ocultarse en lugar de destruirse)
        pages = {}
        
        def show_page(page_name):
            for name, container in pages.items():
                container.set_visibility(name == page_name)
        
        # Sidebar izquierdo
        with ui.left_drawer(value=True).classes('bg-slate-900 text-slate-300 border-r border-slate-800 p-4 shadow-xl') as drawer:
            ui.label('MENÚ PRINCIPAL').classes('text-xs font-bold text-slate-500 mb-4 tracking-widest')
            
            def menu_item(text, icon, page_id):
                btn = ui.button(text, icon=icon, on_click=lambda: show_page(page_id))
                btn.props('flat align="left"').classes('w-full text-slate-300 hover:text-white hover:bg-slate-800 transition-all justify-start py-3')
                return btn
                
            menu_item('Strategy Builder', 'build', 'builder')
            menu_item('Estrategias Guardadas', 'list', 'catalog')
            menu_item('Strategy Analyzer', 'analytics', 'analyzer')
            menu_item('Historial Backtests', 'history', 'history')
            ui.separator().classes('my-4 bg-slate-700')
            ui.label('HERRAMIENTAS AVANZADAS').classes('text-xs font-bold text-slate-500 mb-4 tracking-widest')
            menu_item('Datos Almacenados', 'storage', 'market')
            menu_item('Machine Learning', 'psychology', 'ml')
            menu_item('Live Monitor', 'play_circle', 'live')

        # Contenedor principal
        with ui.column().classes('w-full h-full p-4 bg-slate-950'):
            
            # --- Instanciar Páginas ---
            with ui.column().classes('w-full h-full') as pages['builder']:
                builder_state = render_strategy_builder()
                
            with ui.column().classes('w-full h-full') as pages['catalog']:
                def on_edit(row):
                    if builder_state and 'load_strategy_data' in builder_state:
                        builder_state['load_strategy_data'](row['name'])
                    show_page('builder')

                def on_analyze(row):
                    if analyzer_state and 'select_strategy' in analyzer_state:
                        analyzer_state['select_strategy'](row.get('filename'))
                    show_page('analyzer')
                render_strategy_catalog(on_edit_strategy=on_edit, on_select_strategy=on_analyze)

            with ui.column().classes('w-full h-full') as pages['analyzer']:
                def on_back_to_builder():
                    show_page('builder')
                analyzer_state = render_strategy_analyzer(on_back_to_builder=on_back_to_builder)

            with ui.column().classes('w-full h-full') as pages['history']:
                def on_load_to_analyzer(row):
                    if analyzer_state and 'load_from_history' in analyzer_state:
                        analyzer_state['load_from_history'](row)
                    show_page('analyzer')

                def on_open_portfolio(row=None):
                    if analyzer_state:
                        if row and 'load_from_history' in analyzer_state:
                            analyzer_state['load_from_history'](row)
                        if 'open_portfolio_modal' in analyzer_state:
                            analyzer_state['open_portfolio_modal']()
                    show_page('analyzer')

                render_backtest_history_page(on_load_in_analyzer=on_load_to_analyzer, on_open_portfolio=on_open_portfolio)
                
            with ui.column().classes('w-full h-full') as pages['market']:
                render_market_analyzer()
                
            with ui.column().classes('w-full h-full') as pages['ml']:
                render_ml_page()

            with ui.column().classes('w-full h-full') as pages['live']:
                render_live_monitor_page()
                
        # Mostrar builder por defecto
        show_page('builder')

    # Run NiceGUI over the existing FastAPI app
    ui.run_with(
        app,
        title='Trading Quant',
        favicon='📈',
        # El puerto se manejará desde Uvicorn en start.bat
    )
