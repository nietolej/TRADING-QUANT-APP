from nicegui import ui
import os
import glob
from execution_engine.paper_trader import PaperTrader

class LiveMonitorPage:
    def __init__(self):
        self.strategies_dir = "config/strategies"
        self.trader = None
        self.state = {
            'message': 'Esperando inicio...',
            'balance': 10000.0,
            'position': None,
            'trades': []
        }
        self.timer = None

    def _get_available_strategies(self):
        if not os.path.exists(self.strategies_dir):
            return []
        files = glob.glob(f"{self.strategies_dir}/*.yaml")
        return [os.path.basename(f) for f in files]

    def _on_state_update(self, new_state):
        self.state.update(new_state)

    def _start_bot(self):
        if not self.selected_strategy:
            ui.notify('Selecciona una estrategia primero.', type='warning')
            return
            
        if self.trader and self.trader.is_running:
            ui.notify('El bot ya está corriendo.', type='warning')
            return

        strategy_path = os.path.join(self.strategies_dir, self.selected_strategy)
        self.trader = PaperTrader(strategy_path, initial_balance=float(self.balance_input.value), update_callback=self._on_state_update)
        self.trader.start()
        
        self.status_label.set_text("Estado: CORRIENDO 🟢")
        ui.notify('Bot iniciado en modo Paper Trading', type='positive')

    def _stop_bot(self):
        if self.trader and self.trader.is_running:
            self.trader.stop()
            self.status_label.set_text("Estado: DETENIDO 🔴")
            self.state['message'] = "Bot detenido."
            ui.notify('Bot detenido.', type='info')

    def _ui_update_loop(self):
        try:
            # Actualizar labels e info con el state
            self.log_label.set_text(self.state['message'])
            self.balance_label.set_text(f"Saldo Virtual: ${self.state['balance']:.2f}")
            
            if self.state['position']:
                pos = self.state['position']
                self.pos_label.set_text(f"Posición Abierta: {pos.side.upper()} @ {pos.entry_price}")
            else:
                self.pos_label.set_text("Posición Abierta: NINGUNA")
                
            # Actualizar trades
            self.trades_label.set_text(f"Trades Completados: {len(self.state['trades'])}")
        except Exception:
            if self.timer:
                self.timer.deactivate()
            pass

    def render(self):
        ui.label('Live Monitor (Paper Trading)').classes('text-3xl font-bold text-white mb-6')

        strategies = self._get_available_strategies()
        self.selected_strategy = strategies[0] if strategies else None

        with ui.row().classes('w-full gap-4 mb-6'):
            with ui.card().classes('bg-gray-800 text-white p-4 flex-1'):
                ui.label('Configuración de Ejecución').classes('text-xl font-bold mb-4')
                self.strat_select = ui.select(strategies, label='Estrategia a Operar', value=self.selected_strategy, on_change=lambda e: setattr(self, 'selected_strategy', e.value)).classes('w-full mb-4')
                self.balance_input = ui.number(label='Saldo Inicial (USDT)', value=10000.0).classes('w-full mb-4')
                
                with ui.row().classes('w-full justify-between mt-4'):
                    ui.button('Iniciar Bot', on_click=self._start_bot).classes('bg-green-600 hover:bg-green-700 text-white font-bold py-2 px-4 rounded')
                    ui.button('Detener Bot', on_click=self._stop_bot).classes('bg-red-600 hover:bg-red-700 text-white font-bold py-2 px-4 rounded')

            with ui.card().classes('bg-gray-800 text-white p-4 flex-1'):
                ui.label('Estado en Vivo').classes('text-xl font-bold mb-4')
                self.status_label = ui.label('Estado: DETENIDO 🔴').classes('text-lg font-semibold mb-2')
                self.balance_label = ui.label('Saldo Virtual: $10000.00').classes('text-lg font-semibold mb-2 text-green-400')
                self.pos_label = ui.label('Posición Abierta: NINGUNA').classes('text-lg mb-2 text-blue-400')
from nicegui import ui
import os
import glob
from execution_engine.paper_trader import PaperTrader

