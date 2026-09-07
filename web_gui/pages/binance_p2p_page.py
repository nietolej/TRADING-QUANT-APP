"""
Página de Historial de Operaciones P2P de Binance.
EXCLUSIVA de la cuenta Real: Binance Testnet no ofrece mercado P2P/C2C en absoluto.
"""
import asyncio
from typing import Dict, Any, List
from nicegui import ui

from execution_engine.binance_client import BinanceTestnetClient, get_binance_credentials


class BinanceP2PPage:
    def __init__(self):
        self.trade_type = "BUY"
        self.is_loading = False

    def render(self):
        with ui.column().classes('w-full h-full p-2 md:p-4 gap-6 bg-[#0a0e17] text-white'):

            with ui.row().classes('w-full justify-between items-center pb-4 border-b border-gray-800 flex-wrap gap-4'):
                with ui.column().classes('gap-1'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('handshake', size='32px', color='yellow-400')
                        ui.label('Operaciones P2P (Binance Real)').classes('text-2xl md:text-3xl font-extrabold text-white tracking-tight font-heading')
                    ui.label('Historial de compras y ventas P2P de tu cuenta Real de Binance').classes('text-xs md:text-sm text-gray-400')

                self.btn_refresh = ui.button('Actualizar', icon='refresh', on_click=self._refresh) \
                    .classes('bg-gray-800 hover:bg-gray-700 text-white font-bold text-xs px-3 py-2 rounded-xl shadow border border-gray-700')

            creds = get_binance_credentials()
            if not creds.get('has_real'):
                with ui.card().classes('w-full bg-amber-950/40 border border-amber-600/50 p-5 rounded-2xl'):
                    ui.icon('warning', size='28px', color='amber-400')
                    ui.label('No has configurado claves de Binance Real.').classes('text-sm font-bold text-amber-300 mt-2')
                    ui.label('P2P es una funcionalidad exclusiva de la cuenta Real (no existe en Testnet). Configura tus claves reales en "Conectar APIs Exchange" para ver tu historial.').classes('text-xs text-gray-400 mt-1')
                return

            with ui.row().classes('w-full justify-between items-center bg-gray-900/90 p-3.5 rounded-2xl border border-gray-800 flex-wrap gap-4 shadow-lg'):
                ui.label('TIPO DE OPERACIÓN:').classes('text-xs font-black text-gray-300 tracking-wider')
                with ui.row().classes('bg-gray-950 p-1.5 rounded-xl border border-gray-800 gap-1.5 shadow-inner'):
                    self.btn_buy = ui.button('🟢 BUY (Compras)', on_click=lambda: self._switch_type('BUY')) \
                        .props('dense').classes('bg-yellow-500 text-black text-xs font-black px-4 py-2 rounded-lg transition-all shadow')
                    self.btn_sell = ui.button('🔴 SELL (Ventas)', on_click=lambda: self._switch_type('SELL')) \
                        .props('dense flat').classes('text-gray-400 hover:text-white text-xs font-bold px-4 py-2 rounded-lg transition-all')
                self.status_badge = ui.badge('Cargando...', color='gray-800').classes('text-xs px-3 py-2 rounded-xl')

            with ui.card().classes('bg-gray-900 p-5 rounded-2xl border border-gray-800 w-full shadow-xl'):
                self.p2p_grid = ui.aggrid({
                    'defaultColDef': {'flex': 1, 'sortable': True, 'resizable': True},
                    'columnDefs': [
                        {'headerName': 'Fecha', 'field': 'createTime', 'maxWidth': 160, 'cellClass': 'font-mono text-xs text-gray-400'},
                        {'headerName': 'N° Orden', 'field': 'orderNumber', 'maxWidth': 180, 'cellClass': 'font-mono text-xs text-gray-500'},
                        {'headerName': 'Tipo', 'field': 'trade_type', 'maxWidth': 90, 'cellClass': 'font-bold'},
                        {'headerName': 'Activo', 'field': 'asset', 'maxWidth': 90, 'cellClass': 'font-bold text-yellow-400'},
                        {'headerName': 'Cantidad', 'field': 'amount', 'maxWidth': 130, 'cellClass': 'font-mono'},
                        {'headerName': 'Precio Unitario', 'field': 'unitPrice', 'maxWidth': 140, 'cellClass': 'font-mono'},
                        {'headerName': 'Total Fiat', 'field': 'totalPrice', 'maxWidth': 130, 'cellClass': 'font-mono text-green-400 font-bold'},
                        {'headerName': 'Moneda Fiat', 'field': 'fiat', 'maxWidth': 110},
                        {'headerName': 'Contraparte', 'field': 'counterPartNickName', 'maxWidth': 160, 'cellClass': 'text-gray-300'},
                        {'headerName': 'Estado', 'field': 'type', 'maxWidth': 130, 'cellClass': 'font-bold'},
                    ],
                    'rowData': [],
                    'rowClassRules': {
                        'text-emerald-400': "data.type == 'COMPLETED'",
                        'text-red-400': "data.type == 'CANCELLED'",
                    }
                }).classes('h-[60vh] text-white w-full')

        ui.timer(0.5, self._refresh, once=True)

    def _switch_type(self, t: str):
        self.trade_type = t
        if t == 'BUY':
            self.btn_buy.classes('bg-yellow-500 text-black font-black', remove='text-gray-400')
            self.btn_sell.classes('text-gray-400 hover:text-white', remove='bg-yellow-500 text-black font-black')
        else:
            self.btn_sell.classes('bg-yellow-500 text-black font-black', remove='text-gray-400')
            self.btn_buy.classes('text-gray-400 hover:text-white', remove='bg-yellow-500 text-black font-black')
        asyncio.create_task(self._refresh())

    async def _refresh(self):
        if self.is_loading:
            return
        self.is_loading = True
        self.btn_refresh.props('loading')
        self.status_badge.set_text('Actualizando...')
        self.status_badge.props('color=gray-800')
        try:
            loop = asyncio.get_event_loop()
            client = BinanceTestnetClient(use_testnet=False)
            rows, err = await loop.run_in_executor(None, lambda: client.get_p2p_trade_history(trade_type=self.trade_type))
            self.p2p_grid.options['rowData'] = rows
            self.p2p_grid.update()
            if err:
                self.status_badge.set_text(f"⚠️ {err}")
                self.status_badge.props('color=red-900')
                ui.notify(f"Error consultando P2P: {err}", type='warning')
            else:
                self.status_badge.set_text(f"✅ {len(rows)} operaciones")
                self.status_badge.props('color=emerald-900')
        except Exception as e:
            self.status_badge.set_text('❌ Error')
            self.status_badge.props('color=red-900')
            ui.notify(f"Error actualizando P2P: {e}", type='negative')
        finally:
            self.btn_refresh.props(remove='loading')
            self.is_loading = False


def render_binance_p2p_page():
    page = BinanceP2PPage()
    page.render()
    return page
