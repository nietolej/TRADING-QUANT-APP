from nicegui import ui, run
import asyncio
import os
import glob
import yaml
import plotly.graph_objects as go
from execution_engine.paper_trader import PaperTrader

class LiveMonitorPage:
    def __init__(self):
        self.strategies_dir = "config/strategies"
        self.trader = None
        self.state = {
            'message': 'Esperando inicio...',
            'balance': 10000.0,
            'position': None,
            'trades': [],
            'stats': {},
            'log_lines': ['Esperando inicio...'],
            'klines': None
        }
        self.custom_params = {}
        self.param_inputs = {}
        self.timer = None

    def _get_available_strategies(self):
        if not os.path.exists(self.strategies_dir):
            return []
        files = glob.glob(f"{self.strategies_dir}/*.yaml")
        return [os.path.basename(f) for f in files]

    def _load_strategy_params(self, strategy_filename):
        path = os.path.join(self.strategies_dir, strategy_filename)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                return config.get('parameters', {})
        except Exception as e:
            return {}

    def _on_strategy_change(self, e):
        self.selected_strategy = e.value
        self.custom_params = self._load_strategy_params(self.selected_strategy)
        self._render_params_form()

    def _on_symbol_change(self, e):
        if not hasattr(self, 'balance_currency_select'):
            return
            
        symbol = e.value.upper()
        if '/' in symbol:
            parts = symbol.split('/')
            if len(parts) == 2:
                base, quote = parts[0].strip(), parts[1].strip()
                if base and quote:
                    options = [base, quote]
                    self.balance_currency_select.options = options
                    if self.balance_currency_select.value not in options:
                        self.balance_currency_select.value = quote
                    self.balance_currency_select.update()

    def _on_state_update(self, new_state):
        self.state.update(new_state)
        # Acumular mensajes en el log (máx. 50 líneas)
        msg = new_state.get('message', '')
        if msg:
            self.state['log_lines'] = (self.state.get('log_lines', []) + [msg])[-50:]

    async def _start_bot(self):
        if not self.selected_strategy:
            ui.notify('Selecciona una estrategia primero.', type='warning')
            return
            
        if self.trader and self.trader.is_running:
            ui.notify('El bot ya está corriendo.', type='warning')
            return

        strategy_path = os.path.join(self.strategies_dir, self.selected_strategy)
        try:
            initial_bal = float(self.balance_input.value)
        except (TypeError, ValueError):
            initial_bal = 10000.0

        # Collect current parameter values from UI
        params_to_pass = {}
        for k, v in self.param_inputs.items():
            try:
                # Intenta castear a float si es posible, de lo contrario deja como string
                val = v.value
                if isinstance(val, str) and val.replace('.', '', 1).isdigit():
                    params_to_pass[k] = float(val) if '.' in val else int(val)
                else:
                    params_to_pass[k] = val
            except Exception:
                params_to_pass[k] = v.value

        self.trader = PaperTrader(
            strategy_path,
            initial_balance=initial_bal,
            update_callback=self._on_state_update,
            custom_parameters=params_to_pass,
            use_testnet=(self.network_select.value == 'Binance Testnet'),
            custom_timeframe=self.timeframe_select.value,
            custom_symbol=getattr(self, 'symbol_input', None) and self.symbol_input.value
        )
        self.status_label.set_text("Estado: INICIANDO ⏳")
        ui.notify('Iniciando Paper Trading (descargando histórico)...', type='info')

        # Ejecutar en hilo I/O para no bloquear la UI durante la descarga
        await run.io_bound(self.trader.start)

        if self.trader.is_running:
            self.status_label.set_text("Estado: CORRIENDO 🟢")
            ui.notify('Bot iniciado en modo Paper Trading', type='positive')
        else:
            self.status_label.set_text("Estado: ERROR ❌")
            ui.notify('Error al iniciar el bot. Revisa la consola.', type='negative')

    def _stop_bot(self):
        if self.trader and self.trader.is_running:
            self.trader.stop()
            self.status_label.set_text("Estado: DETENIDO 🔴")
            self.state['message'] = "Bot detenido."
            ui.notify('Bot detenido.', type='info')

    def _ui_update_loop(self):
        try:
            curr = self.balance_currency_select.value if hasattr(self, 'balance_currency_select') else 'USDT'
            # Balance
            self.balance_label.set_text(f"Saldo Virtual: {self.state['balance']:.2f} {curr}")
            
            # Posición abierta
            if self.state['position']:
                pos = self.state['position']
                self.pos_label.set_text(f"Posición Abierta: {pos.side.upper()} @ {pos.entry_price:.4f} | SL: {pos.sl_price} | TP: {pos.tp_price}")
            else:
                self.pos_label.set_text("Posición Abierta: NINGUNA")
                
            # Log acumulado
            log_lines = self.state.get('log_lines', ['Esperando inicio...'])
            self.log_label.set_text('\n'.join(log_lines))
            
            # Trades y estadísticas
            trades_list = self.state.get('trades', [])
            self.trades_label.set_text(f"Trades Completados: {len(trades_list)}")
            
            stats = self.state.get('stats', {})
            win_rate = stats.get('win_rate', 0.0)
            total_pnl = stats.get('total_pnl', 0.0)
            initial_balance = self.trader.initial_balance if hasattr(self, 'trader') and self.trader else 10000.0
            total_pnl_pct = (total_pnl / initial_balance) * 100 if initial_balance > 0 else 0.0
            self.stats_label.set_text(f"Win Rate: {win_rate:.2f}% | Total PNL: {total_pnl:+.2f} {curr} ({total_pnl_pct:+.2f}%)")
            
            # Actualizar Bid/Ask Ticker si existe
            if hasattr(self, 'bid_label'):
                bid = self.state.get('current_bid', 0.0)
                ask = self.state.get('current_ask', 0.0)
                bid_qty = self.state.get('current_bid_qty', 0.0)
                ask_qty = self.state.get('current_ask_qty', 0.0)
                spread = ask - bid
                self.bid_label.set_text(f"{bid:,.2f}")
                self.ask_label.set_text(f"{ask:,.2f}")
                self.spread_label.set_text(f"{spread:.2f}")
                
                if hasattr(self, 'bid_qty_label'):
                    self.bid_qty_label.set_text(f"Vol: {bid_qty:.3f}")
                    self.ask_qty_label.set_text(f"Vol: {ask_qty:.3f}")

            # Actualizar gráfico
            if hasattr(self, 'chart') and self.state.get('klines') is not None and not self.state['klines'].empty:
                df = self.state['klines']
                fig = self._build_chart(df, trades_list, self.state.get('position'))
                self.chart.update_figure(fig)
            
            # Actualizar tabla aggrid
            if hasattr(self, 'trades_grid'):
                rows = []
                
                # Añadir posición abierta como primera fila si existe
                if self.state.get('position'):
                    pos = self.state['position']
                    current_price = df['close'].iloc[-1] if 'df' in locals() and not df.empty else pos.entry_price
                    if pos.side == 'long':
                        pnl = (current_price - pos.entry_price) * pos.quantity
                        pnl_pct = ((current_price - pos.entry_price) / pos.entry_price) * 100 if pos.entry_price else 0
                    else:
                        pnl = (pos.entry_price - current_price) * pos.quantity
                        pnl_pct = ((pos.entry_price - current_price) / pos.entry_price) * 100 if pos.entry_price else 0
                    
                    rows.append({
                        'side': f"📈 {pos.side.upper()}" if pos.side == 'long' else f"📉 {pos.side.upper()}",
                        'entry_price': f"{pos.entry_price:.4f}",
                        'exit_price': "-",
                        'sl_price': f"{pos.sl_price:.4f}" if pos.sl_price else '-',
                        'tp_price': f"{pos.tp_price:.4f}" if pos.tp_price else '-',
                        'pnl': f"{pnl:+.2f} ({pnl_pct:+.2f}%)",
                        'reason': 'ABIERTA'
                    })

                for t in reversed(trades_list):
                    pnl_val = t.get('pnl', 0.0)
                    pnl_pct = t.get('pnl_pct', 0.0)
                    # Formatear como "15.00 (1.50%)"
                    pnl_str = f"{pnl_val:+.2f} ({pnl_pct:+.2f}%)"
                    
                    rows.append({
                        'side': t.get('side', '').upper(),
                        'entry_price': f"{t.get('entry_price', 0):.4f}",
                        'exit_price': f"{t.get('exit_price', 0):.4f}",
                        'sl_price': f"{t.get('sl_price', 0):.4f}" if t.get('sl_price') else '-',
                        'tp_price': f"{t.get('tp_price', 0):.4f}" if t.get('tp_price') else '-',
                        'pnl': pnl_str,
                        'reason': t.get('reason', '')
                    })
                self.trades_grid.options['rowData'] = rows
                self.trades_grid.update()
                
        except Exception as e:
            import traceback
            err_str = traceback.format_exc()
            try:
                with open("ui_error.log", "a", encoding="utf-8") as f:
                    f.write(err_str + "\n")
            except:
                pass
                
            if self.timer:
                self.timer.deactivate()
            
            # Notificar en la interfaz de usuario para depuración
            self.state['log_lines'] = (self.state.get('log_lines', []) + [f"❌ Error UI: {type(e).__name__} - {e}"])[-50:]
            if hasattr(self, 'log_label'):
                try:
                    self.log_label.set_text('\n'.join(self.state['log_lines']))
                except:
                    pass

    def _render_params_form(self):
        if not hasattr(self, 'params_container'):
            return
        self.params_container.clear()
        self.param_inputs.clear()
        
        if not self.custom_params:
            with self.params_container:
                ui.label('Esta estrategia no tiene parámetros configurables.').classes('text-gray-400 italic')
            return
            
        with self.params_container:
            ui.label('Parámetros:').classes('text-sm font-bold text-gray-300')
            # Create a grid for params to save space
            with ui.grid(columns=2).classes('w-full gap-2'):
                for k, v in self.custom_params.items():
                    # Decide input type based on value
                    if isinstance(v, (int, float)):
                        inp = ui.number(label=k, value=v).classes('w-full')
                    else:
                        inp = ui.input(label=k, value=str(v)).classes('w-full')
                    self.param_inputs[k] = inp

    def _build_empty_chart(self):
        fig = go.Figure()
        fig.update_layout(
            template='plotly_dark',
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=0, r=0, t=30, b=0),
            title='Esperando datos...'
        )
        return fig

    def _build_chart(self, df, trades, position=None):
        fig = go.Figure()
        
        # Convert index to string to avoid Timestamp JSON serialization error
        x_vals = df.index.astype(str).tolist()

        # Velas
        fig.add_trace(go.Candlestick(
            x=x_vals,
            open=df['open'],
            high=df['high'],
            low=df['low'],
            close=df['close'],
            name='Precio'
        ))
        
        # Añadir indicadores dinámicos basados en la estrategia configurada
        import ta
        try:
            if hasattr(self, 'trader') and self.trader and self.trader.strategy:
                config = self.trader.strategy.config
                params = self.trader.strategy.parameters or {}
                
                # Helper para resolver el periodo de un indicador
                def resolve_period(p):
                    if isinstance(p, str) and p in params:
                        return int(params[p])
                    try:
                        return int(float(p))
                    except:
                        return 20
                        
                # Recopilar todas las reglas de entrada y salida
                rules = []
                for cond_type in ["entry_conditions", "exit_conditions"]:
                    conds = config.get(cond_type, {})
                    rules.extend(conds.get("rules", []))
                    
                try:
                    with open("chart_debug.txt", "a") as f:
                        f.write(f"Chart updating. Rules found: {len(rules)}, Params: {params}\n")
                except:
                    pass

                added_indicators = set()
                
                # Iterar sobre las reglas y extraer indicadores a graficar (SMA, EMA)
                for rule in rules:
                    rule_type = rule.get("type")
                    if rule_type == "ma_cross":
                        fast = resolve_period(rule.get("fast_period", 20))
                        slow = resolve_period(rule.get("slow_period", 50))
                        ma_type = rule.get("ma_type", "SMA")
                        
                        for p, m_type in [(fast, ma_type), (slow, ma_type)]:
                            ind_name = f"{m_type}_{p}"
                            if ind_name not in added_indicators:
                                series = ta.trend.sma_indicator(df['close'], window=p) if m_type == "SMA" else ta.trend.ema_indicator(df['close'], window=p)
                                import pandas as pd
                                series_list = [None if pd.isna(v) else v for v in series]
                                fig.add_trace(go.Scatter(x=x_vals, y=series_list, mode='lines', name=f'{m_type} {p}', line=dict(width=1.5)))
                                added_indicators.add(ind_name)
                                
                    elif rule_type == "technical_indicator":
                        ind1 = rule.get("indicator1", "EMA")
                        p1 = resolve_period(rule.get("period1", 20))
                        ind2 = rule.get("indicator2", "Price")
                        p2 = resolve_period(rule.get("period2", 50))
                        
                        for ind, p in [(ind1, p1), (ind2, p2)]:
                            if ind in ["SMA", "EMA"]:
                                ind_name = f"{ind}_{p}"
                                if ind_name not in added_indicators:
                                    series = ta.trend.sma_indicator(df['close'], window=p) if ind == "SMA" else ta.trend.ema_indicator(df['close'], window=p)
                                    import pandas as pd
                                    series_list = [None if pd.isna(v) else v for v in series]
                                    fig.add_trace(go.Scatter(x=x_vals, y=series_list, mode='lines', name=f'{ind} {p}', line=dict(width=1.5)))
                                    added_indicators.add(ind_name)
        except Exception as exc:
            import logging, traceback
            logging.error("Error al construir indicadores: %s", exc, exc_info=True)
            try:
                with open("chart_debug.txt", "a") as f:
                    f.write(f"Exception: {exc}\n{traceback.format_exc()}\n")
            except:
                pass

        # Marcadores de Trades
        for t in trades:
            entry_time_str = str(t['entry_time'])
            exit_time_str = str(t['exit_time']) if t['exit_time'] else None
            
            # Entrada
            if t['side'] == 'long':
                fig.add_annotation(x=entry_time_str, y=t['entry_price'], text="▲ BUY", showarrow=True, arrowhead=1, arrowcolor="green", font=dict(color="green"))
                if exit_time_str:
                    fig.add_annotation(x=exit_time_str, y=t['exit_price'], text="▼ SELL", showarrow=True, arrowhead=1, arrowcolor="red", font=dict(color="red"))
            else:
                fig.add_annotation(x=entry_time_str, y=t['entry_price'], text="▼ SELL", showarrow=True, arrowhead=1, arrowcolor="red", font=dict(color="red"))
                if exit_time_str:
                    fig.add_annotation(x=exit_time_str, y=t['exit_price'], text="▲ BUY", showarrow=True, arrowhead=1, arrowcolor="green", font=dict(color="green"))

        # Posición Abierta
        if position:
            entry_time_str = str(position.entry_timestamp)
            if position.side == 'long':
                fig.add_annotation(x=entry_time_str, y=position.entry_price, text="▲ BUY (ABIERTA)", showarrow=True, arrowhead=1, arrowcolor="#00ff00", font=dict(color="#00ff00", size=14, weight="bold"))
            else:
                fig.add_annotation(x=entry_time_str, y=position.entry_price, text="▼ SELL (ABIERTA)", showarrow=True, arrowhead=1, arrowcolor="#ff0000", font=dict(color="#ff0000", size=14, weight="bold"))
            
            # Dibujar líneas horizontales para Stop Loss y Take Profit
            if position.sl_price:
                fig.add_hline(y=position.sl_price, line_dash="dash", line_color="rgba(255, 0, 0, 0.6)", annotation_text="SL", annotation_position="right", annotation_font_color="rgba(255, 0, 0, 0.9)")
            if position.tp_price:
                fig.add_hline(y=position.tp_price, line_dash="dash", line_color="rgba(0, 255, 0, 0.6)", annotation_text="TP", annotation_position="right", annotation_font_color="rgba(0, 255, 0, 0.9)")
                
        # Línea de Precio Actual (estilo TradingView)
        if not df.empty:
            current_price = df['close'].iloc[-1]
            fig.add_hline(
                y=current_price, 
                line_dash="dot", 
                line_color="#f23645", 
                line_width=1,
                annotation_text=f"{current_price:.2f}",
                annotation_position="right",
                annotation_font_color="white",
                annotation_bgcolor="#f23645"
            )
                
        fig.update_layout(
            template='plotly_dark',
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=40, r=40, t=30, b=30),
            xaxis_rangeslider_visible=False,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            uirevision='constant'
        )
        return fig

    def render(self):
        ui.label('Live Monitor (Paper Trading)').classes('text-3xl font-bold text-white mb-6')

        strategies = self._get_available_strategies()
        self.selected_strategy = strategies[0] if strategies else None
        if self.selected_strategy:
            self.custom_params = self._load_strategy_params(self.selected_strategy)

        with ui.row().classes('w-full gap-4 mb-6'):
            with ui.card().classes('bg-gray-800 text-white p-4 flex-1'):
                ui.label('Configuración de Ejecución').classes('text-xl font-bold mb-4')
                self.strat_select = ui.select(strategies, label='Estrategia a Operar', value=self.selected_strategy, on_change=self._on_strategy_change).classes('w-full mb-4')
                
                self.params_container = ui.column().classes('w-full mb-4 gap-2')
                self._render_params_form()

                with ui.row().classes('w-full items-center gap-2 mb-4'):
                    self.balance_input = ui.number(label='Saldo Inicial', value=10000.0).classes('flex-1')
                    self.balance_currency_select = ui.select(
                        ['BTC', 'USDT'], 
                        value='USDT', 
                        label='Moneda'
                    ).classes('w-32')
                
                self.timeframe_select = ui.select(
                    ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w', '1M'],
                    value='1h',
                    label='Temporalidad (Timeframe)'
                ).classes('w-full mb-4')

                self.symbol_input = ui.input(
                    label='Par/Moneda (ej. BTC/USDT)', 
                    value='BTC/USDT',
                    on_change=self._on_symbol_change
                ).classes('w-full mb-4')
                
                self.network_select = ui.select(
                    ['Binance Real (Mainnet)', 'Binance Testnet'], 
                    label='Red de Datos', 
                    value='Binance Real (Mainnet)'
                ).classes('w-full mb-4')
                
                with ui.row().classes('w-full justify-between mt-4'):
                    ui.button('Iniciar Bot', on_click=self._start_bot).classes('bg-green-600 hover:bg-green-700 text-white font-bold py-2 px-4 rounded')
                    ui.button('Detener Bot', on_click=self._stop_bot).classes('bg-red-600 hover:bg-red-700 text-white font-bold py-2 px-4 rounded')

            with ui.card().classes('bg-gray-800 text-white p-4 flex-1'):
                ui.label('Estado en Vivo').classes('text-xl font-bold mb-4')
                self.status_label = ui.label('Estado: DETENIDO 🔴').classes('text-lg font-semibold mb-2')
                self.balance_label = ui.label('Saldo Virtual: 10000.00 USDT').classes('text-lg font-semibold mb-2 text-green-400')
                self.stats_label = ui.label('Win Rate: 0.00% | Total PNL: 0.00 USDT').classes('text-lg font-semibold mb-2 text-yellow-400')
                self.pos_label = ui.label('Posición Abierta: NINGUNA').classes('text-lg mb-2 text-blue-400')
                self.trades_label = ui.label('Trades Completados: 0').classes('text-lg mb-2')
                
        with ui.card().classes('bg-gray-800 p-4 w-full mb-6'):
            with ui.row().classes('w-full justify-between items-center mb-4'):
                ui.label('Gráfico en Vivo').classes('text-xl font-bold text-white')
                # Order Book Ticker (Bid / Spread / Ask)
                with ui.row().classes('gap-4 items-center'):
                    with ui.column().classes('items-center border border-red-500 rounded px-4 py-1'):
                        self.bid_label = ui.label('0.00').classes('text-red-500 font-bold text-xl')
                        with ui.row().classes('gap-2 items-center'):
                            ui.label('SELL').classes('text-red-500 text-sm font-bold')
                            self.bid_qty_label = ui.label('0.000').classes('text-red-300 text-xs')
                    self.spread_label = ui.label('0.0').classes('text-gray-400 font-bold text-lg')
                    with ui.column().classes('items-center border border-blue-500 rounded px-4 py-1'):
                        self.ask_label = ui.label('0.00').classes('text-blue-500 font-bold text-xl')
                        with ui.row().classes('gap-2 items-center'):
                            ui.label('BUY').classes('text-blue-500 text-sm font-bold')
                            self.ask_qty_label = ui.label('0.000').classes('text-blue-300 text-xs')

            self.chart = ui.plotly(self._build_empty_chart()).classes('w-full h-96')
                
        with ui.card().classes('bg-gray-800 p-4 w-full mb-6'):
            ui.label('Historial de Trades (Sesión Actual)').classes('text-xl font-bold text-white mb-4')
            self.trades_grid = ui.aggrid({
                'defaultColDef': {'flex': 1, 'sortable': True, 'resizable': True},
                'columnDefs': [
                    {'headerName': 'Lado',          'field': 'side',         'maxWidth': 80},
                    {'headerName': 'Entrada',        'field': 'entry_price',  'maxWidth': 120},
                    {'headerName': 'Salida',         'field': 'exit_price',   'maxWidth': 120},
                    {'headerName': 'SL',             'field': 'sl_price',     'maxWidth': 120},
                    {'headerName': 'TP',             'field': 'tp_price',     'maxWidth': 120},
                    {'headerName': 'PNL',            'field': 'pnl',          'maxWidth': 110},
                    {'headerName': 'Gatillo/Razón',  'field': 'reason'},
                ],
                'rowData': [],
                'rowClassRules': {
                    'text-green-400': 'parseFloat(data.pnl) > 0',
                    'text-red-400':   'parseFloat(data.pnl) <= 0',
                }
            }).classes('h-72 text-white')

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
    return page
