import os
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional
from nicegui import ui
from execution_engine.binance_client import BinanceTestnetClient

def _format_time(ts) -> str:
    """Formatea timestamps de Binance en DD/MM HH:mm:ss."""
    if not ts:
        return "-"
    try:
        if isinstance(ts, (int, float)):
            # Si el timestamp viene en milisegundos
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
        self.selected_network = "testnet" # 'testnet' o 'mainnet'
        self.account_data: Dict[str, Any] = {}
        self.is_loading = False

    def render(self):
        with ui.column().classes('w-full h-full p-2 md:p-4 gap-6 bg-[#0a0e17] text-white'):
            
            # ──────────────────────────────────────────────────────────────
            # 1. Cabecera Principal y Selector de Red
            # ──────────────────────────────────────────────────────────────
            with ui.row().classes('w-full justify-between items-center pb-4 border-b border-gray-800 flex-wrap gap-4'):
                with ui.column().classes('gap-1'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('account_balance_wallet', size='32px', color='yellow-400')
                        ui.label('Información de Cuenta y Exchange Binance').classes('text-2xl md:text-3xl font-extrabold text-white tracking-tight font-heading')
                    ui.label('Monitoreo en tiempo real de balances, posiciones, órdenes y diagnóstico de conectividad').classes('text-xs md:text-sm text-gray-400')

                with ui.row().classes('gap-3 items-center flex-wrap'):
                    # Selector de Red
                    with ui.row().classes('bg-gray-950 p-1 rounded-xl border border-gray-800 gap-1'):
                        self.btn_testnet = ui.button(
                            '🟡 Binance Futures Testnet', 
                            on_click=lambda: self._switch_network('testnet')
                        ).props('dense').classes('bg-yellow-500 text-black text-xs font-bold px-3 py-1.5 rounded-lg transition-all shadow')
                        
                        self.btn_mainnet = ui.button(
                            '🌐 Binance Real (Mainnet)', 
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

            # ──────────────────────────────────────────────────────────────
            # 2. Tarjetas de Diagnóstico de Conectividad (Testnet y Mainnet)
            # ──────────────────────────────────────────────────────────────
            with ui.grid(columns=2).classes('w-full gap-4'):
                
                # Test Card 1: Binance Futures Testnet
                with ui.card().classes('bg-gray-900/90 border border-yellow-500/30 p-4 rounded-2xl shadow-lg flex flex-col justify-between'):
                    with ui.row().classes('w-full justify-between items-center mb-2'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('speed', color='yellow-400', size='22px')
                            ui.label('Diagnóstico: Binance Futures Testnet').classes('text-sm font-bold text-yellow-400')
                        self.testnet_status_badge = ui.badge('Pendiente', color='gray-800').props('rounded').classes('text-xs text-gray-400')
                    
                    self.testnet_diag_content = ui.column().classes('w-full text-xs text-gray-300 gap-1 my-2')
                    with self.testnet_diag_content:
                        ui.label('Haz clic en el botón para probar ping, consulta de cuenta y envío/cierre de orden de prueba.').classes('text-gray-400 italic')
                    
                    with ui.row().classes('w-full justify-between items-center mt-2 pt-2 border-t border-gray-800'):
                        ui.button(
                            '🧪 Ejecutar Test de Testnet', 
                            icon='play_arrow', 
                            on_click=self._run_testnet_diagnostic
                        ).classes('bg-yellow-500 hover:bg-yellow-600 text-black font-bold text-xs px-3 py-1.5 rounded-lg shadow')
                        ui.link('🔗 Abrir Testnet Web', 'https://testnet.binancefuture.com/en/futures/BTCUSDT', new_tab=True).classes('text-xs text-yellow-400 underline font-semibold')

                # Test Card 2: Binance Real (Mainnet)
                with ui.card().classes('bg-gray-900/90 border border-blue-500/30 p-4 rounded-2xl shadow-lg flex flex-col justify-between'):
                    with ui.row().classes('w-full justify-between items-center mb-2'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('language', color='blue-400', size='22px')
                            ui.label('Diagnóstico: Binance Real (Mainnet)').classes('text-sm font-bold text-blue-400')
                        self.mainnet_status_badge = ui.badge('Pendiente', color='gray-800').props('rounded').classes('text-xs text-gray-400')
                    
                    self.mainnet_diag_content = ui.column().classes('w-full text-xs text-gray-300 gap-1 my-2')
                    with self.mainnet_diag_content:
                        ui.label('Haz clic en el botón para verificar ping, latencia global y estado de servidores de Binance.').classes('text-gray-400 italic')
                    
                    with ui.row().classes('w-full justify-between items-center mt-2 pt-2 border-t border-gray-800'):
                        ui.button(
                            '🧪 Ejecutar Test de Mainnet', 
                            icon='play_arrow', 
                            on_click=self._run_mainnet_diagnostic
                        ).classes('bg-blue-600 hover:bg-blue-700 text-white font-bold text-xs px-3 py-1.5 rounded-lg shadow')
                        ui.link('🔗 Estado Oficial Binance', 'https://www.binance.com', new_tab=True).classes('text-xs text-blue-400 underline font-semibold')

            # ──────────────────────────────────────────────────────────────
            # 3. KPIs de Alto Nivel de la Cuenta
            # ──────────────────────────────────────────────────────────────
            with ui.grid(columns=4).classes('w-full gap-4'):
                # KPI 1: Valor Total Consolidado en USD (Multiactivo)
                with ui.card().classes('bg-gray-900 p-4 rounded-2xl border border-gray-800 shadow-md flex flex-col justify-between'):
                    ui.label('VALOR TOTAL CARTERA (USD)').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                    self.kpi_wallet_balance = ui.label('$0.00 USD').classes('text-2xl font-black text-green-400 mt-1 font-mono')
                    self.kpi_net_badge = ui.label('USDT + USDC + BTC').classes('text-[11px] text-gray-400 font-medium')

                # KPI 2: Margen Disponible en USDT
                with ui.card().classes('bg-gray-900 p-4 rounded-2xl border border-gray-800 shadow-md flex flex-col justify-between'):
                    ui.label('SALDO USDT DISPONIBLE').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                    self.kpi_avail_margin = ui.label('0.00 USDT').classes('text-2xl font-black text-yellow-400 mt-1 font-mono')
                    ui.label('Margen libre para operar contratos USDT-M').classes('text-[11px] text-gray-500')

                # KPI 3: PnL No Realizado
                with ui.card().classes('bg-gray-900 p-4 rounded-2xl border border-gray-800 shadow-md flex flex-col justify-between'):
                    ui.label('PNL NO REALIZADO TOTAL').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                    self.kpi_unrealized_pnl = ui.label('+0.00 USDT').classes('text-2xl font-black text-white mt-1 font-mono')
                    self.kpi_margin_balance = ui.label('Margen Total: 0.00 USDT').classes('text-[11px] text-gray-500')

                # KPI 4: Posiciones y Órdenes
                with ui.card().classes('bg-gray-900 p-4 rounded-2xl border border-gray-800 shadow-md flex flex-col justify-between'):
                    ui.label('POSICIONES & ÓRDENES ACTIVAS').classes('text-[10px] font-bold text-gray-400 uppercase tracking-wider')
                    self.kpi_positions_count = ui.label('0 Posiciones | 0 Órdenes').classes('text-lg font-bold text-blue-400 mt-1')
                    self.kpi_api_status = ui.label('API: Conectada').classes('text-[11px] text-emerald-400 font-semibold')

            # ──────────────────────────────────────────────────────────────
            # 4. Tabla de Saldos y Activos en la Billetera
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
            # 5. Posiciones Abiertas en Binance Futures
            # ──────────────────────────────────────────────────────────────
            with ui.card().classes('bg-gray-900 p-5 rounded-2xl border border-gray-800 w-full shadow-xl'):
                with ui.row().classes('w-full justify-between items-center mb-3'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('show_chart', color='blue-400', size='22px')
                        ui.label('Posiciones Abiertas en Binance Futures').classes('text-lg font-bold text-white font-heading')
                    ui.label('Posiciones activas actualmente en el exchange').classes('text-xs text-gray-400 italic')

                self.positions_grid = ui.aggrid({
                    'defaultColDef': {'flex': 1, 'sortable': True, 'resizable': True},
                    'columnDefs': [
                        {'headerName': 'Símbolo / Contrato', 'field': 'symbol_display', 'maxWidth': 160, 'cellClass': 'font-bold text-white'},
                        {'headerName': 'Tamaño (Size)',      'field': 'size_display',   'maxWidth': 130, 'cellClass': 'font-mono text-yellow-300 font-bold'},
                        {'headerName': 'Precio Entrada',     'field': 'entry_price',    'maxWidth': 140, 'cellClass': 'font-mono text-gray-200'},
                        {'headerName': 'Break Even',         'field': 'break_even',     'maxWidth': 140, 'cellClass': 'font-mono text-gray-400'},
                        {'headerName': 'Precio Marca',       'field': 'mark_price',     'maxWidth': 140, 'cellClass': 'font-mono text-sky-400'},
                        {'headerName': 'Liq. Price',         'field': 'liq_price',      'maxWidth': 120, 'cellClass': 'font-mono text-red-400'},
                        {'headerName': 'Margen',             'field': 'margin_display', 'maxWidth': 150, 'cellClass': 'font-mono text-blue-300'},
                        {'headerName': 'PNL (ROI %)',        'field': 'pnl_display',    'maxWidth': 180, 'cellClass': 'font-mono font-bold'},
                    ],
                    'rowData': [],
                    'rowClassRules': {
                        'text-green-400 font-semibold': 'data.raw_pnl > 0',
                        'text-red-400 font-semibold':   'data.raw_pnl < 0',
                    }
                }).classes('h-44 text-white')

            # ──────────────────────────────────────────────────────────────
            # 6. Órdenes Abiertas (Open Orders) y Cancelación
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
            # 7. Historial Reciente de Órdenes Ejecutadas en Binance
            # ──────────────────────────────────────────────────────────────
            with ui.card().classes('bg-gray-900 p-5 rounded-2xl border border-gray-800 w-full shadow-xl'):
                with ui.row().classes('w-full justify-between items-center mb-3'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('history', color='yellow-400', size='22px')
                        ui.label('Historial Reciente de Órdenes en Binance (Exchange Log)').classes('text-lg font-bold text-white font-heading')
                    ui.label('Últimas órdenes emitidas hacia el exchange').classes('text-xs text-gray-400 italic')

                self.recent_orders_grid = ui.aggrid({
                    'defaultColDef': {'flex': 1, 'sortable': True, 'resizable': True},
                    'columnDefs': [
                        {'headerName': 'ID Orden',       'field': 'orderId',      'maxWidth': 150, 'cellClass': 'font-mono text-xs text-gray-400'},
                        {'headerName': 'Fecha/Hora',     'field': 'time_str',     'maxWidth': 135, 'cellClass': 'font-mono text-xs text-gray-300 text-center'},
                        {'headerName': 'Símbolo',        'field': 'symbol',       'maxWidth': 120, 'cellClass': 'font-bold text-white'},
                        {'headerName': 'Lado',           'field': 'side_display', 'maxWidth': 110},
                        {'headerName': 'Tipo',           'field': 'type',         'maxWidth': 120},
                        {'headerName': 'Cantidad',       'field': 'origQty',      'maxWidth': 120, 'cellClass': 'font-mono'},
                        {'headerName': 'Precio Ejecución','field': 'avgPrice',     'maxWidth': 140, 'cellClass': 'font-mono text-green-400 font-semibold'},
                        {'headerName': 'Estado',         'field': 'status',       'maxWidth': 120, 'cellClass': 'font-bold'},
                    ],
                    'rowData': [],
                    'rowClassRules': {
                        'text-green-400 font-semibold': 'data.status === "FILLED"',
                        'text-red-400':                 'data.status === "CANCELED" || data.status === "REJECTED"',
                    }
                }).classes('h-56 text-white')

            # ──────────────────────────────────────────────────────────────
            # 8. Información de Seguridad y Credenciales API
            # ──────────────────────────────────────────────────────────────
            with ui.card().classes('bg-gray-900/60 p-4 rounded-xl border border-gray-800 w-full'):
                with ui.row().classes('w-full justify-between items-center flex-wrap gap-2 text-xs text-gray-400'):
                    k = os.getenv("BINANCE_API_KEY", "")
                    s = os.getenv("BINANCE_SECRET_KEY", "")
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('vpn_key', color='yellow-400', size='18px')
                        ui.label(f"API Key: {_obfuscate_key(k)} | Secret Key: {_obfuscate_key(s)}").classes('font-mono text-gray-300')
                    
                    with ui.row().classes('items-center gap-2'):
                        ui.label("Endpoints:").classes('text-gray-500')
                        ui.badge("Testnet: testnet.binancefuture.com", color='yellow-950').props('rounded').classes('text-yellow-300 text-[10px]')
                        ui.badge("Mainnet: api.binance.com", color='blue-950').props('rounded').classes('text-blue-300 text-[10px]')

            # Cargar datos iniciales al abrir y activar refresco periódico en vivo cada 3 segundos
            ui.timer(0.2, self._refresh_account_data_async, once=True)
            self.live_timer = ui.timer(3.0, self._refresh_account_data_async)
            ui.context.client.on_disconnect(lambda: self.live_timer.deactivate() if hasattr(self, 'live_timer') and self.live_timer else None)

    # ──────────────────────────────────────────────────────────────
    # Métodos y Acciones
    # ──────────────────────────────────────────────────────────────

    def _switch_network(self, net: str):
        self.selected_network = net
        if net == "testnet":
            self.btn_testnet.classes('bg-yellow-500 text-black font-bold', remove='text-gray-400')
            self.btn_mainnet.classes('text-gray-400 hover:text-white', remove='bg-yellow-500 text-black font-bold')
            self.kpi_net_badge.set_text('Entorno: Binance Futures Testnet')
        else:
            self.btn_mainnet.classes('bg-blue-600 text-white font-bold', remove='text-gray-400')
            self.btn_testnet.classes('text-gray-400 hover:text-white', remove='bg-yellow-500 text-black font-bold')
            self.kpi_net_badge.set_text('Entorno: Binance Real (Mainnet)')
        
        asyncio.create_task(self._refresh_account_data_async())

    async def _refresh_account_data_async(self):
        """Descarga de forma asíncrona todos los datos de la cuenta sin congelar la UI."""
        if self.is_loading:
            return
        self.is_loading = True
        self.btn_refresh.props('loading')

        use_test = (self.selected_network == "testnet")
        client = BinanceTestnetClient(use_testnet=use_test)

        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: client.get_full_account_info(use_testnet=use_test))

        self.account_data = data
        self._update_ui_with_account_data(data)

        self.btn_refresh.props(remove='loading')
        self.is_loading = False

    def _update_ui_with_account_data(self, data: Dict[str, Any]):
        if not data.get("success"):
            err = data.get("error", "Error desconocido")
            ui.notify(f"⚠️ {err}", type='warning')
            self.kpi_api_status.set_text(f"API: Error")
            self.kpi_api_status.classes('text-red-400', remove='text-emerald-400')
            return

        self.kpi_api_status.set_text("API: Conectada 🟢")
        self.kpi_api_status.classes('text-emerald-400', remove='text-red-400')

        # Estado del Modo Multiactivos (BTC como garantía global)
        is_multi = data.get("multi_assets_margin", False)
        if hasattr(self, 'multi_assets_btn'):
            if is_multi:
                self.multi_assets_btn.set_text("🔀 Multiactivos (BTC Colateral): ACTIVO ✅")
                self.multi_assets_btn.classes('bg-green-950 text-green-300 border-green-500/60 font-bold', remove='text-yellow-300 border-yellow-500/40 bg-gray-900')
            else:
                self.multi_assets_btn.set_text("🔀 Multiactivos (BTC Colateral): INACTIVO ⚪")
                self.multi_assets_btn.classes('bg-gray-900 text-gray-400 border-gray-700 font-normal', remove='bg-green-950 text-green-300 border-green-500/60')

        # 1. KPIs
        total_usd = data.get("total_usd_value", 0.0)
        tot_bal = data.get("total_wallet_balance", 0.0)
        avail_bal = data.get("available_balance", 0.0)
        upnl = data.get("total_unrealized_pnl", 0.0)
        tot_margin = data.get("total_margin_balance", 0.0)

        # Mostrar el total consolidado multiactivo en USD
        disp_usd = total_usd if total_usd > 0 else tot_bal
        self.kpi_wallet_balance.set_text(f"${disp_usd:,.2f} USD")
        self.kpi_avail_margin.set_text(f"{avail_bal:,.2f} USDT")
        
        sign = "+" if upnl >= 0 else ""
        self.kpi_unrealized_pnl.set_text(f"{sign}{upnl:,.2f} USDT")
        self.kpi_unrealized_pnl.classes('text-green-400' if upnl >= 0 else 'text-red-400', remove='text-white text-green-400 text-red-400')
        self.kpi_margin_balance.set_text(f"Margen Total: {tot_margin:,.2f} USDT")

        pos_count = len(data.get("positions", []))
        orders_count = len(data.get("open_orders", []))
        self.kpi_positions_count.set_text(f"{pos_count} Posiciones | {orders_count} Órdenes")

        # 2. Assets Table
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

        # 3. Positions Table
        pos_rows = []
        for p in data.get("positions", []):
            side_icon = "📈 " if p['side'] == "LONG" else "📉 "
            raw_pnl = float(p.get('unrealized_pnl', 0.0))
            roi_pct = float(p.get('roi_pct', 0.0))
            pos_rows.append({
                "symbol_display": f"{p['symbol']} Perp {p.get('leverage', 1)}x",
                "size_display": f"{side_icon} {p.get('amount', 0):.4f} {p['symbol'].replace('USDT', '').replace('USDC', '')}",
                "entry_price": f"{p['entry_price']:,.2f}",
                "break_even": f"{p.get('break_even_price', p['entry_price']):,.2f}",
                "mark_price": f"{p.get('mark_price', 0):,.2f}" if p.get('mark_price') else "-",
                "liq_price": f"{p['liquidation_price']:,.2f}" if p.get('liquidation_price') else "--",
                "margin_display": p.get('margin_display', f"{p['initial_margin']:,.2f} USDT"),
                "pnl_display": f"{raw_pnl:+,.2f} USDT ({roi_pct:+.2f}%)",
                "raw_pnl": raw_pnl
            })
        self.positions_grid.options['rowData'] = pos_rows
        self.positions_grid.update()

        # 4. Open Orders Table
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

        # 5. Recent Trades Table
        trade_rows = []
        for t in data.get("recent_trades", []):
            side = t.get("side", "")
            side_icon = "🟢 BUY" if side == "BUY" else "🔴 SELL"
            trade_rows.append({
                "orderId": str(t.get("orderId")),
                "time_str": _format_time(t.get("time")),
                "symbol": t.get("symbol"),
                "side_display": side_icon,
                "type": t.get("type"),
                "origQty": f"{t.get('origQty', 0):.4f}",
                "avgPrice": f"{t.get('avgPrice', 0):,.2f}",
                "status": t.get("status")
            })
        self.recent_orders_grid.options['rowData'] = trade_rows
        self.recent_orders_grid.update()

    async def _run_testnet_diagnostic(self):
        self.testnet_status_badge.set_text("Ejecutando...")
        self.testnet_status_badge.classes('bg-yellow-600 text-black', remove='bg-gray-800 text-gray-400')
        self.testnet_diag_content.clear()
        with self.testnet_diag_content:
            ui.spinner('dots', size='sm', color='yellow-400')
            ui.label('Probando ping, saldo y enviando orden de prueba...').classes('text-xs text-yellow-400 italic')

        client = BinanceTestnetClient(use_testnet=True)
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, lambda: client.test_testnet_connection(symbol="BTC/USDT"))

        self.testnet_diag_content.clear()
        with self.testnet_diag_content:
            if res.get("success"):
                self.testnet_status_badge.set_text("✅ Operativo")
                self.testnet_status_badge.classes('bg-green-600 text-white', remove='bg-yellow-600 text-black')
                ui.label(f"• Latencia Ping: {res.get('latency_ms')} ms | Conexión OK").classes('text-green-400 font-semibold')
                ui.label(f"• Saldo Testnet: {res.get('account_balance_usdt', 0.0):,.2f} USDT ({res.get('assets_count')} activos)").classes('text-gray-200')
                buy = res.get('buy_order', {})
                sell = res.get('sell_order', {})
                ui.label(f"• Orden de Entrada: BUY {buy.get('origQty')} BTC | ID: {buy.get('orderId')} ({buy.get('status')})").classes('text-blue-300')
                ui.label(f"• Orden de Cierre: SELL {sell.get('origQty')} BTC | ID: {sell.get('orderId')} ({sell.get('status')})").classes('text-orange-300')
                ui.notify("✅ Test de Binance Testnet completado con éxito!", type='positive')
                # Refrescar tablas
                await self._refresh_account_data_async()
            else:
                self.testnet_status_badge.set_text("❌ Error")
                self.testnet_status_badge.classes('bg-red-600 text-white', remove='bg-yellow-600 text-black')
                ui.label(f"• Error: {res.get('error')}").classes('text-red-400 font-mono')
                ui.notify(f"❌ Error en Testnet: {res.get('error')}", type='negative')

    async def _run_mainnet_diagnostic(self):
        self.mainnet_status_badge.set_text("Ejecutando...")
        self.mainnet_status_badge.classes('bg-blue-600 text-white', remove='bg-gray-800 text-gray-400')
        self.mainnet_diag_content.clear()
        with self.mainnet_diag_content:
            ui.spinner('dots', size='sm', color='blue-400')
            ui.label('Verificando latencia, estado del sistema y servidores Binance...').classes('text-xs text-blue-400 italic')

        client = BinanceTestnetClient(use_testnet=False)
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, client.test_mainnet_connection)

        self.mainnet_diag_content.clear()
        with self.mainnet_diag_content:
            if res.get("success"):
                self.mainnet_status_badge.set_text("✅ En Línea")
                self.mainnet_status_badge.classes('bg-green-600 text-white', remove='bg-blue-600 text-white')
                ui.label(f"• Latencia Servidores: {res.get('latency_ms')} ms").classes('text-green-400 font-semibold')
                ui.label(f"• Estado del Sistema Binance: {res.get('system_status')}").classes('text-gray-200')
                ui.label(f"• Sincronización Reloj: Offset {res.get('server_time_offset_ms')} ms").classes('text-gray-300')
                if res.get('account_note'):
                    ui.label(f"• {res.get('account_note')}").classes('text-gray-400 text-[11px] italic')
                ui.notify("✅ Conexión con Binance Mainnet verificada!", type='positive')
            else:
                self.mainnet_status_badge.set_text("❌ Error")
                self.mainnet_status_badge.classes('bg-red-600 text-white', remove='bg-blue-600 text-white')
                ui.label(f"• Error: {res.get('error')}").classes('text-red-400 font-mono')
                ui.notify(f"❌ Error en Mainnet: {res.get('error')}", type='negative')

    async def _toggle_multi_assets_mode(self):
        """Conmuta el Modo Multiactivos (Multi-Assets Margin) en Binance Futures."""
        use_test = (self.selected_network == "testnet")
        client = BinanceTestnetClient(use_testnet=use_test)
        
        current_state = bool(self.account_data.get("multi_assets_margin", False)) if self.account_data else False
        new_target = not current_state
        
        ui.notify(f"Configurando Modo Multiactivos en Binance a {'ACTIVO' if new_target else 'INACTIVO'}...", type='info')
        loop = asyncio.get_event_loop()
        ok, err = await loop.run_in_executor(None, lambda: client.set_multi_assets_margin(new_target))
        
        if ok:
            ui.notify(f"✅ Modo Multiactivos {'ACTIVADO' if new_target else 'DESACTIVADO'} en Binance (BTC listo como garantía global).", type='positive')
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
