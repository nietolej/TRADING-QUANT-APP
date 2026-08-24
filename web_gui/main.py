from nicegui import ui
from .pages.strategy_builder_page import render_strategy_builder
from .pages.strategy_catalog_page import render_strategy_catalog
from .pages.market_analyzer_page import render_market_analyzer
from .pages.strategy_analyzer_page import render_strategy_analyzer
from .pages.backtest_history_page import render_backtest_history_page
from .pages.ml_page import render_ml_page
from .pages.live_monitor_page import render_live_monitor_page
from .pages.mle_thermometer_page import render_mle_thermometer_page

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
            /* Ocultar barra de scroll para el menú horizontal */
            .hide-scrollbar::-webkit-scrollbar {
                display: none;
            }
            .hide-scrollbar {
                -ms-overflow-style: none;
                scrollbar-width: none;
            }
            </style>
            <script>
            // ─────────────────────────────────────────────────────────────────
            // FIX: Previene que la tecla Enter en un <input> o <input[type=number]>
            // dispare el primer <button> del DOM (que pertenece a otra página oculta).
            // Todas las páginas conviven en el mismo DOM (sólo se ocultan con
            // visibility/display), por lo que sin esta protección el Enter en el
            // Analyzer dispararía "Guardar Estrategia" del Builder.
            // ─────────────────────────────────────────────────────────────────
            document.addEventListener('keydown', function(e) {
                if (e.key !== 'Enter') return;
                var el = e.target;
                // Sólo actuar en inputs de texto y numéricos (no en textareas ni selects)
                if (el.tagName !== 'INPUT') return;
                var t = (el.type || '').toLowerCase();
                if (t === 'textarea') return;
                // Permitir Enter en q-btn o si el input está dentro de un dialog abierto
                // (los dialogs son portales y se manejan correctamente por Quasar)
                var inDialog = el.closest('.q-dialog');
                if (inDialog) return;
                // Prevenir submit/trigger por defecto
                e.preventDefault();
                e.stopPropagation();
            }, true); // capture phase

            // Además: asegurar que todos los <button> sin type explícito
            // se traten como type="button" y no como type="submit".
            document.addEventListener('DOMContentLoaded', function() {
                document.querySelectorAll('button:not([type])').forEach(function(btn) {
                    btn.setAttribute('type', 'button');
                });
            });

            // MutationObserver para botones añadidos dinámicamente por NiceGUI/Vue
            var _btnObserver = new MutationObserver(function(mutations) {
                mutations.forEach(function(m) {
                    m.addedNodes.forEach(function(node) {
                        if (node.nodeType !== 1) return;
                        if (node.tagName === 'BUTTON' && !node.getAttribute('type')) {
                            node.setAttribute('type', 'button');
                        }
                        node.querySelectorAll && node.querySelectorAll('button:not([type])').forEach(function(btn) {
                            btn.setAttribute('type', 'button');
                        });
                    });
                });
            });
            _btnObserver.observe(document.body, { childList: true, subtree: true });
            </script>
        ''')
        
        # Contenedores de las páginas (mantienen estado al ocultarse en lugar de destruirse)
        pages = {}
        live_page = None
        
        def show_page(page_name):
            for name, container in pages.items():
                container.set_visibility(name == page_name)
            # Persistir la página activa en localStorage del navegador
            ui.run_javascript(f"localStorage.setItem('tqa_active_page', '{page_name}');")

        # Header oscuro premium con glassmorphism y menú horizontal
        with ui.header().classes('bg-slate-900/95 backdrop-blur-md text-white shadow-md border-b border-slate-700'):
            # Fila Superior: Título y Configuración
            with ui.row().classes('w-full justify-between items-center px-6 py-2 border-b border-slate-800'):
                with ui.row().classes('items-center gap-3'):
                    ui.icon('rocket_launch', size='2rem').classes('text-cyan-400')
                    ui.label('Trading Quant Terminal').classes('text-xl md:text-2xl font-bold tracking-tight')
                ui.button('Configuración', icon='settings').props('flat round text-color=white').classes('hover:bg-slate-800 transition-colors')
            
            # Fila Inferior: Menú Horizontal
            with ui.row().classes('w-full items-center px-4 py-1 gap-1 overflow-x-auto no-wrap hide-scrollbar'):
                def menu_item(text, icon, page_id):
                    btn = ui.button(text, icon=icon, on_click=lambda p=page_id: show_page(p))
                    btn.props('flat no-caps').classes('text-slate-300 hover:text-white hover:bg-slate-800 transition-all text-sm py-1 px-3 rounded whitespace-nowrap')
                    return btn
                
                ui.label('PRINCIPAL').classes('text-[10px] font-bold text-slate-500 tracking-widest mx-2')
                menu_item('Strategy Builder', 'build', 'builder')
                menu_item('Estrategias Guardadas', 'list', 'catalog')
                menu_item('Strategy Analyzer', 'analytics', 'analyzer')
                menu_item('Historial Backtests', 'history', 'history')
                
                ui.separator().props('vertical').classes('mx-2 h-5 bg-slate-700')
                
                ui.label('AVANZADAS').classes('text-[10px] font-bold text-slate-500 tracking-widest mx-2')
                menu_item('Datos Almacenados', 'storage', 'market')
                menu_item('Machine Learning', 'psychology', 'ml')
                menu_item('Filtro MLE', 'thermostat', 'mle')
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
                
                def on_go_to_live(strategy_filename):
                    if strategy_filename and live_page:
                        live_page.selected_strategy = strategy_filename
                        live_page.strat_select.value = strategy_filename
                    show_page('live')
                    
                analyzer_state = render_strategy_analyzer(on_back_to_builder=on_back_to_builder, on_go_to_live=on_go_to_live)

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
                live_page = render_live_monitor_page()
                
            with ui.column().classes('w-full h-full') as pages['mle']:
                render_mle_thermometer_page()
                
        # Restaurar la última página activa desde localStorage (evita volver a builder tras reconexiones)
        async def restore_active_page():
            stored = await ui.run_javascript("localStorage.getItem('tqa_active_page') || 'analyzer'")
            target = stored if stored in pages else 'analyzer'
            show_page(target)
        
        ui.timer(0, restore_active_page, once=True)

    # Run NiceGUI over the existing FastAPI app
    ui.run_with(
        app,
        title='Trading Quant',
        favicon='📈',
        # El puerto se manejará desde Uvicorn en start.bat
    )
