import asyncio
import pandas as pd
from nicegui.json import orjson_wrapper

# Patch global para orjson y pandas Timestamp (previene colapsos en la UI)
original_converter = orjson_wrapper._orjson_converter
def custom_orjson_converter(obj):
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    return original_converter(obj)
orjson_wrapper._orjson_converter = custom_orjson_converter

from nicegui import ui
from .pages.live_monitor_page import render_live_monitor_page
from .pages.binance_account_page import render_binance_account_page
from .pages.binance_operations_page import render_binance_operations_page
from .pages.binance_p2p_page import render_binance_p2p_page
from .pages.derivatives_analyzer_page import render_derivatives_analyzer_page
from .pages.options_algo_page import render_options_algo_page
from .pages.reconciliation_page import render_reconciliation_page
from .components.api_credentials_dialog import open_api_credentials_dialog
from .components.quant_copilot import render_quant_copilot


def create_gui_bots(app):
    """
    Integra en la app FastAPI una interfaz NiceGUI reducida, enfocada
    únicamente en los módulos de operación de bots sobre Binance:
    Live Monitor, Cartera & Riesgo, Operativa Spot/Fut, P2P, Derivados,
    Opciones & TWAP/POV, Copiloto Cuant (IA) y Conexión de APIs.
    """

    @ui.page('/')
    def dashboard():
        ui.dark_mode().enable()
        ui.colors(primary='#f59e0b', secondary='#0a0e17', accent='#10b981', dark='#0a0e17')

        ui.add_head_html('''
            <link rel="preconnect" href="https://fonts.googleapis.com">
            <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
            <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
            <style>
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

            .q-card, .nicegui-card {
                background-color: #111827 !important;
                border: 1px solid #1e293b !important;
                border-radius: 12px !important;
            }

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
            document.addEventListener('keydown', function(e) {
                if (e.key !== 'Enter') return;
                var el = e.target;
                if (el.tagName !== 'INPUT') return;
                var t = (el.type || '').toLowerCase();
                if (t === 'textarea') return;
                var inDialog = el.closest('.q-dialog');
                if (inDialog) return;
                e.preventDefault();
                e.stopPropagation();
            }, true);

            document.addEventListener('DOMContentLoaded', function() {
                document.querySelectorAll('button:not([type])').forEach(function(btn) {
                    btn.setAttribute('type', 'button');
                });
            });

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

            if live_page and hasattr(live_page, 'set_page_active'):
                live_page.set_page_active(page_name == 'live')

            ui.run_javascript(f"localStorage.setItem('tqa_bots_active_page', '{page_name}');")

        with ui.left_drawer(value=True).classes('bg-[#080c14] text-white border-r border-[#1e293b] p-3 flex flex-col justify-between overflow-y-auto').props('bordered width=260 :breakpoint="0" no-swipe-open'):
            with ui.column().classes('w-full gap-2'):
                with ui.row().classes('items-center gap-3 px-2 py-3 border-b border-[#1e293b]/80 w-full mb-1'):
                    with ui.row().classes('items-center justify-center w-9 h-9 rounded-lg bg-amber-500/15 border border-amber-500/40 text-amber-400 flex-none'):
                        ui.icon('smart_toy', size='1.5rem')
                    with ui.column().classes('gap-0 flex-1 min-w-0'):
                        ui.label('TRADING QUANT BOTS').classes('text-[10px] font-bold text-amber-400 tracking-widest uppercase font-mono truncate')
                        ui.label('Operativa Binance').classes('text-sm font-extrabold text-white tracking-tight font-heading truncate')

                def menu_item(text, icon, page_id):
                    btn = ui.button(text, icon=icon, on_click=lambda p=page_id: show_page(p))
                    btn.props('flat no-caps align=left')
                    btn.classes('w-full justify-start text-left text-slate-300 hover:text-white hover:bg-slate-800/80 font-medium text-xs py-2.5 px-3 rounded-lg transition-all border border-transparent')
                    menu_buttons[page_id] = btn
                    return btn

                ui.label('BOTS BINANCE').classes('text-[10px] font-extrabold text-slate-500 tracking-wider px-3 pt-2 pb-0.5 font-mono')
                menu_item('Live Monitor', 'play_circle', 'live')
                menu_item('Cartera & Riesgo Binance', 'account_balance_wallet', 'binance_account')
                menu_item('Operativa Binance (Spot/Fut)', 'receipt_long', 'binance_operations')
                menu_item('P2P Binance (Real)', 'handshake', 'binance_p2p')
                menu_item('Derivados & Futuros', 'query_stats', 'derivatives')
                menu_item('Opciones & TWAP/POV', 'hub', 'options_algo')
                menu_item('Conciliación App ↔ Binance', 'fact_check', 'reconciliation')

                copilot_holder = [None]

                ui.button(
                    'Copiloto Cuant (IA)',
                    icon='smart_toy',
                    on_click=lambda: copilot_holder[0].toggle() if copilot_holder[0] else None
                ).props('flat no-caps align=left').classes('w-full justify-start text-left text-emerald-400 hover:text-emerald-300 hover:bg-emerald-500/15 font-bold text-xs py-2 px-3 rounded-lg transition-all border border-emerald-500/25 mt-1')

                ui.button(
                    'Conectar APIs Exchange',
                    icon='vpn_key',
                    on_click=lambda: open_api_credentials_dialog()
                ).props('flat no-caps align=left').classes('w-full justify-start text-left text-amber-400 hover:text-amber-300 hover:bg-amber-500/15 font-bold text-xs py-2 px-3 rounded-lg transition-all border border-amber-500/25 mt-1')

            with ui.column().classes('w-full gap-2 pt-3 border-t border-[#1e293b]/80 mt-auto'):
                with ui.row().classes('w-full items-center justify-between px-2'):
                    with ui.row().classes('items-center gap-1.5 bg-[#111827] px-2.5 py-1 rounded-full border border-[#1e293b]'):
                        ui.icon('circle', size='0.55rem').classes('text-emerald-400 animate-pulse')
                        ui.label('EN LÍNEA').classes('text-[11px] font-bold text-emerald-400 font-mono')
                    ui.label('BOTS v1.0').classes('text-xs text-slate-500 font-mono font-bold')

                ui.button(
                    'Configuración / APIs',
                    icon='settings',
                    on_click=lambda: open_api_credentials_dialog()
                ).props('flat no-caps align=left').classes('w-full justify-start text-left text-slate-300 hover:text-white hover:bg-slate-800/80 font-medium text-xs py-2 px-3 rounded-lg transition-all')

        with ui.column().classes('w-full h-full p-2 md:p-3 bg-[#0a0e17]'):
            with ui.column().classes('w-full h-full') as pages['live']:
                live_page = render_live_monitor_page()

            with ui.column().classes('w-full h-full') as pages['binance_account']:
                render_binance_account_page()

            with ui.column().classes('w-full h-full') as pages['binance_operations']:
                render_binance_operations_page()

            with ui.column().classes('w-full h-full') as pages['binance_p2p']:
                render_binance_p2p_page()

            with ui.column().classes('w-full h-full') as pages['derivatives']:
                render_derivatives_analyzer_page()

            with ui.column().classes('w-full h-full') as pages['options_algo']:
                render_options_algo_page()

            with ui.column().classes('w-full h-full') as pages['reconciliation']:
                render_reconciliation_page()

        copilot_holder[0] = render_quant_copilot()

        async def restore_active_page():
            try:
                await asyncio.sleep(0.5)
                stored = await ui.run_javascript("localStorage.getItem('tqa_bots_active_page') || 'live'", timeout=6.0)
                target = stored if stored in pages else 'live'
                show_page(target)
            except Exception:
                try:
                    show_page('live')
                except Exception:
                    pass

        ui.timer(0.5, restore_active_page, once=True)

    ui.run_with(
        app,
        title='Trading Quant Bots',
        favicon='🤖',
        # Con el valor por defecto (3 s) un corte breve del WebSocket borra el cliente y todos sus timers.
        reconnect_timeout=120.0,
    )
