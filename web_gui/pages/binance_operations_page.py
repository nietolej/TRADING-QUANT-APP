"""
Página de Operativa Completa de Binance (Spot & Futuros).
Replica las mismas pestañas que la interfaz real de Binance: Posiciones, Órdenes Abiertas,
Historial de Órdenes, Historial de Trades, Historial de Transacciones y Activos — tanto para
Testnet como para cuenta Real.
"""
from datetime import datetime
from typing import Dict, Any, List, Optional
from nicegui import ui

from execution_engine.binance_client import BinanceTestnetClient

COMMON_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT', 'DOGEUSDT']

FUTURES_INCOME_TYPES = [
    'Todos', 'TRANSFER', 'REALIZED_PNL', 'FUNDING_FEE', 'COMMISSION',
    'INSURANCE_CLEAR', 'API_REBATE',
]


def _grid(columns: List[Dict[str, Any]], height: str = 'h-72', row_rules: Optional[Dict[str, str]] = None) -> ui.aggrid:
    opts = {
        'defaultColDef': {'flex': 1, 'sortable': True, 'resizable': True},
        'columnDefs': columns,
        'rowData': [],
    }
    if row_rules:
        opts['rowClassRules'] = row_rules
    return ui.aggrid(opts).classes(f'{height} text-white w-full')


