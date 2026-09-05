import os
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List
from nicegui import ui
import plotly.graph_objects as go

from execution_engine.binance_client import (
    BinanceTestnetClient,
    get_binance_credentials,
    verify_binance_credentials,
)
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
        self.account_data: Dict[str, Any] = {}
        self.risk_analysis: Dict[str, Any] = {}
        self.is_loading = False

    def render(self):
        with ui.column().classes('w-full h-full p-2 md:p-4 gap-6 bg-[#0a0e17] text-white'):

            # ──────────────────────────────────────────────────────────────
            # 1. Cabecera Principal y Selector de Red (Testnet vs Real)
            # ──────────────────────────────────────────────────────────────
            with ui.row().classes('w-full justify-between items-center pb-4 border-b border-gray-800 flex-wrap gap-4'):
                with ui.column().classes('gap-1'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('account_balance_wallet', size='32px', color='yellow-400')
                        ui.label('Cartera, Diagnóstico de Riesgo y Exchange Binance').classes('text-2xl md:text-3xl font-extrabold text-white tracking-tight font-heading')
                    ui.label('Analizador de situación en tiempo real, interpretación cuantitativa, métricas de riesgo y separación de cuentas').classes('text-xs md:text-sm text-gray-400')

                with ui.row().classes('gap-3 items-center flex-wrap'):
                    # Selector de Red Exclusivo (Testnet vs Mainnet)
                    with ui.row().classes('bg-gray-950 p-1 rounded-xl border border-gray-800 gap-1 shadow-inner'):
                        self.btn_testnet = ui.button(
                            '🟡 Binance Futures Testnet (Demo)', 
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

                    # Botón de refresco manual
                    self.btn_refresh = ui.button(
                        'Actualizar', 
                        icon='refresh', 
                        on_click=self._refresh_account_data_async
                    ).classes('bg-gray-800 hover:bg-gray-700 text-white font-bold text-xs px-3 py-2 rounded-xl shadow border border-gray-700')

                    # Botón para Conectar / Configurar APIs
                    ui.button(
                        '🔑 Conectar APIs (Test & Real)', 
                        icon='vpn_key', 
                        on_click=lambda: open_api_credentials_dialog(on_saved_callback=self._refresh_account_data_async)
                    ).classes('bg-gradient-to-r from-amber-500 to-yellow-400 hover:from-amber-400 hover:to-yellow-300 text-black font-extrabold text-xs px-3 py-2 rounded-xl shadow-lg border border-amber-300/40 transition-all')

            # ──────────────────────────────────────────────────────────────
            # 2. KPIs de Alto Nivel de la Cartera y Exposición
            # ──────────────────────────────────────────────────────────────
            with ui.grid(columns=6).classes('w-full gap-3'):
                # KPI 1: Patrimonio Total
                with ui.card().classes('bg-gray-900 p-3 rounded-xl border border-gray-800 shadow-md flex flex-col justify-between'):
                    ui.label('VALOR TOTAL CARTERA').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                    self.kpi_wallet_balance = ui.label('$0.00 USD').classes('text-xl font-black text-green-400 mt-1 font-mono')
                    self.kpi_net_badge = ui.label('Entorno: Testnet').classes('text-[10px] text-gray-400 font-medium')

                # KPI 2: Margen Disponible
                with ui.card().classes('bg-gray-900 p-3 rounded-xl border border-gray-800 shadow-md flex flex-col justify-between'):
                    ui.label('SALDO DISPONIBLE').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                    self.kpi_avail_margin = ui.label('0.00 USDT').classes('text-xl font-black text-yellow-400 mt-1 font-mono')
                    self.kpi_margin_util = ui.label('Uso Margen: 0.0%').classes('text-[10px] text-gray-400')

                # KPI 3: PnL No Realizado
                with ui.card().classes('bg-gray-900 p-3 rounded-xl border border-gray-800 shadow-md flex flex-col justify-between'):
                    ui.label('PNL NO REALIZADO').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                    self.kpi_unrealized_pnl = ui.label('+0.00 USDT').classes('text-xl font-black text-white mt-1 font-mono')
                    self.kpi_margin_balance = ui.label('Margen Total: 0.00 USDT').classes('text-[10px] text-gray-400')

                # KPI 4: Apalancamiento Efectivo
                with ui.card().classes('bg-gray-900 p-3 rounded-xl border border-gray-800 shadow-md flex flex-col justify-between'):
                    ui.label('APALANCAMIENTO').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                    self.kpi_effective_leverage = ui.label('0.00x').classes('text-xl font-black text-sky-400 mt-1 font-mono')
                    self.kpi_notional_exposure = ui.label('Nocional: $0.00 USD').classes('text-[10px] text-gray-400')

                # KPI 5: Distancia Mínima a Liquidación
                with ui.card().classes('bg-gray-900 p-3 rounded-xl border border-gray-800 shadow-md flex flex-col justify-between'):
                    ui.label('DISTANCIA A LIQUIDACIÓN').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                    self.kpi_liq_distance = ui.label('Seguro (100%)').classes('text-lg font-bold text-emerald-400 mt-1')
                    self.kpi_highest_risk_sym = ui.label('Sin riesgo de liq.').classes('text-[10px] text-gray-400')

                # KPI 6: Value at Risk (VaR 95% 1D)
                with ui.card().classes('bg-gray-900 p-3 rounded-xl border border-gray-800 shadow-md flex flex-col justify-between'):
                    ui.label('VALUE AT RISK (VaR 95%)').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                    self.kpi_var_95 = ui.label('$0.00 (0.0%)').classes('text-lg font-bold text-amber-400 mt-1 font-mono')
                    self.kpi_api_status = ui.label('API: Conectada 🟢').classes('text-[10px] text-emerald-400 font-semibold')

            # ──────────────────────────────────────────────────────────────
            # 3. 🧠 INTERPRETACIÓN Y DIAGNÓSTICO CUANTITATIVO DE LA CARTERA
            # ──────────────────────────────────────────────────────────────
            with ui.card().classes('bg-gray-900/90 border border-yellow-500/40 p-5 rounded-2xl w-full shadow-xl'):
                with ui.row().classes('w-full justify-between items-center mb-3 flex-wrap gap-2'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('psychology', color='yellow-400', size='26px')
                        ui.label('Diagnóstico e Interpretación Cuantitativa de la Cartera').classes('text-lg font-bold text-white font-heading')
                    self.diag_health_badge = ui.badge('Analizando...', color='emerald-950').props('rounded').classes('text-emerald-300 font-bold text-xs px-3 py-1')

                # Tarjetas de diagnóstico estructurado
                with ui.grid(columns=4).classes('w-full gap-3 mb-4'):
                    # 1. Postura de Mercado
                    with ui.card().classes('bg-gray-950 p-3.5 rounded-xl border border-gray-800 flex flex-col justify-between'):
                        with ui.row().classes('items-center gap-2 mb-1'):
                            ui.icon('explore', color='blue-400', size='18px')
                            ui.label('Postura de Mercado').classes('text-xs font-bold text-blue-400')
                        self.diag_posture_label = ui.label('Calculando sesgo direccional...').classes('text-xs text-gray-300')

                    # 2. Margen y Apalancamiento
                    with ui.card().classes('bg-gray-950 p-3.5 rounded-xl border border-gray-800 flex flex-col justify-between'):
                        with ui.row().classes('items-center gap-2 mb-1'):
                            ui.icon('balance', color='yellow-400', size='18px')
                            ui.label('Margen y Apalancamiento').classes('text-xs font-bold text-yellow-400')
                        self.diag_margin_label = ui.label('Evaluando utilización de capital...').classes('text-xs text-gray-300')

                    # 3. Seguridad de Liquidación
                    with ui.card().classes('bg-gray-950 p-3.5 rounded-xl border border-gray-800 flex flex-col justify-between'):
                        with ui.row().classes('items-center gap-2 mb-1'):
                            ui.icon('shield', color='emerald-400', size='18px')
                            ui.label('Buffer de Liquidación').classes('text-xs font-bold text-emerald-400')
                        self.diag_liq_label = ui.label('Comprobando precios de liquidación...').classes('text-xs text-gray-300')

                    # 4. Exposición VaR & Volatilidad
                    with ui.card().classes('bg-gray-950 p-3.5 rounded-xl border border-gray-800 flex flex-col justify-between'):
                        with ui.row().classes('items-center gap-2 mb-1'):
                            ui.icon('trending_down', color='amber-400', size='18px')
                            ui.label('Riesgo Estadístico (VaR)').classes('text-xs font-bold text-amber-400')
                        self.diag_var_label = ui.label('Estimando pérdida máxima esperada...').classes('text-xs text-gray-300')

                # Recomendaciones Accionables
                with ui.column().classes('w-full bg-gray-950/80 p-3.5 rounded-xl border border-gray-800/80'):
                    with ui.row().classes('items-center gap-2 mb-2'):
                        ui.icon('checklist', color='green-400', size='18px')
                        ui.label('Pautas y Recomendaciones de Gestión de Riesgo:').classes('text-xs font-bold text-green-400 uppercase tracking-wider')
                    self.diag_recommendations_container = ui.column().classes('w-full gap-1.5 text-xs text-gray-300')

            # ──────────────────────────────────────────────────────────────
            # 4. 📊 MATRIZ DE RIESGO CUANTITATIVO Y SIMULACIÓN DE ESTRÉS (PLOTLY)
            # ──────────────────────────────────────────────────────────────
            with ui.row().classes('w-full gap-4 flex-wrap lg:flex-nowrap'):
                # Gráfico 1: Simulación de Estrés de Mercado
                with ui.card().classes('bg-gray-900 p-4 rounded-2xl border border-gray-800 flex-1 shadow-lg'):
                    with ui.row().classes('w-full justify-between items-center mb-2'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('show_chart', color='amber-400', size='20px')
                            ui.label('Simulador de Estrés de Mercado (Impacto en PnL & Equity)').classes('text-sm font-bold text-white')
                        ui.label('Shocks de mercado (-20% a +20%)').classes('text-[11px] text-gray-400 italic')
                    self.stress_chart = ui.plotly(self._build_empty_stress_chart()).classes('w-full h-64')

                # Gráfico 2: Distribución de Margen y Exposición (Donut)
                with ui.card().classes('bg-gray-900 p-4 rounded-2xl border border-gray-800 w-full lg:w-96 shadow-lg'):
                    with ui.row().classes('w-full justify-between items-center mb-2'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('pie_chart', color='blue-400', size='20px')
                            ui.label('Composición y Exposición').classes('text-sm font-bold text-white')
                    self.allocation_chart = ui.plotly(self._build_empty_alloc_chart()).classes('w-full h-64')

            # ──────────────────────────────────────────────────────────────
            # 5. Tabla de Saldos y Activos en la Billetera
            # ──────────────────────────────────────────────────────────────
            with ui.card().classes('bg-gray-900 p-5 rounded-2xl border border-gray-800 w-full shadow-xl'):
                with ui.row().classes('w-full justify-between items-center mb-3'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('savings', color='green-400', size='22px')
                        ui.label('Billetera y Activos con Saldo').classes('text-lg font-bold text-white font-heading')
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

            # ──────────────────────────────────────────────────────────────
            # 6. Posiciones Abiertas en Binance Futures
            # ──────────────────────────────────────────────────────────────
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

            # ──────────────────────────────────────────────────────────────
            # 7. Órdenes Abiertas (Open Orders) y Cancelación
            # ──────────────────────────────────────────────────────────────
            with ui.card().classes('bg-gray-900 p-5 rounded-2xl border border-gray-800 w-full shadow-xl'):
                with ui.row().classes('w-full justify-between items-center mb-3 flex-wrap gap-2'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('pending_actions', color='orange-400', size='22px')
                        ui.label('Órdenes Abiertas en Binance').classes('text-lg font-bold text-white font-heading')
                    
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
            # 8. Centro de Conexión y Estado de APIs de Exchange (Testnet & Real)
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
                            ui.label('Endpoint: testnet.binancefuture.com').classes('text-[10px] text-gray-500 font-mono')
                        
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
                            ui.label('Endpoint: fapi.binance.com').classes('text-[10px] text-gray-500 font-mono')
                        
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
    # Métodos y Acciones
    # ──────────────────────────────────────────────────────────────

    def _switch_network(self, net: str):
        self.selected_network = net
        if net == "testnet":
            self.btn_testnet.classes('bg-yellow-500 text-black font-bold', remove='text-gray-400')
            self.btn_mainnet.classes('text-gray-400 hover:text-white', remove='bg-blue-600 text-white font-bold')
            self.kpi_net_badge.set_text('Entorno: Binance Futures Testnet')
        else:
            self.btn_mainnet.classes('bg-blue-600 text-white font-bold', remove='text-gray-400')
            self.btn_testnet.classes('text-gray-400 hover:text-white', remove='bg-yellow-500 text-black font-bold')
            self.kpi_net_badge.set_text('Entorno: Binance Real (Mainnet)')
        
        asyncio.create_task(self._refresh_account_data_async())

    async def _refresh_account_data_async(self):
        """Descarga de forma asíncrona todos los datos de la cuenta y ejecuta el analizador de riesgo."""
        if self.is_loading:
            return
        self.is_loading = True
        self.btn_refresh.props('loading')

        use_test = (self.selected_network == "testnet")
        client = BinanceTestnetClient(use_testnet=use_test)

        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: client.get_full_account_info(use_testnet=use_test))

        self.account_data = data
        if data.get("success"):
            # Ejecutar análisis cuantitativo de riesgo
            self.risk_analysis = PortfolioRiskAnalyzer.analyze_portfolio(data)
        else:
            self.risk_analysis = {}

        self._update_ui_with_account_data(data, self.risk_analysis)

        self.btn_refresh.props(remove='loading')
        self.is_loading = False

    def _update_ui_with_account_data(self, data: Dict[str, Any], risk: Dict[str, Any]):
        # Actualizar información de credenciales para ambas redes
        creds = get_binance_credentials()
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

        # 1. KPIs Globales
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

        # 2. Actualizar Diagnóstico e Interpretación Cuantitativa
        if interpretation:
            score = interpretation.get("health_score", 100.0)
            badge_txt = interpretation.get("health_badge", "🟢 ÓPTIMO")
            self.diag_health_badge.set_text(f"{badge_txt} (Score: {score:.0f}/100)")
            
            self.diag_posture_label.set_text(interpretation.get("market_posture", "-"))
            self.diag_margin_label.set_text(interpretation.get("margin_and_leverage", "-"))
            self.diag_liq_label.set_text(interpretation.get("liquidation_safety", "-"))
            
            cvar_pct = var_metrics.get("cvar_95_pct", 0.0)
            self.diag_var_label.set_text(f"VaR 95%: {var_95_pct:.1f}% | CVaR 95%: {cvar_pct:.1f}% | Riesgo {var_metrics.get('risk_category', 'BAJO')}")

            # Recomendaciones
            self.diag_recommendations_container.clear()
            with self.diag_recommendations_container:
                recs = interpretation.get("recommendations", [])
                for r in recs:
                    ui.label(f"• {r}")

        # 3. Actualizar Gráficos Plotly de Riesgo
        stress_res = risk.get("stress_test", [])
        if stress_res and hasattr(self, 'stress_chart'):
            self.stress_chart.update_figure(self._build_stress_chart_fig(stress_res, tot_usd))

        if hasattr(self, 'allocation_chart'):
            self.allocation_chart.update_figure(self._build_allocation_chart_fig(metrics))

        # 4. Tabla de Activos
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

        # 6. Tabla de Órdenes Abiertas
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
            title=dict(text="Composición de Cartera", font=dict(color="#94a3b8", size=12))
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


def render_binance_account_page():
    page = BinanceAccountPage()
    page.render()
    return page