class LiveMonitorPage:
    def __init__(self):
        self.strategies_dir = "config/strategies"
        self.trader = None
        self.state = {
            'message': 'Esperando inicio...',
            'balance': 10000.0,
            'position': None,
            'trades': []
        }
        self.timer = None

    def _get_available_strategies(self):
        if not os.path.exists(self.strategies_dir):
            return []
        files = glob.glob(f"{self.strategies_dir}/*.yaml")
        return [os.path.basename(f) for f in files]

    def _on_state_update(self, new_state):
        self.state.update(new_state)

    def _start_bot(self):
        if not self.selected_strategy:
            ui.notify('Selecciona una estrategia primero.', type='warning')
            return
            
        if self.trader and self.trader.is_running:
            ui.notify('El bot ya está corriendo.', type='warning')
            return

        strategy_path = os.path.join(self.strategies_dir, self.selected_strategy)
        self.trader = PaperTrader(strategy_path, initial_balance=float(self.balance_input.value), update_callback=self._on_state_update)
        self.trader.start()
        
        self.status_label.set_text("Estado: CORRIENDO 🟢")
        ui.notify('Bot iniciado en modo Paper Trading', type='positive')

    def _stop_bot(self):
        if self.trader and self.trader.is_running:
            self.trader.stop()
            self.status_label.set_text("Estado: DETENIDO 🔴")
            self.state['message'] = "Bot detenido."
            ui.notify('Bot detenido.', type='info')

    def _ui_update_loop(self):
        try:
            # Actualizar labels e info con el state
            self.log_label.set_text(self.state['message'])
            self.balance_label.set_text(f"Saldo Virtual: ${self.state['balance']:.2f}")
            
            if self.state['position']:
                pos = self.state['position']
                self.pos_label.set_text(f"Posición Abierta: {pos.side.upper()} @ {pos.entry_price}")
            else:
                self.pos_label.set_text("Posición Abierta: NINGUNA")
                
            # Actualizar trades
            self.trades_label.set_text(f"Trades Completados: {len(self.state['trades'])}")
        except Exception:
            if self.timer:
                self.timer.deactivate()
            pass

    def render(self):
        ui.label('Live Monitor (Paper Trading)').classes('text-3xl font-bold text-white mb-6')

        strategies = self._get_available_strategies()
        self.selected_strategy = strategies[0] if strategies else None

        with ui.row().classes('w-full gap-4 mb-6'):
            with ui.card().classes('bg-gray-800 text-white p-4 flex-1'):
                ui.label('Configuración de Ejecución').classes('text-xl font-bold mb-4')
                self.strat_select = ui.select(strategies, label='Estrategia a Operar', value=self.selected_strategy, on_change=lambda e: setattr(self, 'selected_strategy', e.value)).classes('w-full mb-4')
                self.balance_input = ui.number(label='Saldo Inicial (USDT)', value=10000.0).classes('w-full mb-4')
                
                with ui.row().classes('w-full justify-between mt-4'):
                    ui.button('Iniciar Bot', on_click=self._start_bot).classes('bg-green-600 hover:bg-green-700 text-white font-bold py-2 px-4 rounded')
                    ui.button('Detener Bot', on_click=self._stop_bot).classes('bg-red-600 hover:bg-red-700 text-white font-bold py-2 px-4 rounded')

            with ui.card().classes('bg-gray-800 text-white p-4 flex-1'):
                ui.label('Estado en Vivo').classes('text-xl font-bold mb-4')
                self.status_label = ui.label('Estado: DETENIDO 🔴').classes('text-lg font-semibold mb-2')
                self.balance_label = ui.label('Saldo Virtual: $10000.00').classes('text-lg font-semibold mb-2 text-green-400')
                self.pos_label = ui.label('Posición Abierta: NINGUNA').classes('text-lg mb-2 text-blue-400')
                self.trades_label = ui.label('Trades Completados: 0').classes('text-lg mb-2')
                
        with ui.card().classes('bg-gray-900 text-green-500 p-4 w-full h-48 overflow-y-auto font-mono'):
            ui.label('Consola de Ejecución:').classes('text-sm text-gray-400 mb-2')
            self.log_label = ui.label('Esperando inicio...').classes('whitespace-pre-wrap')

        # Timer para actualizar la UI sin bloquear el websocket thread.
        # Se guarda referencia para cancelarlo cuando el cliente se desconecta
        # (recarga de página, cierre de pestaña) y evitar el RuntimeError de slot eliminado.
        self.timer = ui.timer(1.0, self._ui_update_loop)
        ui.context.client.on_disconnect(lambda: self.timer.deactivate() if self.timer else None)

def render_live_monitor_page():
    page = LiveMonitorPage()
    page.render()
