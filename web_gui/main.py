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
        
        ui.add_head_html('''
            <style>
            /* Sticky Header & Estilizado de Tablas */
            .q-table__middle {
                max-height: 520px;
                overflow-y: auto !important;
            }
            .q-table thead tr th {
                position: sticky !important;
                top: 0 !important;
                z-index: 20 !important;
                background-color: #0f172a !important;
                box-shadow: 0 2px 4px rgba(0,0,0,0.4);
                font-size: 0.8125rem !important;
                font-weight: 700 !important;
                letter-spacing: 0.03em;
                color: #cbd5e1 !important;
                padding: 8px 10px !important;
            }
            .q-table tbody td {
                font-size: 0.875rem !important; /* 14px */
                font-weight: 500 !important;
                padding: 6px 10px !important;
            }
            </style>
        ''')
        
        # Header oscuro premium con glassmorphism
        with ui.header().classes('bg-slate-900/80 backdrop-blur-md text-white justify-between items-center px-6 py-4 shadow-md border-b border-slate-700'):
            with ui.row().classes('items-center gap-3'):
                ui.button(icon='menu', on_click=lambda: drawer.toggle()).props('flat round text-color=white').classes('hover:bg-slate-800 transition-colors').tooltip('Contraer / Expandir Menú')
                ui.icon('rocket_launch', size='2rem').classes('text-cyan-400')
                ui.label('Trading Quant Terminal').classes('text-2xl font-bold tracking-tight')
            ui.button('Configuración', icon='settings').props('flat round text-color=white').classes('hover:bg-slate-800 transition-colors')
            
        # Contenedores de las páginas (mantienen estado al ocultarse en lugar de destruirse)
        pages = {}
        
        def show_page(page_name):
            for name, container in pages.items():
                container.set_visibility(name == page_name)
        
        # Sidebar izquierdo ajustable en ancho
        with ui.left_drawer(value=True).classes('bg-slate-900 text-slate-300 border-r border-slate-800 p-4 shadow-xl relative').props('width=280') as drawer:
            ui.html('''
                <div id="drawer-resizer" style="
                    position: absolute;
                    top: 0;
                    right: -3px;
                    width: 8px;
                    height: 100%;
                    cursor: col-resize;
                    z-index: 1000;
                    background: transparent;
                    transition: background 0.2s;
                " title="Arrastra el borde para cambiar el ancho del menú (Doble clic para restablecer 280px)">
                </div>
            ''')
            ui.add_body_html('''
                <script>
                (function() {
                    const setupResizer = () => {
                        const resizer = document.getElementById('drawer-resizer');
                        if (!resizer) return;
                        let isResizing = false;
                        const drawerEl = resizer.closest('.q-drawer');
                        
                        const setWidth = (w) => {
                            if (drawerEl) drawerEl.style.width = w + 'px';
                            const pc = document.querySelector('.q-page-container');
                            if (pc) pc.style.paddingLeft = w + 'px';
                        };

                        resizer.addEventListener('mouseover', () => { resizer.style.background = '#06b6d4'; });
                        resizer.addEventListener('mouseout', () => { if (!isResizing) resizer.style.background = 'transparent'; });

                        resizer.addEventListener('mousedown', function(e) {
                            isResizing = true;
                            resizer.style.background = '#06b6d4';
                            document.body.style.cursor = 'col-resize';
                            document.body.style.userSelect = 'none';
                        });
                        
                        document.addEventListener('mousemove', function(e) {
                            if (!isResizing || !drawerEl) return;
                            const rect = drawerEl.getBoundingClientRect();
                            const newWidth = e.clientX - rect.left;
                            if (newWidth >= 160 && newWidth <= 650) {
                                setWidth(newWidth);
                                window.dispatchEvent(new Event('resize'));
                            }
                        });
                        
                        document.addEventListener('mouseup', function(e) {
                            if (isResizing) {
                                isResizing = false;
                                resizer.style.background = 'transparent';
                                document.body.style.cursor = '';
                                document.body.style.userSelect = '';
                                window.dispatchEvent(new Event('resize'));
                            }
                        });

                        resizer.addEventListener('dblclick', function() {
                            setWidth(280);
                            window.dispatchEvent(new Event('resize'));
                        });
                    };
                    setTimeout(setupResizer, 300);
                })();
                </script>
            ''')

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