class BinanceOperationsPage:
    def __init__(self):
        self.selected_network = "testnet"
        self.selected_wallet = "futures"
        self.symbol_futures = "BTCUSDT"
        self.symbol_spot = "BTCUSDT"
        self.income_type = "Todos"
        self.is_loading = False

    # ──────────────────────────────────────────────────────────────
    def render(self):
        with ui.column().classes('w-full h-full p-2 md:p-4 gap-6 bg-[#0a0e17] text-white'):

            with ui.row().classes('w-full justify-between items-center pb-4 border-b border-gray-800 flex-wrap gap-4'):
                with ui.column().classes('gap-1'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('receipt_long', size='32px', color='yellow-400')
                        ui.label('Operativa Binance (Spot & Futuros)').classes('text-2xl md:text-3xl font-extrabold text-white tracking-tight font-heading')
                    ui.label('Vista completa de posiciones, órdenes y transacciones, igual que en la web/app real de Binance').classes('text-xs md:text-sm text-gray-400')

                with ui.row().classes('gap-3 items-center flex-wrap'):
                    with ui.row().classes('bg-gray-950 p-1 rounded-xl border border-gray-800 gap-1 shadow-inner'):
                        self.btn_testnet = ui.button('🟡 Testnet', on_click=lambda: self._switch_network('testnet')) \
                            .props('dense').classes('bg-yellow-500 text-black text-xs font-bold px-3 py-1.5 rounded-lg transition-all shadow')
                        self.btn_mainnet = ui.button('🌐 Real (Mainnet)', on_click=lambda: self._switch_network('mainnet')) \
                            .props('dense flat').classes('text-gray-400 hover:text-white text-xs font-semibold px-3 py-1.5 rounded-lg transition-all')

                    self.btn_refresh = ui.button('Actualizar', icon='refresh', on_click=self._refresh_all) \
                        .classes('bg-gray-800 hover:bg-gray-700 text-white font-bold text-xs px-3 py-2 rounded-xl shadow border border-gray-700')

            with ui.row().classes('w-full justify-between items-center bg-gray-900/90 p-3.5 rounded-2xl border border-gray-800 flex-wrap gap-4 shadow-lg'):
                ui.label('CARTERA:').classes('text-xs font-black text-gray-300 tracking-wider')
                with ui.row().classes('bg-gray-950 p-1.5 rounded-xl border border-gray-800 gap-1.5 shadow-inner'):
                    self.btn_wallet_futures = ui.button('⚡ Futuros (USDⓈ-M)', on_click=lambda: self._switch_wallet('futures')) \
                        .props('dense').classes('bg-yellow-500 text-black text-xs font-black px-4 py-2 rounded-lg transition-all shadow')
                    self.btn_wallet_spot = ui.button('🪙 Spot (Contado)', on_click=lambda: self._switch_wallet('spot')) \
                        .props('dense flat').classes('text-gray-400 hover:text-white text-xs font-bold px-4 py-2 rounded-lg transition-all')
                self.status_badge = ui.badge('Cargando...', color='gray-800').classes('text-xs px-3 py-2 rounded-xl')

            # ── FUTUROS ──
            self.container_futures = ui.column().classes('w-full gap-4')
            with self.container_futures:
                self._render_futures_tabs()

            # ── SPOT ──
            self.container_spot = ui.column().classes('w-full gap-4')
            self.container_spot.set_visibility(False)
            with self.container_spot:
                self._render_spot_tabs()

        ui.timer(0.5, self._refresh_all, once=True)

    # ──────────────────────────────────────────────────────────────
    def _render_futures_tabs(self):
        with ui.card().classes('bg-gray-900 p-2 rounded-2xl border border-gray-800 w-full shadow-xl'):
            with ui.tabs().classes('w-full text-gray-300') as tabs:
                ui.tab('positions', label='Positions')
                ui.tab('open_orders', label='Open Orders')
                ui.tab('order_history', label='Order History')
                ui.tab('trade_history', label='Trade History')
                ui.tab('transactions', label='Transaction History')
                ui.tab('assets', label='Assets')

            with ui.tab_panels(tabs, value='positions').classes('w-full bg-transparent'):
                with ui.tab_panel('positions'):
                    self.fut_positions_grid = _grid([
                        {'headerName': 'Symbol', 'field': 'symbol_display', 'maxWidth': 140, 'cellClass': 'font-bold text-white'},
                        {'headerName': 'Size', 'field': 'size_display', 'maxWidth': 130, 'cellClass': 'font-mono text-yellow-300'},
                        {'headerName': 'Entry Price', 'field': 'entry_price', 'maxWidth': 120, 'cellClass': 'font-mono'},
                        {'headerName': 'Break Even', 'field': 'break_even_price', 'maxWidth': 120, 'cellClass': 'font-mono text-gray-400'},
                        {'headerName': 'Mark Price', 'field': 'mark_price', 'maxWidth': 120, 'cellClass': 'font-mono text-sky-400'},
                        {'headerName': 'Liq.Price', 'field': 'liquidation_price', 'maxWidth': 120, 'cellClass': 'font-mono text-red-400'},
                        {'headerName': 'Margin', 'field': 'margin_display', 'maxWidth': 150, 'cellClass': 'font-mono text-blue-300'},
                        {'headerName': 'PNL(ROI%)', 'field': 'pnl_display', 'maxWidth': 180, 'cellClass': 'font-mono font-bold'},
                    ], row_rules={'text-green-400': 'data.unrealized_pnl > 0', 'text-red-400': 'data.unrealized_pnl < 0'})

                with ui.tab_panel('open_orders'):
                    self.fut_open_orders_grid = _grid([
                        {'headerName': 'Date', 'field': 'time_str', 'maxWidth': 140, 'cellClass': 'font-mono text-xs text-gray-400'},
                        {'headerName': 'Symbol', 'field': 'symbol', 'maxWidth': 110, 'cellClass': 'font-bold'},
                        {'headerName': 'Type', 'field': 'type', 'maxWidth': 130},
                        {'headerName': 'Side', 'field': 'side', 'maxWidth': 90},
                        {'headerName': 'Price', 'field': 'price', 'maxWidth': 110, 'cellClass': 'font-mono'},
                        {'headerName': 'Amount', 'field': 'origQty', 'maxWidth': 110, 'cellClass': 'font-mono'},
                        {'headerName': 'Stop Price', 'field': 'stopPrice', 'maxWidth': 110, 'cellClass': 'font-mono text-yellow-400'},
                        {'headerName': 'ID', 'field': 'orderId', 'maxWidth': 140, 'cellClass': 'font-mono text-gray-500 text-xs'},
                    ])

                with ui.tab_panel('order_history'):
                    with ui.row().classes('items-center gap-2 mb-2'):
                        ui.label('Símbolo:').classes('text-xs text-gray-400')
                        self.fut_oh_symbol = ui.select(COMMON_SYMBOLS, value=self.symbol_futures, new_value_mode='add-unique') \
                            .classes('w-40').props('dense outline').on_value_change(self._refresh_futures_order_history)
                    self.fut_order_history_grid = _grid([
                        {'headerName': 'Date', 'field': 'time_str', 'maxWidth': 140, 'cellClass': 'font-mono text-xs text-gray-400'},
                        {'headerName': 'Symbol', 'field': 'symbol', 'maxWidth': 110, 'cellClass': 'font-bold'},
                        {'headerName': 'Type', 'field': 'type', 'maxWidth': 120},
                        {'headerName': 'Side', 'field': 'side', 'maxWidth': 90},
                        {'headerName': 'Avg Price', 'field': 'avgPrice', 'maxWidth': 110, 'cellClass': 'font-mono'},
                        {'headerName': 'Amount', 'field': 'origQty', 'maxWidth': 110, 'cellClass': 'font-mono'},
                        {'headerName': 'Filled', 'field': 'executedQty', 'maxWidth': 110, 'cellClass': 'font-mono'},
                        {'headerName': 'Status', 'field': 'status', 'maxWidth': 120, 'cellClass': 'font-bold'},
                    ], row_rules={'text-emerald-400': "data.status == 'FILLED'", 'text-gray-500': "data.status == 'CANCELED'", 'text-red-400': "data.status == 'EXPIRED'"})

                with ui.tab_panel('trade_history'):
                    with ui.row().classes('items-center gap-2 mb-2'):
                        ui.label('Símbolo:').classes('text-xs text-gray-400')
                        self.fut_th_symbol = ui.select(COMMON_SYMBOLS, value=self.symbol_futures, new_value_mode='add-unique') \
                            .classes('w-40').props('dense outline').on_value_change(self._refresh_futures_trade_history)
                    self.fut_trade_history_grid = _grid([
                        {'headerName': 'Date', 'field': 'time_str', 'maxWidth': 140, 'cellClass': 'font-mono text-xs text-gray-400'},
                        {'headerName': 'Symbol', 'field': 'symbol', 'maxWidth': 110, 'cellClass': 'font-bold'},
                        {'headerName': 'Side', 'field': 'side', 'maxWidth': 90},
                        {'headerName': 'Price', 'field': 'price', 'maxWidth': 110, 'cellClass': 'font-mono'},
                        {'headerName': 'Qty', 'field': 'qty', 'maxWidth': 110, 'cellClass': 'font-mono'},
                        {'headerName': 'Fee', 'field': 'commission', 'maxWidth': 110, 'cellClass': 'font-mono text-gray-400'},
                        {'headerName': 'Realized PNL', 'field': 'realizedPnl', 'maxWidth': 130, 'cellClass': 'font-mono font-bold'},
                    ], row_rules={'text-green-400': 'data.realizedPnl > 0', 'text-red-400': 'data.realizedPnl < 0'})

                with ui.tab_panel('transactions'):
                    with ui.row().classes('items-center gap-2 mb-2'):
                        ui.label('Tipo:').classes('text-xs text-gray-400')
                        self.fut_income_type = ui.select(FUTURES_INCOME_TYPES, value=self.income_type) \
                            .classes('w-48').props('dense outline').on_value_change(self._refresh_futures_transactions)
                    self.fut_transactions_grid = _grid([
                        {'headerName': 'Date', 'field': 'time_str', 'maxWidth': 140, 'cellClass': 'font-mono text-xs text-gray-400'},
                        {'headerName': 'Symbol', 'field': 'symbol', 'maxWidth': 110, 'cellClass': 'font-bold'},
                        {'headerName': 'Type', 'field': 'type', 'maxWidth': 160},
                        {'headerName': 'Amount', 'field': 'income', 'maxWidth': 130, 'cellClass': 'font-mono font-bold'},
                        {'headerName': 'Asset', 'field': 'asset', 'maxWidth': 100},
                    ], row_rules={'text-green-400': 'data.income > 0', 'text-red-400': 'data.income < 0'})

                with ui.tab_panel('assets'):
                    self.fut_assets_grid = _grid([
                        {'headerName': 'Asset', 'field': 'asset', 'maxWidth': 110, 'cellClass': 'font-bold text-yellow-400'},
                        {'headerName': 'Wallet Balance', 'field': 'wallet_balance', 'maxWidth': 150, 'cellClass': 'font-mono text-green-400'},
                        {'headerName': 'Available', 'field': 'available_balance', 'maxWidth': 150, 'cellClass': 'font-mono'},
                        {'headerName': 'Margin Balance', 'field': 'margin_balance', 'maxWidth': 150, 'cellClass': 'font-mono text-blue-300'},
                        {'headerName': 'Unrealized PNL', 'field': 'unrealized_pnl', 'maxWidth': 150, 'cellClass': 'font-mono'},
                    ], height='h-56')

    # ──────────────────────────────────────────────────────────────
    def _render_spot_tabs(self):
        with ui.card().classes('bg-gray-900 p-2 rounded-2xl border border-gray-800 w-full shadow-xl'):
            with ui.tabs().classes('w-full text-gray-300') as tabs:
                ui.tab('open_orders', label='Open Orders')
                ui.tab('order_history', label='Order History')
                ui.tab('trade_history', label='Trade History')
                ui.tab('transactions', label='Transaction History')
                ui.tab('assets', label='Assets')

            with ui.tab_panels(tabs, value='open_orders').classes('w-full bg-transparent'):
                with ui.tab_panel('open_orders'):
                    self.spot_open_orders_grid = _grid([
                        {'headerName': 'Date', 'field': 'time_str', 'maxWidth': 140, 'cellClass': 'font-mono text-xs text-gray-400'},
                        {'headerName': 'Pair', 'field': 'symbol', 'maxWidth': 110, 'cellClass': 'font-bold'},
                        {'headerName': 'Type', 'field': 'type', 'maxWidth': 120},
                        {'headerName': 'Side', 'field': 'side', 'maxWidth': 90},
                        {'headerName': 'Price', 'field': 'price', 'maxWidth': 110, 'cellClass': 'font-mono'},
                        {'headerName': 'Amount', 'field': 'origQty', 'maxWidth': 110, 'cellClass': 'font-mono'},
                        {'headerName': 'ID', 'field': 'orderId', 'maxWidth': 140, 'cellClass': 'font-mono text-gray-500 text-xs'},
                    ])

                with ui.tab_panel('order_history'):
                    with ui.row().classes('items-center gap-2 mb-2'):
                        ui.label('Símbolo:').classes('text-xs text-gray-400')
                        self.spot_oh_symbol = ui.select(COMMON_SYMBOLS, value=self.symbol_spot, new_value_mode='add-unique') \
                            .classes('w-40').props('dense outline').on_value_change(self._refresh_spot_order_history)
                    self.spot_order_history_grid = _grid([
                        {'headerName': 'Date', 'field': 'time_str', 'maxWidth': 140, 'cellClass': 'font-mono text-xs text-gray-400'},
                        {'headerName': 'Pair', 'field': 'symbol', 'maxWidth': 110, 'cellClass': 'font-bold'},
                        {'headerName': 'Type', 'field': 'type', 'maxWidth': 120},
                        {'headerName': 'Side', 'field': 'side', 'maxWidth': 90},
                        {'headerName': 'Amount', 'field': 'origQty', 'maxWidth': 110, 'cellClass': 'font-mono'},
                        {'headerName': 'Filled', 'field': 'executedQty', 'maxWidth': 110, 'cellClass': 'font-mono'},
                        {'headerName': 'Total', 'field': 'cummulativeQuoteQty', 'maxWidth': 120, 'cellClass': 'font-mono'},
                        {'headerName': 'Status', 'field': 'status', 'maxWidth': 120, 'cellClass': 'font-bold'},
                    ], row_rules={'text-emerald-400': "data.status == 'FILLED'", 'text-gray-500': "data.status == 'CANCELED'"})

                with ui.tab_panel('trade_history'):
                    with ui.row().classes('items-center gap-2 mb-2'):
                        ui.label('Símbolo:').classes('text-xs text-gray-400')
                        self.spot_th_symbol = ui.select(COMMON_SYMBOLS, value=self.symbol_spot, new_value_mode='add-unique') \
                            .classes('w-40').props('dense outline').on_value_change(self._refresh_spot_trade_history)
                    self.spot_trade_history_grid = _grid([
                        {'headerName': 'Date', 'field': 'time_str', 'maxWidth': 140, 'cellClass': 'font-mono text-xs text-gray-400'},
                        {'headerName': 'Pair', 'field': 'symbol', 'maxWidth': 110, 'cellClass': 'font-bold'},
                        {'headerName': 'Side', 'field': 'side', 'maxWidth': 90},
                        {'headerName': 'Price', 'field': 'price', 'maxWidth': 110, 'cellClass': 'font-mono'},
                        {'headerName': 'Qty', 'field': 'qty', 'maxWidth': 110, 'cellClass': 'font-mono'},
                        {'headerName': 'Fee', 'field': 'commission', 'maxWidth': 110, 'cellClass': 'font-mono text-gray-400'},
                    ])

                with ui.tab_panel('transactions'):
                    self.spot_transactions_grid = _grid([
                        {'headerName': 'Date', 'field': 'time_str', 'maxWidth': 140, 'cellClass': 'font-mono text-xs text-gray-400'},
                        {'headerName': 'Type', 'field': 'type', 'maxWidth': 110, 'cellClass': 'font-bold'},
                        {'headerName': 'Asset', 'field': 'asset', 'maxWidth': 100},
                        {'headerName': 'Amount', 'field': 'amount', 'maxWidth': 130, 'cellClass': 'font-mono'},
                        {'headerName': 'Status', 'field': 'status', 'maxWidth': 120},
                    ])

                with ui.tab_panel('assets'):
                    self.spot_assets_grid = _grid([
                        {'headerName': 'Asset', 'field': 'asset', 'maxWidth': 110, 'cellClass': 'font-bold text-yellow-400'},
                        {'headerName': 'Total', 'field': 'total_str', 'maxWidth': 150, 'cellClass': 'font-mono text-green-400'},
                        {'headerName': 'Available', 'field': 'free_str', 'maxWidth': 150, 'cellClass': 'font-mono'},
                        {'headerName': 'Locked', 'field': 'locked_str', 'maxWidth': 150, 'cellClass': 'font-mono text-gray-400'},
                        {'headerName': 'Valor USD', 'field': 'usd_value_str', 'maxWidth': 150, 'cellClass': 'font-mono text-yellow-300'},
                    ], height='h-56')

    # ──────────────────────────────────────────────────────────────
    # Navegación
    # ──────────────────────────────────────────────────────────────
    def _switch_network(self, net: str):
        self.selected_network = net
        if net == 'testnet':
            self.btn_testnet.classes('bg-yellow-500 text-black font-bold', remove='text-gray-400')
            self.btn_mainnet.classes('text-gray-400 hover:text-white', remove='bg-yellow-500 text-black font-bold')
        else:
            self.btn_mainnet.classes('bg-yellow-500 text-black font-bold', remove='text-gray-400')
            self.btn_testnet.classes('text-gray-400 hover:text-white', remove='bg-yellow-500 text-black font-bold')
        import asyncio
        asyncio.create_task(self._refresh_all())

    def _switch_wallet(self, wallet: str):
        self.selected_wallet = wallet
        is_fut = (wallet == 'futures')
        self.container_futures.set_visibility(is_fut)
        self.container_spot.set_visibility(not is_fut)
        if is_fut:
            self.btn_wallet_futures.classes('bg-yellow-500 text-black font-black', remove='text-gray-400')
            self.btn_wallet_spot.classes('text-gray-400 hover:text-white', remove='bg-yellow-500 text-black font-black')
        else:
            self.btn_wallet_spot.classes('bg-yellow-500 text-black font-black', remove='text-gray-400')
            self.btn_wallet_futures.classes('text-gray-400 hover:text-white', remove='bg-yellow-500 text-black font-black')
        import asyncio
        asyncio.create_task(self._refresh_all())

    def _client(self) -> BinanceTestnetClient:
        return BinanceTestnetClient(use_testnet=(self.selected_network == 'testnet'))

    # ──────────────────────────────────────────────────────────────
    # Carga de datos
    # ──────────────────────────────────────────────────────────────
    async def _refresh_all(self):
        if self.is_loading:
            return
        self.is_loading = True
        self.btn_refresh.props('loading')
        self.status_badge.set_text('Actualizando...')
        self.status_badge.props('color=gray-800')
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            client = self._client()

            if self.selected_wallet == 'futures':
                info = await loop.run_in_executor(None, lambda: client.get_full_account_info(use_testnet=(self.selected_network == 'testnet')))
                if info.get('success'):
                    self.fut_positions_grid.options['rowData'] = self._map_positions(info.get('positions', []))
                    self.fut_positions_grid.update()
                    self.fut_open_orders_grid.options['rowData'] = self._map_open_orders(info.get('open_orders', []))
                    self.fut_open_orders_grid.update()
                    self.fut_assets_grid.options['rowData'] = info.get('assets', [])
                    self.fut_assets_grid.update()
                    self.status_badge.set_text('✅ Conectado')
                    self.status_badge.props('color=emerald-900')
                else:
                    self.status_badge.set_text(f"⚠️ {info.get('error', 'Error')}")
                    self.status_badge.props('color=red-900')

                await self._refresh_futures_order_history()
                await self._refresh_futures_trade_history()
                await self._refresh_futures_transactions()
            else:
                info = await loop.run_in_executor(None, lambda: client.get_spot_account_info(use_testnet=(self.selected_network == 'testnet')))
                if info.get('success'):
                    self.spot_open_orders_grid.options['rowData'] = info.get('open_orders', [])
                    self.spot_open_orders_grid.update()
                    self.spot_assets_grid.options['rowData'] = info.get('assets', [])
                    self.spot_assets_grid.update()
                    self.status_badge.set_text('✅ Conectado')
                    self.status_badge.props('color=emerald-900')
                else:
                    self.status_badge.set_text(f"⚠️ {info.get('error', 'Error')}")
                    self.status_badge.props('color=red-900')

                await self._refresh_spot_order_history()
                await self._refresh_spot_trade_history()
                await self._refresh_spot_transactions()
        except Exception as e:
            ui.notify(f"Error actualizando operativa: {e}", type='negative')
            self.status_badge.set_text('❌ Error')
            self.status_badge.props('color=red-900')
        finally:
            self.btn_refresh.props(remove='loading')
            self.is_loading = False

    @staticmethod
    def _map_positions(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return positions

    @staticmethod
    def _map_open_orders(orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        for o in orders:
            if 'time_str' not in o:
                ts = o.get('time')
                if ts:
                    try:
                        o['time_str'] = datetime.fromtimestamp(int(ts) / 1000.0).strftime("%d/%m %H:%M:%S")
                    except Exception:
                        o['time_str'] = '-'
                else:
                    o['time_str'] = '-'
        return orders

    async def _refresh_futures_order_history(self, *_):
        import asyncio
        loop = asyncio.get_event_loop()
        sym = self.fut_oh_symbol.value or self.symbol_futures
        client = self._client()
        rows = await loop.run_in_executor(None, lambda: client.get_futures_order_history(symbol=sym))
        self.fut_order_history_grid.options['rowData'] = rows
        self.fut_order_history_grid.update()

    async def _refresh_futures_trade_history(self, *_):
        import asyncio
        loop = asyncio.get_event_loop()
        sym = self.fut_th_symbol.value or self.symbol_futures
        client = self._client()
        rows = await loop.run_in_executor(None, lambda: client.get_futures_trade_history(symbol=sym))
        self.fut_trade_history_grid.options['rowData'] = rows
        self.fut_trade_history_grid.update()

    async def _refresh_futures_transactions(self, *_):
        import asyncio
        loop = asyncio.get_event_loop()
        itype = self.fut_income_type.value
        client = self._client()
        rows = await loop.run_in_executor(None, lambda: client.get_futures_transaction_history(income_type=None if itype == 'Todos' else itype))
        self.fut_transactions_grid.options['rowData'] = rows
        self.fut_transactions_grid.update()

    async def _refresh_spot_order_history(self, *_):
        import asyncio
        loop = asyncio.get_event_loop()
        sym = self.spot_oh_symbol.value or self.symbol_spot
        client = self._client()
        rows = await loop.run_in_executor(None, lambda: client.get_spot_order_history(symbol=sym))
        self.spot_order_history_grid.options['rowData'] = rows
        self.spot_order_history_grid.update()

    async def _refresh_spot_trade_history(self, *_):
        import asyncio
        loop = asyncio.get_event_loop()
        sym = self.spot_th_symbol.value or self.symbol_spot
        client = self._client()
        rows = await loop.run_in_executor(None, lambda: client.get_spot_trade_history(symbol=sym))
        self.spot_trade_history_grid.options['rowData'] = rows
        self.spot_trade_history_grid.update()

    async def _refresh_spot_transactions(self, *_):
        import asyncio
        loop = asyncio.get_event_loop()
        client = self._client()
        rows = await loop.run_in_executor(None, lambda: client.get_spot_transaction_history())
        self.spot_transactions_grid.options['rowData'] = rows
        self.spot_transactions_grid.update()


def render_binance_operations_page():
    page = BinanceOperationsPage()
    page.render()
    return page
