from nicegui import ui
from .pages.strategy_builder_page import render_strategy_builder
from .pages.strategy_catalog_page import render_strategy_catalog
from .pages.market_analyzer_page import render_market_analyzer
from .pages.strategy_analyzer_page import render_strategy_analyzer
from .pages.optimizer_page import render_optimizer_page
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
        # Dark Quant Terminal Aesthetic - Bloomberg Obsidian Edition
        ui.dark_mode().enable()
        ui.colors(primary='#f59e0b', secondary='#0a0e17', accent='#10b981', dark='#0a0e17')
        
        ui.add_head_html('''
            <link rel="preconnect" href="https://fonts.googleapis.com">
            <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
            <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
            <style>
            /* ─────────────────────────────────────────────────────────────
               BLOOMBERG OBSIDIAN DESIGN SYSTEM - High Contrast & Visibility
               ───────────────────────────────────────────────────────────── */
            :root {
                --bg-obsidian: #0a0e17;
                --card-obsidian: #111827;
                --border-obsidian: #1e293b;
                --text-primary: #ffffff;
                --text-secondary: #cbd5e1;
                --text-muted: #94a3b8;
                --gold-accent: #f59e0b;
                --gold-light: #fbbf24;
                --emerald-accent: #10b981;
                --cyan-accent: #06b6d4;
            }

            body {
                font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
                background-color: var(--bg-obsidian) !important;
                color: var(--text-primary) !important;
                letter-spacing: -0.01em;
            }

            .font-mono, .mono-val, code {
                font-family: 'JetBrains Mono', monospace !important;
                font-variant-numeric: tabular-nums;
            }

            .font-heading {
                font-family: 'Space Grotesk', 'Plus Jakarta Sans', sans-serif !important;
                letter-spacing: -0.02em;
            }

            /* Sticky Header & Estilizado de Tablas Quasar */
            .q-table__container {
                background-color: #111827 !important;
                border: 1px solid #1e293b !important;
                border-radius: 12px !important;
                box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5) !important;
            }
            .q-table__middle {
                max-height: 540px;
                overflow-y: auto !important;
            }
            .q-table thead tr th {
                position: sticky !important;
                top: 0 !important;
                z-index: 20 !important;
                background-color: #0f172a !important;
                border-bottom: 2px solid #1e293b !important;
                font-size: 0.8125rem !important;
                font-weight: 700 !important;
                letter-spacing: 0.05em;
                text-transform: uppercase;
                color: #94a3b8 !important;
                padding: 10px 14px !important;
            }
            .q-table tbody tr {
                transition: background-color 0.15s ease !important;
            }
            .q-table tbody tr:nth-child(even) {
                background-color: rgba(15, 23, 42, 0.4) !important;
            }
            .q-table tbody tr:hover {
                background-color: rgba(245, 158, 11, 0.08) !important;
            }
            .q-table tbody td {
                font-size: 0.875rem !important;
                font-weight: 500 !important;
                color: #f1f5f9 !important;
                padding: 8px 14px !important;
                border-bottom: 1px solid #1e293b !important;
            }
            .q-table__bottom {
                background-color: #0f172a !important;
                color: #94a3b8 !important;
                font-size: 0.8125rem !important;
                border-top: 1px solid #1e293b !important;
            }

            /* Inputs & Selects Quasar */
            .q-field--outlined .q-field__control {
                background-color: #111827 !important;
                border-radius: 8px !important;
                border-color: #1e293b !important;
                transition: all 0.2s ease !important;
            }
            .q-field--outlined:hover .q-field__control {
                border-color: #334155 !important;
            }
            .q-field--outlined.q-field--focused .q-field__control {
                border-color: #f59e0b !important;
                box-shadow: 0 0 0 1px #f59e0b, 0 0 12px rgba(245, 158, 11, 0.2) !important;
            }
            .q-field__label {
                color: #94a3b8 !important;
                font-size: 0.875rem !important;
                font-weight: 600 !important;
            }
            .q-field__native, .q-field__input {
                color: #ffffff !important;
                font-weight: 500 !important;
            }

            /* Tarjetas y Contenedores */
            .q-card, .nicegui-card {
                background-color: #111827 !important;
                border: 1px solid #1e293b !important;
                border-radius: 12px !important;
            }

            /* Scrollbars Modernos */
            ::-webkit-scrollbar {
                width: 6px;
                height: 6px;
            }
            ::-webkit-scrollbar-track {
                background: #0a0e17;
            }
            ::-webkit-scrollbar-thumb {
                background: #1e293b;
                border-radius: 4px;
            }
            ::-webkit-scrollbar-thumb:hover {
                background: #f59e0b;
            }
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
        menu_buttons = {}
        live_page = None
        
        def show_page(page_name):
            for name, container in pages.items():
                container.set_visibility(name == page_name)
            for name, btn in menu_buttons.items():
                if name == page_name:
                    btn.classes(replace='w-full justify-start text-left bg-amber-500/20 text-amber-400 font-bold border border-amber-500/50 shadow-sm text-xs py-2.5 px-3 rounded-lg transition-all')
                else:
                    btn.classes(replace='w-full justify-start text-left text-slate-300 hover:text-white hover:bg-slate-800/80 font-medium text-xs py-2.5 px-3 rounded-lg transition-all border border-transparent')
            # Persistir la página activa en localStorage del navegador
            ui.run_javascript(f"localStorage.setItem('tqa_active_page', '{page_name}');")

        # Menú Lateral Vertical Izquierdo (Left Drawer) con estilo Bloomberg Obsidian
        with ui.left_drawer(value=True).classes('bg-[#080c14] text-white border-r border-[#1e293b] p-3 flex flex-col justify-between overflow-y-auto').props('bordered width=260 :breakpoint="0" no-swipe-open') as left_drawer:
            with ui.column().classes('w-full gap-2'):
                # Logo y Título
                with ui.row().classes('items-center gap-3 px-2 py-3 border-b border-[#1e293b]/80 w-full mb-1'):
                    with ui.row().classes('items-center justify-center w-9 h-9 rounded-lg bg-amber-500/15 border border-amber-500/40 text-amber-400 flex-none'):
                        ui.icon('candlestick_chart', size='1.5rem')
                    with ui.column().classes('gap-0 flex-1 min-w-0'):
                        ui.label('TRADING QUANT APP').classes('text-[10px] font-bold text-amber-400 tracking-widest uppercase font-mono truncate')
                        ui.label('Terminal Cuantitativo').classes('text-sm font-extrabold text-white tracking-tight font-heading truncate')
                
                # Menú de Navegación Vertical
                def menu_item(text, icon, page_id):
                    btn = ui.button(text, icon=icon, on_click=lambda p=page_id: show_page(p))
                    btn.props('flat no-caps align=left')
                    btn.classes('w-full justify-start text-left text-slate-300 hover:text-white hover:bg-slate-800/80 font-medium text-xs py-2.5 px-3 rounded-lg transition-all border border-transparent')
                    menu_buttons[page_id] = btn
                    return btn

                ui.label('PRINCIPAL').classes('text-[10px] font-extrabold text-slate-500 tracking-wider px-3 pt-2 pb-0.5 font-mono')
                menu_item('Strategy Builder', 'build', 'builder')
                menu_item('Estrategias Guardadas', 'list', 'catalog')
                menu_item('Strategy Analyzer', 'analytics', 'analyzer')
                menu_item('Optimizador (Grid)', 'tune', 'optimizer')
                menu_item('Historial Backtests', 'history', 'history')

                ui.label('AVANZADAS').classes('text-[10px] font-extrabold text-slate-500 tracking-wider px-3 pt-4 pb-0.5 font-mono')
                menu_item('Datos Almacenados', 'storage', 'market')
                menu_item('Machine Learning', 'psychology', 'ml')
                menu_item('Filtro MLE', 'thermostat', 'mle')
                menu_item('Live Monitor', 'play_circle', 'live')

            # Pie del Drawer Lateral
            with ui.column().classes('w-full gap-2 pt-3 border-t border-[#1e293b]/80 mt-auto'):
                with ui.row().classes('w-full items-center justify-between px-2'):
                    with ui.row().classes('items-center gap-1.5 bg-[#111827] px-2.5 py-1 rounded-full border border-[#1e293b]'):
                        ui.icon('circle', size='0.55rem').classes('text-emerald-400 animate-pulse')
                        ui.label('EN LÍNEA').classes('text-[11px] font-bold text-emerald-400 font-mono')
                    ui.label('v2.0').classes('text-xs text-slate-500 font-mono font-bold')
                
                ui.button('Configuración', icon='settings').props('flat no-caps align=left').classes('w-full justify-start text-left text-slate-300 hover:text-white hover:bg-slate-800/80 font-medium text-xs py-2 px-3 rounded-lg transition-all')


        # Contenedor principal
        with ui.column().classes('w-full h-full p-2 md:p-3 bg-[#0a0e17]'):
            
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

            with ui.column().classes('w-full h-full') as pages['optimizer']:
                def on_opt_go_to_analyzer(strat_name=None, symbol=None, timeframe=None, custom_params=None):
                    if analyzer_state and 'select_strategy' in analyzer_state:
                        if strat_name:
                            analyzer_state['select_strategy'](strat_name)
                    show_page('analyzer')

                render_optimizer_page(on_go_to_analyzer=on_opt_go_to_analyzer)

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
