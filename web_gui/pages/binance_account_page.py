import os
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from nicegui import ui
import plotly.graph_objects as go

logger = logging.getLogger("BinanceAccountPage")

from execution_engine.binance_client import (
    BinanceTestnetClient,
    get_binance_credentials,
    verify_binance_credentials,
)
from execution_engine.security_manager import SecurityManager, is_real_trading_enabled
from analytics.portfolio_risk_analyzer import PortfolioRiskAnalyzer
from web_gui.components.api_credentials_dialog import open_api_credentials_dialog


def _format_time(ts) -> str:
    """Formatea timestamps de Binance en DD/MM HH:mm:ss."""
    if not ts:
        return "-"
    try:
        if isinstance(ts, (int, float)):
            if ts > 1e11:
                ts = ts / 1000.0
            dt = datetime.fromtimestamp(ts)
            return dt.strftime("%d/%m %H:%M:%S")
        return str(ts)
    except Exception:
        return str(ts)


def _obfuscate_key(key: str) -> str:
    """Ofusca claves API para visualización segura."""
    if not key:
        return "No configurada"
    if len(key) <= 8:
        return "********"
    return f"{key[:4]}...{key[-4:]}"


class BinanceAccountPage:
    def __init__(self):
        self.selected_network = "testnet"  # 'testnet' o 'mainnet'
        self.selected_wallet = "futures"   # 'futures' o 'spot'
        self.account_data: Dict[str, Any] = {}
        self.spot_data: Dict[str, Any] = {}
        self.risk_analysis: Dict[str, Any] = {}
        self.is_loading = False

    def render(self):
        with ui.column().classes('w-full h-full p-2 md:p-4 gap-6 bg-[#0a0e17] text-white'):

            # ──────────────────────────────────────────────────────────────
            # 1. Cabecera Principal y Selectores
            # ──────────────────────────────────────────────────────────────
            with ui.row().classes('w-full justify-between items-center pb-4 border-b border-gray-800 flex-wrap gap-4'):
                with ui.column().classes('gap-1'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('account_balance_wallet', size='32px', color='yellow-400')
                        ui.label('Cartera, Diagnóstico de Riesgo y Exchange Binance').classes('text-2xl md:text-3xl font-extrabold text-white tracking-tight font-heading')
                    ui.label('Analizador de situación en tiempo real, interpretación cuantitativa, métricas de riesgo y separación de carteras').classes('text-xs md:text-sm text-gray-400')

                with ui.row().classes('gap-3 items-center flex-wrap'):
                    # Selector de Red Exclusivo (Testnet vs Mainnet)
                    with ui.row().classes('bg-gray-950 p-1 rounded-xl border border-gray-800 gap-1 shadow-inner'):
                        self.btn_testnet = ui.button(
                            '🟡 Binance Testnet (Demo)', 
                            on_click=lambda: self._switch_network('testnet')
                        ).props('dense').classes('bg-yellow-500 text-black text-xs font-bold px-3 py-1.5 rounded-lg transition-all shadow')
                        
                        self.btn_mainnet = ui.button(
                            '🌐 Binance Real (Mainnet / Producción)', 
                            on_click=lambda: self._switch_network('mainnet')
                        ).props('dense flat').classes('text-gray-400 hover:text-white text-xs font-semibold px-3 py-1.5 rounded-lg transition-all')

                    # Botón de Modo Multiactivos (BTC como colateral)
                    self.multi_assets_btn = ui.button(
                        '🔀 Multiactivos: Verificando...', 
                        icon='account_balance_wallet', 
                        on_click=self._toggle_multi_assets_mode
                    ).props('dense outline').classes('text-xs text-yellow-300 border-yellow-500/40 rounded-xl px-3 py-1.5').tooltip('Permite usar tu saldo en BTC como garantía o colateral global para operar cualquier par en futuros')

                    # Badge de Candado de Seguridad (Solo Lectura vs Trading Real)
                    self.badge_security_lock = ui.badge(
                        '🔒 MODO SOLO LECTURA (SEGURO)', 
                        color='emerald-900'
                    ).classes('text-emerald-300 border border-emerald-500/40 font-bold text-xs px-3 py-2 rounded-xl shadow cursor-pointer')
                    self.badge_security_lock.tooltip('Haz clic para gestionar el candado de trading y los guardarraíles cuantitativos')
                    self.badge_security_lock.on('click', lambda: open_api_credentials_dialog(on_saved_callback=self._on_security_updated))

                    # Botón de refresco manual
                    self.btn_refresh = ui.button(
                        'Actualizar', 
                        icon='refresh', 
                        on_click=self._refresh_account_data_async
                    ).classes('bg-gray-800 hover:bg-gray-700 text-white font-bold text-xs px-3 py-2 rounded-xl shadow border border-gray-700')

                    # Botón para Conectar / Configurar APIs & Guardarraíles
                    ui.button(
                        '🔑 Conectar APIs & Seguridad', 
                        icon='vpn_key', 
                        on_click=lambda: open_api_credentials_dialog(on_saved_callback=self._on_security_updated)
                    ).classes('bg-gradient-to-r from-amber-500 to-yellow-400 hover:from-amber-400 hover:to-yellow-300 text-black font-extrabold text-xs px-3 py-2 rounded-xl shadow-lg border border-amber-300/40 transition-all')

                    # Botón de Kill-Switch de Emergencia
                    self.btn_kill_switch = ui.button(
                        '🚨 Kill-Switch', 
                        icon='power_settings_new', 
                        on_click=self._confirm_emergency_kill_switch
                    ).classes('bg-red-950/80 hover:bg-red-900 text-red-300 border border-red-700/60 font-black text-xs px-3 py-2 rounded-xl shadow transition-all').tooltip('Parada Inmediata: Bloquea trading real y cancela órdenes abiertas')

            # ──────────────────────────────────────────────────────────────
            # 2. Selector de Cartera: FUTUROS (Donde Operan Bots) vs SPOT
            # ──────────────────────────────────────────────────────────────
            with ui.row().classes('w-full justify-between items-center bg-gray-900/90 p-3.5 rounded-2xl border border-gray-800 flex-wrap gap-4 shadow-lg'):
                with ui.row().classes('items-center gap-3 flex-wrap'):
                    ui.label('CARTERA SELECCIONADA:').classes('text-xs font-black text-gray-300 tracking-wider')
                    with ui.row().classes('bg-gray-950 p-1.5 rounded-xl border border-gray-800 gap-1.5 shadow-inner'):
                        self.btn_wallet_futures = ui.button(
                            '⚡ Cartera Futuros (USDⓈ-M)',
                            on_click=lambda: self._switch_wallet('futures')
                        ).props('dense').classes('bg-yellow-500 text-black text-xs font-black px-4 py-2 rounded-lg transition-all shadow')
                        
                        self.btn_wallet_spot = ui.button(
                            '🪙 Cartera Spot (Contado)',
                            on_click=lambda: self._switch_wallet('spot')
                        ).props('dense flat').classes('text-gray-400 hover:text-white text-xs font-semibold px-4 py-2 rounded-lg transition-all')

                with ui.row().classes('items-center gap-2'):
                    self.wallet_info_chip = ui.badge('🤖 ENTORNO DE OPERACIÓN DE LOS BOTS', color='yellow-500').classes('text-black font-extrabold text-xs px-3.5 py-1.5 rounded-lg shadow')

            # ──────────────────────────────────────────────────────────────
            # 3. CONTENEDOR DE CARTERA FUTUROS (USDⓈ-M)
            # ──────────────────────────────────────────────────────────────
            self.container_futures = ui.column().classes('w-full gap-6')
            with self.container_futures:

                # Banner Informativo de Futuros
                with ui.card().classes('w-full bg-gradient-to-r from-yellow-950/30 to-gray-900/50 border border-yellow-500/40 p-3.5 rounded-xl flex flex-row items-center justify-between gap-3 shadow-inner'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('smart_toy', color='yellow-400', size='24px')
                        with ui.column().classes('gap-0.5'):
                            ui.label('Cartera de Futuros USDⓈ-M (Entorno de Algoritmos Cuantitativos)').classes('text-xs font-extrabold text-yellow-300 uppercase tracking-wide')
                            ui.label('Todos los bots de trading operan exclusivamente en esta cartera utilizando margen, apalancamiento y órdenes Stop/Take-Profit sobre contratos perpetuos.').classes('text-[11px] text-gray-300')
                    self.kpi_net_badge = ui.badge('Entorno: Testnet', color='gray-800').classes('text-[10px] text-yellow-300 font-bold px-2 py-1 rounded')

                # KPIs de Alto Nivel de Futuros
                with ui.grid(columns=6).classes('w-full gap-3'):
                    with ui.card().classes('bg-gray-900 p-3 rounded-xl border border-gray-800 shadow-md flex flex-col justify-between'):
                        ui.label('VALOR TOTAL CARTERA').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                        self.kpi_wallet_balance = ui.label('$0.00 USD').classes('text-xl font-black text-green-400 mt-1 font-mono')
                        self.kpi_futures_asset_count = ui.label('0 activos').classes('text-[10px] text-gray-400 font-medium')

                    with ui.card().classes('bg-gray-900 p-3 rounded-xl border border-gray-800 shadow-md flex flex-col justify-between'):
                        ui.label('MARGEN DISPONIBLE').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                        self.kpi_avail_margin = ui.label('0.00 USDT').classes('text-xl font-black text-yellow-400 mt-1 font-mono')
                        self.kpi_margin_util = ui.label('Uso Margen: 0.0%').classes('text-[10px] text-gray-400')

                    with ui.card().classes('bg-gray-900 p-3 rounded-xl border border-gray-800 shadow-md flex flex-col justify-between'):
                        ui.label('PNL NO REALIZADO').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                        self.kpi_unrealized_pnl = ui.label('+0.00 USDT').classes('text-xl font-black text-white mt-1 font-mono')
                        self.kpi_margin_balance = ui.label('Margen Total: 0.00 USDT').classes('text-[10px] text-gray-400')

                    with ui.card().classes('bg-gray-900 p-3 rounded-xl border border-gray-800 shadow-md flex flex-col justify-between'):
                        ui.label('APALANCAMIENTO').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                        self.kpi_effective_leverage = ui.label('0.00x').classes('text-xl font-black text-sky-400 mt-1 font-mono')
                        self.kpi_notional_exposure = ui.label('Nocional: $0.00 USD').classes('text-[10px] text-gray-400')

                    with ui.card().classes('bg-gray-900 p-3 rounded-xl border border-gray-800 shadow-md flex flex-col justify-between'):
                        ui.label('DISTANCIA A LIQUIDACIÓN').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                        self.kpi_liq_distance = ui.label('Seguro (100%)').classes('text-lg font-bold text-emerald-400 mt-1')
                        self.kpi_highest_risk_sym = ui.label('Sin riesgo de liq.').classes('text-[10px] text-gray-400')

                    with ui.card().classes('bg-gray-900 p-3 rounded-xl border border-gray-800 shadow-md flex flex-col justify-between'):
                        ui.label('VALUE AT RISK (VaR 95%)').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                        self.kpi_var_95 = ui.label('$0.00 (0.0%)').classes('text-lg font-bold text-amber-400 mt-1 font-mono')
                        self.kpi_api_status = ui.label('API: Conectada 🟢').classes('text-[10px] text-emerald-400 font-semibold')

                # Diagnóstico Cuantitativo de Cartera Futuros
                with ui.card().classes('bg-gray-900/90 border border-yellow-500/40 p-5 rounded-2xl w-full shadow-xl'):
                    with ui.row().classes('w-full justify-between items-center mb-3 flex-wrap gap-2'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('psychology', color='yellow-400', size='26px')
                            ui.label('Diagnóstico e Interpretación Cuantitativa de la Cartera').classes('text-lg font-bold text-white font-heading')
                        self.diag_health_badge = ui.badge('Analizando...', color='emerald-950').props('rounded').classes('text-emerald-300 font-bold text-xs px-3 py-1')

                    with ui.grid(columns=4).classes('w-full gap-3 mb-4'):
                        with ui.card().classes('bg-gray-950 p-3.5 rounded-xl border border-gray-800 flex flex-col justify-between'):
                            with ui.row().classes('items-center gap-2 mb-1'):
                                ui.icon('explore', color='blue-400', size='18px')
                                ui.label('Postura de Mercado').classes('text-xs font-bold text-blue-400')
                            self.diag_posture_label = ui.label('Calculando sesgo direccional...').classes('text-xs text-gray-300')

                        with ui.card().classes('bg-gray-950 p-3.5 rounded-xl border border-gray-800 flex flex-col justify-between'):
                            with ui.row().classes('items-center gap-2 mb-1'):
                                ui.icon('balance', color='yellow-400', size='18px')
                                ui.label('Margen y Apalancamiento').classes('text-xs font-bold text-yellow-400')
                            self.diag_margin_label = ui.label('Evaluando utilización de capital...').classes('text-xs text-gray-300')

                        with ui.card().classes('bg-gray-950 p-3.5 rounded-xl border border-gray-800 flex flex-col justify-between'):
                            with ui.row().classes('items-center gap-2 mb-1'):
                                ui.icon('shield', color='emerald-400', size='18px')
                                ui.label('Buffer de Liquidación').classes('text-xs font-bold text-emerald-400')
                            self.diag_liq_label = ui.label('Comprobando precios de liquidación...').classes('text-xs text-gray-300')

                        with ui.card().classes('bg-gray-950 p-3.5 rounded-xl border border-gray-800 flex flex-col justify-between'):
                            with ui.row().classes('items-center gap-2 mb-1'):
                                ui.icon('trending_down', color='amber-400', size='18px')
                                ui.label('Riesgo Estadístico (VaR)').classes('text-xs font-bold text-amber-400')
                            self.diag_var_label = ui.label('Estimando pérdida máxima esperada...').classes('text-xs text-gray-300')

                    with ui.column().classes('w-full bg-gray-950/80 p-3.5 rounded-xl border border-gray-800/80'):
                        with ui.row().classes('items-center gap-2 mb-2'):
                            ui.icon('checklist', color='green-400', size='18px')
                            ui.label('Pautas y Recomendaciones de Gestión de Riesgo:').classes('text-xs font-bold text-green-400 uppercase tracking-wider')
                        self.diag_recommendations_container = ui.column().classes('w-full gap-1.5 text-xs text-gray-300')

                # Matriz de Riesgo Cuantitativo y Gráficos Plotly
                with ui.row().classes('w-full gap-4 flex-wrap lg:flex-nowrap'):
                    with ui.card().classes('bg-gray-900 p-4 rounded-2xl border border-gray-800 flex-1 shadow-lg'):
                        with ui.row().classes('w-full justify-between items-center mb-2'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('show_chart', color='amber-400', size='20px')
                                ui.label('Simulador de Estrés de Mercado (Impacto en PnL & Equity)').classes('text-sm font-bold text-white')
                            ui.label('Shocks de mercado (-20% a +20%)').classes('text-[11px] text-gray-400 italic')
                        self.stress_chart = ui.plotly(self._build_empty_stress_chart()).classes('w-full h-64')

                    with ui.card().classes('bg-gray-900 p-4 rounded-2xl border border-gray-800 w-full lg:w-96 shadow-lg'):
                        with ui.row().classes('w-full justify-between items-center mb-2'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('pie_chart', color='blue-400', size='20px')
                                ui.label('Composición y Exposición').classes('text-sm font-bold text-white')
                        self.allocation_chart = ui.plotly(self._build_empty_alloc_chart()).classes('w-full h-64')

                # Tabla de Saldos y Margen en Futuros
                with ui.card().classes('bg-gray-900 p-5 rounded-2xl border border-gray-800 w-full shadow-xl'):
                    with ui.row().classes('w-full justify-between items-center mb-3'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('savings', color='green-400', size='22px')
                            ui.label('Activos con Saldo en Billetera de Futuros').classes('text-lg font-bold text-white font-heading')
                        ui.label('Desglose de activos y conversión en USD en tiempo real').classes('text-xs text-gray-400 italic')

                    self.assets_grid = ui.aggrid({
                        'defaultColDef': {'flex': 1, 'sortable': True, 'resizable': True},
                        'columnDefs': [
                            {'headerName': 'Activo (Asset)',      'field': 'asset',             'maxWidth': 130, 'cellClass': 'font-bold text-yellow-400'},
                            {'headerName': 'Balance Total',       'field': 'wallet_balance',    'maxWidth': 160, 'cellClass': 'font-mono text-green-400 font-bold'},
                            {'headerName': 'Valor Estimado (USD)','field': 'usd_value_str',     'maxWidth': 170, 'cellClass': 'font-mono text-yellow-300 font-bold'},
                            {'headerName': 'Disponible',          'field': 'available_balance', 'maxWidth': 150, 'cellClass': 'font-mono text-gray-200'},
                            {'headerName': 'Margen de Posición',  'field': 'margin_balance',    'maxWidth': 150, 'cellClass': 'font-mono text-blue-300'},
                            {'headerName': 'PnL No Realizado',    'field': 'unrealized_pnl',    'maxWidth': 150, 'cellClass': 'font-mono'},
                        ],
                        'rowData': [],
                        'rowClassRules': {
                            'text-green-400': 'parseFloat(data.unrealized_pnl) > 0',
                            'text-red-400':   'parseFloat(data.unrealized_pnl) < 0',
                        }
                    }).classes('h-48 text-white')

                # Posiciones Abiertas en Futuros
                with ui.card().classes('bg-gray-900 p-5 rounded-2xl border border-gray-800 w-full shadow-xl'):
                    with ui.row().classes('w-full justify-between items-center mb-3'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('show_chart', color='blue-400', size='22px')
                            ui.label('Posiciones Abiertas en Binance Futures').classes('text-lg font-bold text-white font-heading')
                        ui.label('Posiciones activas con medición de distancia a liquidación en tiempo real').classes('text-xs text-gray-400 italic')

                    self.positions_grid = ui.aggrid({
                        'defaultColDef': {'flex': 1, 'sortable': True, 'resizable': True},
                        'columnDefs': [
                            {'headerName': 'Símbolo / Contrato', 'field': 'symbol_display',  'maxWidth': 160, 'cellClass': 'font-bold text-white'},
                            {'headerName': 'Tamaño (Size)',      'field': 'size_display',    'maxWidth': 130, 'cellClass': 'font-mono text-yellow-300 font-bold'},
                            {'headerName': 'Precio Entrada',     'field': 'entry_price',     'maxWidth': 130, 'cellClass': 'font-mono text-gray-200'},
                            {'headerName': 'Break Even',         'field': 'break_even',      'maxWidth': 130, 'cellClass': 'font-mono text-gray-400'},
                            {'headerName': 'Precio Marca',       'field': 'mark_price',      'maxWidth': 130, 'cellClass': 'font-mono text-sky-400'},
                            {'headerName': 'Liq. Price',         'field': 'liq_price',       'maxWidth': 120, 'cellClass': 'font-mono text-red-400 font-semibold'},
                            {'headerName': 'Distancia a Liq.',   'field': 'liq_distance_str','maxWidth': 140, 'cellClass': 'font-mono font-bold'},
                            {'headerName': 'Margen',             'field': 'margin_display',  'maxWidth': 150, 'cellClass': 'font-mono text-blue-300'},
                            {'headerName': 'PNL (ROI %)',        'field': 'pnl_display',     'maxWidth': 180, 'cellClass': 'font-mono font-bold'},
                        ],
                        'rowData': [],
                        'rowClassRules': {
                            'text-green-400 font-semibold': 'data.raw_pnl > 0',
                            'text-red-400 font-semibold':   'data.raw_pnl < 0',
                        }
                    }).classes('h-44 text-white')

                # Órdenes Abiertas en Futuros
                with ui.card().classes('bg-gray-900 p-5 rounded-2xl border border-gray-800 w-full shadow-xl'):
                    with ui.row().classes('w-full justify-between items-center mb-3 flex-wrap gap-2'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('pending_actions', color='orange-400', size='22px')
                            ui.label('Órdenes Abiertas en Binance Futures').classes('text-lg font-bold text-white font-heading')
                        
                        with ui.row().classes('gap-2'):
                            ui.button('Cancelar Todas las Órdenes BTCUSDT', icon='delete_sweep', on_click=self._cancel_all_orders).props('dense outline color=red-400').classes('text-xs text-red-400 hover:bg-red-500/20')

                    self.open_orders_grid = ui.aggrid({
                        'defaultColDef': {'flex': 1, 'sortable': True, 'resizable': True},
                        'columnDefs': [
                            {'headerName': 'ID Orden',     'field': 'orderId',   'maxWidth': 160, 'cellClass': 'font-mono text-gray-300'},
                            {'headerName': 'Símbolo',      'field': 'symbol',    'maxWidth': 130, 'cellClass': 'font-bold text-white'},
                            {'headerName': 'Lado',         'field': 'side',      'maxWidth': 110},
                            {'headerName': 'Tipo',         'field': 'type',      'maxWidth': 130},
                            {'headerName': 'Cantidad',     'field': 'origQty',   'maxWidth': 130, 'cellClass': 'font-mono'},
                            {'headerName': 'Precio',       'field': 'price',     'maxWidth': 130, 'cellClass': 'font-mono'},
                            {'headerName': 'Stop Price',   'field': 'stopPrice', 'maxWidth': 130, 'cellClass': 'font-mono text-yellow-400'},
                            {'headerName': 'Fecha/Hora',   'field': 'time_str',  'maxWidth': 140, 'cellClass': 'font-mono text-xs text-gray-400 text-center'},
                        ],
                        'rowData': []
                    }).classes('h-40 text-white')

            # ──────────────────────────────────────────────────────────────
            # 4. CONTENEDOR DE CARTERA SPOT (CONTADO / CUSTODIA)
            # ──────────────────────────────────────────────────────────────
            self.container_spot = ui.column().classes('w-full gap-6')
            self.container_spot.set_visibility(False)  # Oculto por defecto hasta seleccionarlo

            with self.container_spot:

                # Banner Informativo de Cartera Spot
                with ui.card().classes('w-full bg-gradient-to-r from-blue-950/40 to-gray-900/50 border border-blue-500/40 p-4 rounded-xl flex flex-row items-center justify-between gap-3 shadow-inner'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('wallet', color='blue-400', size='26px')
                        with ui.column().classes('gap-0.5'):
                            ui.label('Cartera Spot de Binance (Contado / Custodia / Hold)').classes('text-xs font-extrabold text-blue-300 uppercase tracking-wide')
                            ui.label('Esta cartera refleja tus criptomonedas y saldo líquido disponible sin apalancamiento. Los bots cuantitativos NO operan en Spot.').classes('text-[11px] text-gray-300')
                    self.kpi_spot_net_badge = ui.badge('Entorno: Testnet', color='gray-800').classes('text-[10px] text-blue-300 font-bold px-2 py-1 rounded')

                # Alerta Destacada para Claves de Solo Futuros (Testnet vs Real)
                self.spot_warning_card = ui.card().classes('w-full bg-amber-950/40 border border-amber-500/60 p-4 rounded-xl shadow-lg')
                self.spot_warning_card.set_visibility(False)
                with self.spot_warning_card:
                    with ui.row().classes('items-start gap-3'):
                        ui.icon('warning', color='amber-400', size='28px').classes('mt-0.5')
                        with ui.column().classes('gap-1 flex-1'):
                            ui.label('Nota sobre Permisos de la API en Cartera Spot').classes('text-sm font-bold text-amber-300')
                            self.spot_warning_text = ui.label('Verificando acceso a Spot...').classes('text-xs text-gray-300 leading-relaxed')
                            with ui.row().classes('items-center gap-2 mt-2'):
                                ui.button(
                                    'Configurar Claves con Permisos Spot', 
                                    icon='vpn_key', 
                                    on_click=lambda: open_api_credentials_dialog(on_saved_callback=self._refresh_account_data_async)
                                ).props('dense outline color=amber-400').classes('text-xs text-amber-300 px-3 py-1 rounded-lg')

                # KPIs de Cartera Spot
                with ui.grid(columns=4).classes('w-full gap-4'):
                    with ui.card().classes('bg-gray-900 p-4 rounded-xl border border-gray-800 shadow-md flex flex-col justify-between'):
                        ui.label('VALOR TOTAL SPOT ESTIMADO').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                        self.kpi_spot_total_usd = ui.label('$0.00 USD').classes('text-2xl font-black text-green-400 mt-1 font-mono')
                        ui.label('Suma de todos los activos a precio de mercado').classes('text-[10px] text-gray-400')

                    with ui.card().classes('bg-gray-900 p-4 rounded-xl border border-gray-800 shadow-md flex flex-col justify-between'):
                        ui.label('SALDO LIBRE EN USDT').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                        self.kpi_spot_free_usdt = ui.label('0.00 USDT').classes('text-2xl font-black text-yellow-400 mt-1 font-mono')
                        self.kpi_spot_free_usd_sub = ui.label('Disponible para compras').classes('text-[10px] text-gray-400')

                    with ui.card().classes('bg-gray-900 p-4 rounded-xl border border-gray-800 shadow-md flex flex-col justify-between'):
                        ui.label('SALDO BLOQUEADO EN ÓRDENES').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                        self.kpi_spot_locked_usd = ui.label('$0.00 USD').classes('text-2xl font-black text-amber-400 mt-1 font-mono')
                        ui.label('Comprometido en órdenes de límite').classes('text-[10px] text-gray-400')

                    with ui.card().classes('bg-gray-900 p-4 rounded-xl border border-gray-800 shadow-md flex flex-col justify-between'):
                        ui.label('CRIPTOACTIVOS CON BALANCE').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                        self.kpi_spot_assets_count = ui.label('0 activos').classes('text-2xl font-black text-sky-400 mt-1 font-mono')
                        self.kpi_spot_orders_count = ui.label('0 órdenes abiertas').classes('text-[10px] text-gray-400')

                # Gráfico Plotly Donut de Composición Spot + Tabla de Saldos Spot
                with ui.row().classes('w-full gap-4 flex-wrap lg:flex-nowrap'):
                    # Donut Chart Spot
                    with ui.card().classes('bg-gray-900 p-4 rounded-2xl border border-gray-800 w-full lg:w-96 shadow-lg'):
                        with ui.row().classes('w-full justify-between items-center mb-2'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('pie_chart', color='blue-400', size='20px')
                                ui.label('Distribución de Criptoactivos').classes('text-sm font-bold text-white')
                        self.spot_pie_chart = ui.plotly(self._build_empty_spot_pie()).classes('w-full h-72')

                    # Tabla de Saldos Spot
                    with ui.card().classes('bg-gray-900 p-5 rounded-2xl border border-gray-800 flex-1 shadow-xl'):
                        with ui.row().classes('w-full justify-between items-center mb-3'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('currency_bitcoin', color='yellow-400', size='22px')
                                ui.label('Activos en Cartera Spot (Free & Locked)').classes('text-lg font-bold text-white font-heading')
                            ui.label('Balances directos con precio unitario y valoración en USD').classes('text-xs text-gray-400 italic')

                        self.spot_assets_grid = ui.aggrid({
                            'defaultColDef': {'flex': 1, 'sortable': True, 'resizable': True},
                            'columnDefs': [
                                {'headerName': 'Criptoactivo',        'field': 'asset',          'maxWidth': 130, 'cellClass': 'font-bold text-yellow-400'},
                                {'headerName': 'Saldo Libre (Free)',  'field': 'free_str',       'maxWidth': 150, 'cellClass': 'font-mono text-green-400 font-semibold'},
                                {'headerName': 'Bloqueado (Locked)',  'field': 'locked_str',     'maxWidth': 150, 'cellClass': 'font-mono text-gray-400'},
                                {'headerName': 'Balance Total',       'field': 'total_str',      'maxWidth': 150, 'cellClass': 'font-mono text-white font-bold'},
                                {'headerName': 'Precio Unit. (USD)',  'field': 'unit_price_str', 'maxWidth': 150, 'cellClass': 'font-mono text-sky-400'},
                                {'headerName': 'Valor Estimado (USD)','field': 'usd_value_str',  'maxWidth': 170, 'cellClass': 'font-mono text-yellow-300 font-bold'},
                            ],
                            'rowData': []
                        }).classes('h-72 text-white')

                # Tabla de Órdenes Abiertas en Spot
                with ui.card().classes('bg-gray-900 p-5 rounded-2xl border border-gray-800 w-full shadow-xl'):
                    with ui.row().classes('w-full justify-between items-center mb-3 flex-wrap gap-2'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('pending_actions', color='orange-400', size='22px')
                            ui.label('Órdenes Abiertas en Binance Spot').classes('text-lg font-bold text-white font-heading')
                        ui.label('Órdenes límite o condicionales pendientes de ejecución en Spot').classes('text-xs text-gray-400 italic')

                    self.spot_orders_grid = ui.aggrid({
                        'defaultColDef': {'flex': 1, 'sortable': True, 'resizable': True},
                        'columnDefs': [
                            {'headerName': 'ID Orden',     'field': 'orderId',   'maxWidth': 160, 'cellClass': 'font-mono text-gray-300'},
                            {'headerName': 'Símbolo / Par', 'field': 'symbol',    'maxWidth': 140, 'cellClass': 'font-bold text-white'},
                            {'headerName': 'Lado',         'field': 'side',      'maxWidth': 110},
                            {'headerName': 'Tipo',         'field': 'type',      'maxWidth': 130},
                            {'headerName': 'Cantidad',     'field': 'origQty',   'maxWidth': 140, 'cellClass': 'font-mono'},
                            {'headerName': 'Precio',       'field': 'price',     'maxWidth': 140, 'cellClass': 'font-mono'},
                            {'headerName': 'Stop Price',   'field': 'stopPrice', 'maxWidth': 130, 'cellClass': 'font-mono text-yellow-400'},
                            {'headerName': 'Fecha/Hora',   'field': 'time_str',  'maxWidth': 140, 'cellClass': 'font-mono text-xs text-gray-400 text-center'},
                        ],
                        'rowData': []
                    }).classes('h-40 text-white')

            # ──────────────────────────────────────────────────────────────
            # 5. Centro de Conexión y Estado de APIs de Exchange (Testnet & Real)
            # ──────────────────────────────────────────────────────────────
            with ui.card().classes('bg-gray-900/90 p-5 rounded-2xl border border-gray-800 w-full shadow-xl'):
                with ui.row().classes('w-full justify-between items-center mb-4 flex-wrap gap-2'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('hub', color='yellow-400', size='24px')
                        ui.label('Centro de Conexión de APIs de Exchange (Testnet & Real)').classes('text-lg font-bold text-white font-heading')
                    
                    ui.button(
                        '⚙️ Configurar / Editar Claves de API', 
                        icon='vpn_key', 
                        on_click=lambda: open_api_credentials_dialog(on_saved_callback=self._refresh_account_data_async)
                    ).classes('bg-amber-500 hover:bg-amber-400 text-black font-extrabold text-xs px-3 py-2 rounded-xl shadow border border-amber-400/40 transition-all')

                with ui.grid(columns=2).classes('w-full gap-4'):
                    # Tarjeta Testnet
                    with ui.card().classes('bg-gray-950 p-4 rounded-xl border border-gray-800 flex flex-col justify-between gap-3'):
                        with ui.row().classes('w-full justify-between items-center'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('science', color='amber-400', size='20px')
                                ui.label('Binance Futures Testnet (Demo)').classes('text-sm font-bold text-amber-400')
                            self.badge_testnet_status = ui.badge('Verificando...', color='gray-800').classes('text-[11px] font-bold px-2 py-0.5 rounded')
                        
                        with ui.column().classes('gap-1 text-xs'):
                            self.label_testnet_key = ui.label('API Key: Cargando...').classes('font-mono text-gray-300')
                            self.label_testnet_secret = ui.label('Secret: Cargando...').classes('font-mono text-gray-400')
                            ui.label('Endpoint: testnet.binancefuture.com (Futures)').classes('text-[10px] text-gray-500 font-mono')
                        
                        with ui.row().classes('w-full justify-between items-center pt-2 border-t border-gray-900'):
                            self.label_testnet_diag_res = ui.label('').classes('text-xs font-mono')
                            self.btn_test_testnet_quick = ui.button(
                                '🧪 Probar Testnet',
                                icon='speed',
                                on_click=lambda: self._test_network_quick(True)
                            ).props('dense outline color=amber-400').classes('text-xs text-amber-400 px-3 py-1.5 rounded-lg')

                    # Tarjeta Real
                    with ui.card().classes('bg-gray-950 p-4 rounded-xl border border-gray-800 flex flex-col justify-between gap-3'):
                        with ui.row().classes('w-full justify-between items-center'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('public', color='blue-400', size='20px')
                                ui.label('Binance Real (Mainnet)').classes('text-sm font-bold text-blue-400')
                            self.badge_real_status = ui.badge('Verificando...', color='gray-800').classes('text-[11px] font-bold px-2 py-0.5 rounded')
                        
                        with ui.column().classes('gap-1 text-xs'):
                            self.label_real_key = ui.label('API Key: Cargando...').classes('font-mono text-gray-300')
                            self.label_real_secret = ui.label('Secret: Cargando...').classes('font-mono text-gray-400')
                            ui.label('Endpoints: fapi.binance.com (Futures) & api.binance.com (Spot)').classes('text-[10px] text-gray-500 font-mono')
                        
                        with ui.row().classes('w-full justify-between items-center pt-2 border-t border-gray-900'):
                            self.label_real_diag_res = ui.label('').classes('text-xs font-mono')
                            self.btn_test_real_quick = ui.button(
                                '🌐 Probar Real',
                                icon='speed',
                                on_click=lambda: self._test_network_quick(False)
                            ).props('dense outline color=blue-400').classes('text-xs text-blue-400 px-3 py-1.5 rounded-lg')

            # Refresco periódico (cada 15s para no congestionar la conexión con Binance)
            ui.timer(1.0, self._refresh_account_data_async, once=True)
            self.live_timer = ui.timer(15.0, self._refresh_account_data_async)
            ui.context.client.on_disconnect(lambda: self.live_timer.deactivate() if hasattr(self, 'live_timer') and self.live_timer else None)

    # ──────────────────────────────────────────────────────────────
    # Métodos y Acciones de Red y Cartera
    # ──────────────────────────────────────────────────────────────

    def _switch_wallet(self, wallet: str):
        """Alterna la vista entre Cartera de Futuros y Cartera Spot."""
        self.selected_wallet = wallet
        if wallet == "futures":
            self.btn_wallet_futures.classes('bg-yellow-500 text-black font-black', remove='text-gray-400')
            self.btn_wallet_spot.classes('text-gray-400 hover:text-white', remove='bg-blue-500 text-white font-black')
            self.wallet_info_chip.set_text('🤖 ENTORNO DE OPERACIÓN DE LOS BOTS')
            self.wallet_info_chip.props('color=yellow-500')
            self.wallet_info_chip.classes('text-black', remove='text-white')
            self.container_futures.set_visibility(True)
            self.container_spot.set_visibility(False)
            if hasattr(self, 'multi_assets_btn'):
                self.multi_assets_btn.set_visibility(True)
        else:
            self.btn_wallet_spot.classes('bg-blue-500 text-white font-black', remove='text-gray-400')
            self.btn_wallet_futures.classes('text-gray-400 hover:text-white', remove='bg-yellow-500 text-black font-black')
            self.wallet_info_chip.set_text('🪙 CARTERA SPOT (HOLD / CUSTODIA)')
            self.wallet_info_chip.props('color=blue-600')
            self.wallet_info_chip.classes('text-white', remove='text-black')
            self.container_futures.set_visibility(False)
            self.container_spot.set_visibility(True)
            if hasattr(self, 'multi_assets_btn'):
                self.multi_assets_btn.set_visibility(False)

        asyncio.create_task(self._refresh_account_data_async())

    def _switch_network(self, net: str):
        self.selected_network = net
        if net == "testnet":
            self.btn_testnet.classes('bg-yellow-500 text-black font-bold', remove='text-gray-400')
            self.btn_mainnet.classes('text-gray-400 hover:text-white', remove='bg-blue-600 text-white font-bold')
            if hasattr(self, 'kpi_net_badge'):
                self.kpi_net_badge.set_text('Entorno: Binance Futures Testnet')
            if hasattr(self, 'kpi_spot_net_badge'):
                self.kpi_spot_net_badge.set_text('Entorno: Binance Spot Testnet')
        else:
            self.btn_mainnet.classes('bg-blue-600 text-white font-bold', remove='text-gray-400')
            self.btn_testnet.classes('text-gray-400 hover:text-white', remove='bg-yellow-500 text-black font-bold')
            if hasattr(self, 'kpi_net_badge'):
                self.kpi_net_badge.set_text('Entorno: Binance Real (Mainnet)')
            if hasattr(self, 'kpi_spot_net_badge'):
                self.kpi_spot_net_badge.set_text('Entorno: Binance Real (Mainnet)')
        
        asyncio.create_task(self._refresh_account_data_async())

    async def _refresh_account_data_async(self):
        """Descarga de forma asíncrona todos los datos de la cuenta según la cartera y red seleccionadas."""
        if self.is_loading:
            return
        self.is_loading = True
        self.btn_refresh.props('loading')

        try:
            use_test = (self.selected_network == "testnet")
            client = BinanceTestnetClient(use_testnet=use_test)
            loop = asyncio.get_event_loop()

            # 1. Actualizar credenciales y estado del candado en pantalla
            creds = get_binance_credentials()
            self._update_credentials_ui(creds)
            self._update_security_badge()

            # 2. Según la cartera activa, consultar endpoint correspondiente
            if self.selected_wallet == "futures":
                data = await loop.run_in_executor(None, lambda: client.get_full_account_info(use_testnet=use_test))
                self.account_data = data
                if data.get("success"):
                    self.risk_analysis = PortfolioRiskAnalyzer.analyze_portfolio(data)
                else:
                    self.risk_analysis = {}
                self._update_futures_ui(data, self.risk_analysis)
            else:
                spot_data = await loop.run_in_executor(None, lambda: client.get_spot_account_info(use_testnet=use_test))
                self.spot_data = spot_data
                self._update_spot_ui(spot_data)
        except Exception as e:
            logger.error("Error refrescando datos de cuenta Binance: %s", e, exc_info=True)
            ui.notify(f"Error actualizando datos de cuenta: {e}", type='negative')
        finally:
            self.btn_refresh.props(remove='loading')
            self.is_loading = False

    def _update_credentials_ui(self, creds: Dict[str, Any]):
        t_k = creds.get("testnet_api_key", "")
        t_s = creds.get("testnet_secret_key", "")
        r_k = creds.get("real_api_key", "")
        r_s = creds.get("real_secret_key", "")

        if hasattr(self, 'label_testnet_key'):
            self.label_testnet_key.set_text(f"API Key: {_obfuscate_key(t_k)}")
            self.label_testnet_secret.set_text(f"Secret: {_obfuscate_key(t_s)}")
            if creds.get("has_testnet"):
                self.badge_testnet_status.set_text('Configurada ✅')
                self.badge_testnet_status.props('color=emerald-900')
            else:
                self.badge_testnet_status.set_text('Sin configurar ⚠️')
                self.badge_testnet_status.props('color=gray-800')

        if hasattr(self, 'label_real_key'):
            self.label_real_key.set_text(f"API Key: {_obfuscate_key(r_k)}")
            self.label_real_secret.set_text(f"Secret: {_obfuscate_key(r_s)}")
            if creds.get("has_real"):
                self.badge_real_status.set_text('Configurada ✅')
                self.badge_real_status.props('color=blue-900')
            else:
                self.badge_real_status.set_text('Sin configurar ⚠️')
                self.badge_real_status.props('color=gray-800')

    def _update_futures_ui(self, data: Dict[str, Any], risk: Dict[str, Any]):
        if not data.get("success"):
            err = data.get("error", "Error desconocido")
            self.kpi_api_status.set_text("API: Error 🔴")
            self.kpi_api_status.classes('text-red-400', remove='text-emerald-400')
            return

        self.kpi_api_status.set_text("API: Conectada 🟢")
        self.kpi_api_status.classes('text-emerald-400', remove='text-red-400')

        # Estado del Modo Multiactivos
        is_multi = data.get("multi_assets_margin", False)
        if hasattr(self, 'multi_assets_btn'):
            if is_multi:
                self.multi_assets_btn.set_text("🔀 Multiactivos (BTC Colateral): ACTIVO ✅")
                self.multi_assets_btn.classes('bg-green-950 text-green-300 border-green-500/60 font-bold', remove='text-yellow-300 border-yellow-500/40 bg-gray-900')
            else:
                self.multi_assets_btn.set_text("🔀 Multiactivos (BTC Colateral): INACTIVO ⚪")
                self.multi_assets_btn.classes('bg-gray-900 text-gray-400 border-gray-700 font-normal', remove='bg-green-950 text-green-300 border-green-500/60')

        metrics = risk.get("metrics", {})
        var_metrics = risk.get("var_metrics", {})
        interpretation = risk.get("interpretation", {})

        tot_usd = metrics.get("total_wallet_usd", data.get("total_usd_value", 0.0))
        avail_bal = metrics.get("available_balance_usd", data.get("available_balance", 0.0))
        upnl = metrics.get("unrealized_pnl_usd", data.get("total_unrealized_pnl", 0.0))
        margin_util = metrics.get("margin_utilization_pct", 0.0)
        eff_lev = metrics.get("effective_leverage", 0.0)
        tot_notional = metrics.get("total_notional_usd", 0.0)
        min_liq_dist = metrics.get("min_liq_distance_pct")
        highest_risk_sym = metrics.get("highest_risk_symbol")

        self.kpi_wallet_balance.set_text(f"${tot_usd:,.2f} USD")
        self.kpi_futures_asset_count.set_text(f"{len(data.get('assets', []))} activos con saldo")
        self.kpi_avail_margin.set_text(f"{avail_bal:,.2f} USDT")
        self.kpi_margin_util.set_text(f"Uso Margen: {margin_util:.1f}%")
        
        sign = "+" if upnl >= 0 else ""
        self.kpi_unrealized_pnl.set_text(f"{sign}{upnl:,.2f} USDT")
        self.kpi_unrealized_pnl.classes('text-green-400' if upnl >= 0 else 'text-red-400', remove='text-white text-green-400 text-red-400')
        self.kpi_margin_balance.set_text(f"Margen: {data.get('total_margin_balance', 0.0):,.2f} USDT")

        self.kpi_effective_leverage.set_text(f"{eff_lev:.2f}x")
        self.kpi_notional_exposure.set_text(f"Nocional: ${tot_notional:,.2f}")

        if min_liq_dist is not None:
            self.kpi_liq_distance.set_text(f"{min_liq_dist:.1f}% Buffer")
            if min_liq_dist < 15.0:
                self.kpi_liq_distance.classes('text-red-400', remove='text-emerald-400 text-yellow-400')
            elif min_liq_dist < 30.0:
                self.kpi_liq_distance.classes('text-yellow-400', remove='text-emerald-400 text-red-400')
            else:
                self.kpi_liq_distance.classes('text-emerald-400', remove='text-yellow-400 text-red-400')
            self.kpi_highest_risk_sym.set_text(f"Riesgo en {highest_risk_sym}")
        else:
            self.kpi_liq_distance.set_text("Seguro (100%)")
            self.kpi_liq_distance.classes('text-emerald-400', remove='text-yellow-400 text-red-400')
            self.kpi_highest_risk_sym.set_text("Sin riesgo de liq.")

        var_95_usd = var_metrics.get("var_95_usd", 0.0)
        var_95_pct = var_metrics.get("var_95_pct", 0.0)
        self.kpi_var_95.set_text(f"${var_95_usd:,.2f} ({var_95_pct:.1f}%)")
        var_method = var_metrics.get("method")
        if var_method == "historical_simulation":
            avg_corr = var_metrics.get("avg_pairwise_correlation")
            corr_txt = f" | Correlación real prom.: {avg_corr:+.2f}" if avg_corr is not None else ""
            self.kpi_var_95.tooltip(
                f"Simulación Histórica sobre precios reales ({var_metrics.get('history_days_used', 0)} días){corr_txt}"
            )
        elif var_method == "parametric_perfect_correlation_assumption":
            self.kpi_var_95.tooltip(
                var_metrics.get("warning") or "VaR paramétrico: histórico local insuficiente, asume correlación perfecta entre activos."
            )
        else:
            self.kpi_var_95.tooltip("")

        # 2. Diagnóstico Cuantitativo
        if interpretation:
            score = interpretation.get("health_score", 100.0)
            badge_txt = interpretation.get("health_badge", "🟢 ÓPTIMO")
            self.diag_health_badge.set_text(f"{badge_txt} (Score: {score:.0f}/100)")
            
            self.diag_posture_label.set_text(interpretation.get("market_posture", "-"))
            self.diag_margin_label.set_text(interpretation.get("margin_and_leverage", "-"))
            self.diag_liq_label.set_text(interpretation.get("liquidation_safety", "-"))
            
            cvar_pct = var_metrics.get("cvar_95_pct", 0.0)
            self.diag_var_label.set_text(f"VaR 95%: {var_95_pct:.1f}% | CVaR 95%: {cvar_pct:.1f}% | Riesgo {var_metrics.get('risk_category', 'BAJO')}")

            self.diag_recommendations_container.clear()
            with self.diag_recommendations_container:
                recs = interpretation.get("recommendations", [])
                for r in recs:
                    ui.label(f"• {r}")

        # 3. Gráficos Plotly
        stress_res = risk.get("stress_test", [])
        if stress_res and hasattr(self, 'stress_chart'):
            self.stress_chart.update_figure(self._build_stress_chart_fig(stress_res, tot_usd))

        if hasattr(self, 'allocation_chart'):
            self.allocation_chart.update_figure(self._build_allocation_chart_fig(metrics))

        # 4. Tabla de Activos Futuros
        asset_rows = []
        for a in data.get("assets", []):
            usd_v = a.get("usd_value", a.get("wallet_balance", 0.0))
            asset_rows.append({
                "asset": a["asset"],
                "wallet_balance": f"{a['wallet_balance']:,.4f}",
                "usd_value_str": f"${usd_v:,.2f}",
                "available_balance": f"{a['available_balance']:,.4f}",
                "margin_balance": f"{a['margin_balance']:,.4f}",
                "unrealized_pnl": f"{a['unrealized_pnl']:+,.4f}",
                "max_withdraw": f"{a['max_withdraw']:,.4f}"
            })
        self.assets_grid.options['rowData'] = asset_rows
        self.assets_grid.update()

        # 5. Tabla de Posiciones
        pos_rows = []
        for p in metrics.get("position_details", []):
            side_icon = "📈 " if p['side'] == "LONG" else "📉 "
            raw_pnl = float(p.get('unrealized_pnl', 0.0))
            im = float(p.get('margin', 0.0))
            roi_pct = (raw_pnl / im * 100.0) if im > 0 else 0.0
            liq_dist_pct = p.get('liq_distance_pct')
            liq_dist_str = f"{liq_dist_pct:.1f}%" if liq_dist_pct is not None else "--"
            
            pos_rows.append({
                "symbol_display": f"{p['symbol']} Perp {p.get('leverage', 1)}x",
                "size_display": f"{side_icon} {abs(p.get('amount', 0)):.4f} {p['symbol'].replace('USDT', '').replace('USDC', '')}",
                "entry_price": f"{p['entry_price']:,.2f}",
                "break_even": f"{p.get('break_even_price', p['entry_price']):,.2f}",
                "mark_price": f"{p.get('mark_price', 0):,.2f}" if p.get('mark_price') else "-",
                "liq_price": f"{p['liq_price']:,.2f}" if p.get('liq_price') else "--",
                "liq_distance_str": liq_dist_str,
                "margin_display": f"{im:.2f} USDT",
                "pnl_display": f"{raw_pnl:+,.2f} USDT ({roi_pct:+.2f}%)",
                "raw_pnl": raw_pnl
            })
        self.positions_grid.options['rowData'] = pos_rows
        self.positions_grid.update()

        # 6. Tabla de Órdenes Abiertas Futuros
        order_rows = []
        for o in data.get("open_orders", []):
            order_rows.append({
                "orderId": str(o.get("orderId")),
                "symbol": o.get("symbol"),
                "side": o.get("side"),
                "type": o.get("type"),
                "origQty": f"{o.get('origQty', 0):.4f}",
                "price": f"{o.get('price', 0):,.2f}",
                "stopPrice": f"{o.get('stopPrice', 0):,.2f}" if o.get('stopPrice') else "-",
                "time_str": _format_time(o.get("time"))
            })
        self.open_orders_grid.options['rowData'] = order_rows
        self.open_orders_grid.update()

    def _update_spot_ui(self, spot_data: Dict[str, Any]):
        """Actualiza la interfaz de la Cartera Spot."""
        is_perm_err = spot_data.get("is_permission_error", False)
        has_error = not spot_data.get("success", False)

        if is_perm_err or has_error:
            self.spot_warning_card.set_visibility(True)
            err_msg = spot_data.get("error", "Error al consultar la cartera Spot.")
            self.spot_warning_text.set_text(
                f"{err_msg}\n\n"
                "💡 Explicación: En Binance Testnet, las claves creadas en testnet.binancefuture.com son EXCLUSIVAS de Futuros. "
                "Para operar o consultar Spot se utilizan las claves de Binance Real (o Spot Testnet). En Binance Real, asegúrate "
                "de activar la opción 'Habilitar Lectura' (Enable Reading) y 'Spot & Margin Trading' en tu Administrador de API."
            )
            # Limpiar KPIs
            self.kpi_spot_total_usd.set_text("$0.00 USD")
            self.kpi_spot_free_usdt.set_text("0.00 USDT")
            self.kpi_spot_locked_usd.set_text("$0.00 USD")
            self.kpi_spot_assets_count.set_text("Acceso Restringido ⚠️")
            self.kpi_spot_orders_count.set_text("0 órdenes")
            self.spot_assets_grid.options['rowData'] = []
            self.spot_assets_grid.update()
            self.spot_orders_grid.options['rowData'] = []
            self.spot_orders_grid.update()
            self.spot_pie_chart.update_figure(self._build_empty_spot_pie())
            return

        self.spot_warning_card.set_visibility(False)

        # 1. KPIs Spot
        tot_usd = spot_data.get("total_usd_value", 0.0)
        free_usd = spot_data.get("free_usd_value", 0.0)
        locked_usd = spot_data.get("locked_usd_value", 0.0)
        assets = spot_data.get("assets", [])
        orders = spot_data.get("open_orders", [])

        self.kpi_spot_total_usd.set_text(f"${tot_usd:,.2f} USD")
        
        # Buscar saldo USDT libre
        usdt_free = 0.0
        for a in assets:
            if a.get("asset") == "USDT":
                usdt_free = a.get("free", 0.0)
                break
        self.kpi_spot_free_usdt.set_text(f"{usdt_free:,.2f} USDT")
        self.kpi_spot_locked_usd.set_text(f"${locked_usd:,.2f} USD")
        self.kpi_spot_assets_count.set_text(f"{len(assets)} activos")
        self.kpi_spot_orders_count.set_text(f"{len(orders)} órdenes abiertas")

        # 2. Gráfico Plotly Donut Spot
        self.spot_pie_chart.update_figure(self._build_spot_pie_fig(assets, tot_usd))

        # 3. Tabla de Activos Spot
        spot_rows = []
        for a in assets:
            u_p = a.get("unit_price_usd", 0.0)
            u_p_str = f"${u_p:,.2f}" if u_p >= 1 else f"${u_p:.4f}"
            spot_rows.append({
                "asset": a["asset"],
                "free_str": a.get("free_str", f"{a.get('free', 0):.4f}"),
                "locked_str": a.get("locked_str", f"{a.get('locked', 0):.4f}"),
                "total_str": a.get("total_str", f"{a.get('total', 0):.4f}"),
                "unit_price_str": u_p_str,
                "usd_value_str": a.get("usd_value_str", f"${a.get('usd_value', 0):,.2f}")
            })
        self.spot_assets_grid.options['rowData'] = spot_rows
        self.spot_assets_grid.update()

        # 4. Tabla de Órdenes Spot
        spot_order_rows = []
        for o in orders:
            spot_order_rows.append({
                "orderId": str(o.get("orderId")),
                "symbol": o.get("symbol"),
                "side": o.get("side"),
                "type": o.get("type"),
                "origQty": f"{o.get('origQty', 0):.4f}",
                "price": f"{o.get('price', 0):,.2f}",
                "stopPrice": f"{o.get('stopPrice', 0):,.2f}" if o.get('stopPrice') else "-",
                "time_str": o.get("time_str", "-")
            })
        self.spot_orders_grid.options['rowData'] = spot_order_rows
        self.spot_orders_grid.update()

    # ──────────────────────────────────────────────────────────────
    # Gráficos Plotly de Riesgo y Stress Testing
    # ──────────────────────────────────────────────────────────────

    def _build_empty_stress_chart(self) -> go.Figure:
        fig = go.Figure()
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(15,23,42,0.6)',
            margin=dict(l=30, r=30, t=20, b=30),
            title=dict(text="Esperando posiciones activas para simular estrés...", font=dict(color="#94a3b8", size=12)),
            xaxis=dict(showgrid=False, zeroline=False),
            yaxis=dict(showgrid=False, zeroline=False)
        )
        return fig

    def _build_empty_alloc_chart(self) -> go.Figure:
        fig = go.Figure()
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=20, r=20, t=20, b=20),
            title=dict(text="Composición de Cartera Futuros", font=dict(color="#94a3b8", size=12))
        )
        return fig

    def _build_empty_spot_pie(self) -> go.Figure:
        fig = go.Figure()
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=20, r=20, t=20, b=20),
            title=dict(text="Composición de Activos Spot", font=dict(color="#94a3b8", size=12))
        )
        return fig

    def _build_spot_pie_fig(self, assets: List[Dict[str, Any]], total_usd: float) -> go.Figure:
        if not assets or total_usd <= 0:
            return self._build_empty_spot_pie()

        labels = [a["asset"] for a in assets[:6]]
        values = [a["usd_value"] for a in assets[:6]]

        # Si hay más de 6 activos, agrupar el resto en 'Otros'
        if len(assets) > 6:
            other_val = sum(a["usd_value"] for a in assets[6:])
            if other_val > 0:
                labels.append("Otros")
                values.append(other_val)

        fig = go.Figure(data=[go.Pie(
            labels=labels,
            values=values,
            hole=.55,
            marker=dict(colors=['#3b82f6', '#f59e0b', '#10b981', '#8b5cf6', '#ec4899', '#06b6d4', '#64748b']),
            textinfo='label+percent',
            insidetextorientation='radial',
            hoverinfo='label+value+percent',
            hovertemplate='%{label}: $%{value:,.2f} USD (%{percent})<extra></extra>'
        )])

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=10, r=10, t=10, b=10),
            font=dict(color='#cbd5e1', size=10),
            showlegend=False,
            annotations=[dict(text=f"Spot<br>${total_usd:,.0f}", x=0.5, y=0.5, font_size=12, showarrow=False, font_color='#ffffff')]
        )
        return fig

    def _build_stress_chart_fig(self, stress_results: List[Dict[str, Any]], total_equity: float) -> go.Figure:
        labels = [r["label"] for r in stress_results]
        impacts = [r["pnl_impact_usd"] for r in stress_results]
        colors = ['#10b981' if p >= 0 else '#ef4444' for p in impacts]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=labels,
            y=impacts,
            marker_color=colors,
            text=[f"{'+' if p >= 0 else ''}${p:,.2f}" for p in impacts],
            textposition='outside',
            hoverinfo='text+x',
            hovertext=[f"Shock Mercado: {r['label']}<br>Impacto PnL: ${r['pnl_impact_usd']:+,.2f} USD<br>Retorno Cartera: {r['return_pct']:+.2f}%<br>Equity Proyectado: ${r['projected_equity']:,.2f} USD" for r in stress_results]
        ))

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(15,23,42,0.6)',
            margin=dict(l=40, r=30, t=30, b=30),
            xaxis=dict(title="Shock de Mercado (%)", gridcolor='rgba(255,255,255,0.05)'),
            yaxis=dict(title="Impacto PnL ($ USD)", gridcolor='rgba(255,255,255,0.05)', zerolinecolor='rgba(255,255,255,0.2)'),
            font=dict(color='#cbd5e1', size=11),
            showlegend=False
        )
        return fig

    def _build_allocation_chart_fig(self, metrics: Dict[str, Any]) -> go.Figure:
        alloc = metrics.get("asset_allocation", [])
        if not alloc:
            return self._build_empty_alloc_chart()

        labels = [a["asset"] for a in alloc]
        values = [a["usd_value"] for a in alloc]

        fig = go.Figure(data=[go.Pie(
            labels=labels,
            values=values,
            hole=.55,
            marker=dict(colors=['#f59e0b', '#3b82f6', '#10b981', '#8b5cf6', '#ec4899']),
            textinfo='label+percent',
            insidetextorientation='radial',
            hoverinfo='label+value+percent',
            hovertemplate='%{label}: $%{value:,.2f} USD (%{percent})<extra></extra>'
        )])

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=10, r=10, t=10, b=10),
            font=dict(color='#cbd5e1', size=10),
            showlegend=False,
            annotations=[dict(text=f"Total<br>${metrics.get('total_wallet_usd', 0):,.0f}", x=0.5, y=0.5, font_size=12, showarrow=False, font_color='#ffffff')]
        )
        return fig

    # ──────────────────────────────────────────────────────────────
    # Diagnósticos y Acciones Rápidas
    # ──────────────────────────────────────────────────────────────

    async def _test_network_quick(self, use_testnet: bool):
        """Ejecuta una verificación rápida de autenticación y conectividad para la red especificada."""
        btn = self.btn_test_testnet_quick if use_testnet else self.btn_test_real_quick
        lbl = self.label_testnet_diag_res if use_testnet else self.label_real_diag_res
        net_name = "Testnet" if use_testnet else "Real"
        
        btn.props('loading')
        lbl.set_text(f"Probando {net_name}...")
        lbl.classes('text-amber-400', remove='text-emerald-400 text-red-400')

        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(
            None,
            lambda: verify_binance_credentials(use_testnet=use_testnet)
        )
        btn.props(remove='loading')

        if res.get("success"):
            lat = res.get("latency_ms", 0)
            bal = res.get("wallet_balance", 0.0)
            lbl.set_text(f"✅ OK ({lat}ms | ${bal:,.0f})")
            lbl.classes('text-emerald-400 font-bold', remove='text-amber-400 text-red-400')
            ui.notify(f"✅ Conexión OK con Binance {net_name} | Latencia: {lat}ms | Saldo: ${bal:,.2f} USDT", type='positive')
            if (self.selected_network == "testnet" and use_testnet) or (self.selected_network == "mainnet" and not use_testnet):
                await self._refresh_account_data_async()
        else:
            err = res.get("error", "Error desconocido")
            lbl.set_text("❌ Error")
            lbl.classes('text-red-400 font-bold', remove='text-amber-400 text-emerald-400')
            ui.notify(f"🚨 Error en Binance {net_name}: {err}", type='negative', duration=7000)

    async def _run_network_diagnostic(self):
        use_test = (self.selected_network == "testnet")
        ui.notify(f"Ejecutando prueba de diagnóstico en Binance ({'Testnet' if use_test else 'Mainnet'})...", type='info')
        client = BinanceTestnetClient(use_testnet=use_test)
        loop = asyncio.get_event_loop()
        
        if use_test:
            res = await loop.run_in_executor(None, lambda: client.test_testnet_connection(symbol="BTC/USDT"))
        else:
            res = await loop.run_in_executor(None, client.test_mainnet_connection)

        if res.get("success"):
            lat = res.get('latency_ms', 0)
            ui.notify(f"✅ Conexión OK con Binance ({'Testnet' if use_test else 'Mainnet'}) | Latencia: {lat} ms", type='positive', duration=6000)
            await self._refresh_account_data_async()
        else:
            err = res.get("error", "Error desconocido")
            ui.notify(f"🚨 Error en diagnóstico de Binance: {err}", type='negative', duration=8000)

    async def _toggle_multi_assets_mode(self):
        use_test = (self.selected_network == "testnet")
        client = BinanceTestnetClient(use_testnet=use_test)
        current_state = bool(self.account_data.get("multi_assets_margin", False)) if self.account_data else False
        new_target = not current_state
        
        ui.notify(f"Configurando Modo Multiactivos en Binance a {'ACTIVO' if new_target else 'INACTIVO'}...", type='info')
        loop = asyncio.get_event_loop()
        ok, err = await loop.run_in_executor(None, lambda: client.set_multi_assets_margin(new_target))
        
        if ok:
            ui.notify(f"✅ Modo Multiactivos {'ACTIVADO' if new_target else 'DESACTIVADO'} en Binance.", type='positive')
            await self._refresh_account_data_async()
        else:
            ui.notify(f"⚠️ No se pudo cambiar el modo multiactivos: {err}", type='warning')

    async def _cancel_all_orders(self):
        use_test = (self.selected_network == "testnet")
        client = BinanceTestnetClient(use_testnet=use_test)
        loop = asyncio.get_event_loop()
        ok, err = await loop.run_in_executor(None, lambda: client.cancel_all_futures_orders(symbol="BTCUSDT", use_testnet=use_test))
        if ok:
            ui.notify("🗑 Todas las órdenes abiertas de BTCUSDT han sido canceladas.", type='positive')
            await self._refresh_account_data_async()
        else:
            ui.notify(f"⚠️ No se pudieron cancelar órdenes: {err}", type='warning')

    def _update_security_badge(self):
        """Actualiza la apariencia del badge según el estado del candado de Real Trading."""
        if not hasattr(self, 'badge_security_lock') or not self.badge_security_lock:
            return
        is_real_enabled = is_real_trading_enabled()
        if is_real_enabled:
            self.badge_security_lock.set_text('⚠️ CANDADO ABIERTO: OPERATIVA REAL')
            self.badge_security_lock.props('color=red-900')
            self.badge_security_lock.classes('text-red-300 border border-red-500/60 animate-pulse font-extrabold', remove='text-emerald-300 border-emerald-500/40')
        else:
            self.badge_security_lock.set_text('🔒 MODO SOLO LECTURA (CANDADO ACTIVO)')
            self.badge_security_lock.props('color=emerald-950')
            self.badge_security_lock.classes('text-emerald-300 border border-emerald-500/40 font-bold', remove='text-red-300 border-red-500/60 animate-pulse font-extrabold')

    async def _on_security_updated(self):
        """Callback invocado al guardar credenciales o modificar guardarraíles."""
        self._update_security_badge()
        await self._refresh_account_data_async()

    def _confirm_emergency_kill_switch(self):
        """Muestra diálogo de confirmación para el Kill-Switch de Emergencia."""
        with ui.dialog() as dlg, ui.card().classes('bg-gray-900 text-white p-6 border-2 border-red-600 rounded-2xl max-w-md w-full gap-4 shadow-2xl'):
            with ui.row().classes('items-center gap-3'):
                ui.icon('warning', size='32px', color='red-500')
                ui.label('PARADA DE EMERGENCIA (KILL-SWITCH)').classes('text-lg font-black text-red-400 uppercase tracking-wide')
            
            ui.label(
                '¿Confirmas la activación inmediata del Kill-Switch de Emergencia? Esta acción ejecutará de inmediato:'
            ).classes('text-sm text-gray-300 leading-relaxed')

            with ui.column().classes('gap-1.5 text-xs text-gray-300 bg-black/40 p-3 rounded-xl border border-gray-800'):
                ui.label('1. 🔒 CERRAR el Candado de Trading Real bloqueando cualquier emisión de órdenes.').classes('text-emerald-400 font-bold')
                ui.label('2. 🛑 CANCELAR todas las órdenes abiertas de Futuros en el exchange.').classes('text-amber-300 font-semibold')
                ui.label('3. 🛡️ DETENER cualquier intento de ejecución algorítmica real.').classes('text-red-400 font-semibold')

            with ui.row().classes('w-full justify-end gap-3 mt-4'):
                ui.button('Cancelar', on_click=dlg.close).props('flat').classes('text-gray-400 hover:text-white font-bold text-xs')
                ui.button(
                    '🚨 SÍ, ACTIVAR KILL-SWITCH', 
                    on_click=lambda: [dlg.close(), asyncio.create_task(self._execute_emergency_kill_switch())]
                ).classes('bg-red-600 hover:bg-red-700 text-white font-black text-xs px-4 py-2 rounded-xl shadow-lg')
        dlg.open()

    async def _execute_emergency_kill_switch(self):
        """Ejecuta el protocolo de parada de emergencia inmediata."""
        ui.notify('🚨 Activando protocolo Kill-Switch de Emergencia...', type='warning', duration=5000)
        sec = SecurityManager()
        sec.set_real_trading_enabled(False)
        self._update_security_badge()

        loop = asyncio.get_event_loop()
        use_test = (self.selected_network == "testnet")
        client = BinanceTestnetClient(use_testnet=use_test)
        ok, err = await loop.run_in_executor(None, lambda: client.cancel_all_futures_orders(symbol="BTCUSDT", use_testnet=use_test))
        
        ui.notify('🛡️ Candado de seguridad BLOQUEADO a MODO SOLO LECTURA.', type='positive', duration=8000)
        if ok:
            ui.notify('🛑 Órdenes abiertas de Futuros canceladas con éxito.', type='positive', duration=8000)
        else:
            ui.notify(f'ℹ️ Cancelación de órdenes: {err}', type='info', duration=6000)

        await self._refresh_account_data_async()


def render_binance_account_page():
    page = BinanceAccountPage()
    page.render()
    return page
