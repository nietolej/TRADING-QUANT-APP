"""
Página de Opciones Cuantitativas y Ejecución Algorítmica (TWAP & POV).
Ofrece dos módulos de nivel institucional:
1. Opciones Vanilla Binance:
   - Cadena de Opciones (Options Chain Matrix)
   - Sonrisa de Volatilidad (Volatility Smile / IV Skew)
   - Curvas de Griegas (Delta, Gamma, Vega, Theta)
2. Motor de Algoritmos de Ejecución:
   - Ejecución TWAP (repartición uniforme en tiempo con jitter)
   - Consola visual con barra de progreso, VWAP acumulado y slippage en bps
"""
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional
import plotly.graph_objects as go
from nicegui import ui

from data_layer.data_sources.binance_options_provider import BinanceOptionsProvider
from execution_engine.algo_execution_engine import algo_engine, AlgoExecutionTask

logger = logging.getLogger("OptionsAlgoPage")


class OptionsAlgoPage:
    """Controlador de la página de Opciones Cuantitativas y Algoritmos de Ejecución."""

    def __init__(self):
        self.options_provider = BinanceOptionsProvider()
        self.current_asset = "BTC"
        self.current_expiry: Optional[str] = None
        self.options_data: Dict[str, Any] = {}
        self.is_loading_options = False

        # Estado del algoritmo activo
        self.active_algo_task: Optional[AlgoExecutionTask] = None

    def render(self):
        """Construye la interfaz visual en NiceGUI."""
        with ui.column().classes('w-full h-full p-4 lg:p-6 space-y-4 bg-[#080c14] text-slate-200'):
            # ──────────────────────────────────────────────────────────
            # 1. Header con Selector de Módulo (Pestañas)
            # ──────────────────────────────────────────────────────────
            with ui.row().classes('w-full justify-between items-center bg-[#0d121f] p-4 rounded-xl border border-[#1e293b]'):
                with ui.row().classes('items-center gap-3'):
                    ui.icon('hub', size='md', color='emerald-400')
                    with ui.column().classes('gap-0'):
                        ui.label('OPCIONES VANILLA & ALGORITMOS DE EJECUCIÓN').classes('text-base font-bold tracking-wider text-emerald-400 font-mono')
                        ui.label('Binance EAPI Opciones • Sonrisa de Volatilidad • Griegas • Ejecutor TWAP / POV').classes('text-xs text-slate-400')

                with ui.row().classes('items-center gap-3'):
                    # Selector de Subyacente
                    with ui.row().classes('bg-[#151c2e] p-1 rounded-lg border border-[#1e293b]'):
                        for a in ["BTC", "ETH"]:
                            btn_style = 'bg-emerald-500 text-black font-bold' if a == self.current_asset else 'text-slate-400 hover:text-white'
                            ui.button(
                                a, 
                                on_click=lambda asset=a: self._change_asset(asset)
                            ).props('dense flat no-caps').classes(f'text-xs px-3 py-1 rounded {btn_style}')

                    # Botón Actualizar
                    self.btn_refresh = ui.button(
                        'Actualizar Opciones', 
                        icon='refresh', 
                        on_click=self._refresh_options_async
                    ).props('dense outline color=emerald-400').classes('text-xs text-emerald-400 px-3 py-1.5 rounded-lg')

            # ──────────────────────────────────────────────────────────
            # 2. Pestañas Principales: Opciones vs Algoritmos
            # ──────────────────────────────────────────────────────────
            with ui.tabs().classes('w-full text-slate-400 border-b border-[#1e293b]') as self.main_tabs:
                self.tab_options = ui.tab('OPCIONES & VOLATILIDAD', icon='candlestick_chart').classes('text-xs font-mono font-bold')
                self.tab_algos = ui.tab('ALGORITMOS TWAP / POV', icon='precision_manufacturing').classes('text-xs font-mono font-bold')

            with ui.tab_panels(self.main_tabs, value=self.tab_options).classes('w-full bg-transparent p-0'):
                # ══════════════════════════════════════════════════════
                # PANEL A: OPCIONES Y GRIEGAS
                # ══════════════════════════════════════════════════════
                with ui.tab_panel(self.tab_options).classes('w-full p-0 space-y-4'):
                    # Selector de Vencimientos (DTE) y KPIs
                    with ui.row().classes('w-full justify-between items-center bg-[#0d121f] p-3 rounded-xl border border-[#1e293b] gap-2 flex-wrap'):
                        with ui.row().classes('items-center gap-2'):
                            ui.label('Vencimiento (Expiración):').classes('text-xs font-mono text-slate-400 font-bold')
                            self.expiry_container = ui.row().classes('gap-1 flex-wrap')

                        with ui.row().classes('items-center gap-4 text-xs font-mono'):
                            self.lbl_index_price = ui.label('Índice: $ --').classes('text-emerald-400 font-bold')
                            self.lbl_active_expiry = ui.label('DTE: -- días').classes('text-amber-400')
                            self.lbl_strikes_count = ui.label('Strikes: --').classes('text-slate-400')

                    # Gráficos de Volatilidad y Griegas
                    with ui.grid(columns=2).classes('w-full gap-4'):
                        # Sonrisa de Volatilidad
                        with ui.column().classes('bg-[#0d121f] p-4 rounded-xl border border-[#1e293b] shadow-md'):
                            with ui.row().classes('w-full justify-between items-center pb-2 border-b border-[#1e293b]'):
                                ui.label('Sonrisa de Volatilidad (Volatility Smile)').classes('text-xs font-bold font-mono text-slate-300')
                                ui.label('IV % vs Precio de Ejercicio').classes('text-[10px] text-slate-500')
                            self.chart_smile = ui.plotly(self._empty_fig('Cargando Volatilidad Implícita...')).classes('w-full h-72')

                        # Curvas de Griegas
                        with ui.column().classes('bg-[#0d121f] p-4 rounded-xl border border-[#1e293b] shadow-md'):
                            with ui.row().classes('w-full justify-between items-center pb-2 border-b border-[#1e293b]'):
                                ui.label('Estructura de Griegas (Delta & Vega)').classes('text-xs font-bold font-mono text-slate-300')
                                ui.label('Sensibilidad al precio y volatilidad').classes('text-[10px] text-slate-500')
                            self.chart_greeks = ui.plotly(self._empty_fig('Cargando Curvas de Griegas...')).classes('w-full h-72')

                    # Cadena de Opciones (Matrix)
                    with ui.column().classes('w-full bg-[#0d121f] p-4 rounded-xl border border-[#1e293b] shadow-md space-y-2'):
                        with ui.row().classes('w-full justify-between items-center pb-2 border-b border-[#1e293b]'):
                            ui.label('Cadena de Opciones Vanilla (Options Chain Matrix)').classes('text-xs font-bold font-mono text-emerald-400')
                            ui.label('Calls (Izquierda) | Strikes ATM/OTM (Centro) | Puts (Derecha)').classes('text-[10px] text-slate-400 font-mono')

                        self.chain_container = ui.column().classes('w-full overflow-x-auto')

                # ══════════════════════════════════════════════════════
                # PANEL B: CONSOLA DE EJECUCIÓN ALGORÍTMICA (TWAP / POV)
                # ══════════════════════════════════════════════════════
                with ui.tab_panel(self.tab_algos).classes('w-full p-0 space-y-4'):
                    with ui.grid(columns=3).classes('w-full gap-4'):
                        # Configuración de Orden Algorítmica (Columna Izquierda)
                        with ui.column().classes('col-span-1 bg-[#0d121f] p-4 rounded-xl border border-[#1e293b] space-y-3 shadow-md'):
                            with ui.row().classes('items-center gap-2 pb-2 border-b border-[#1e293b] w-full'):
                                ui.icon('tune', size='sm', color='amber-400')
                                ui.label('PARÁMETROS DEL ALGORITMO').classes('text-xs font-bold font-mono text-amber-400')

                            self.sel_algo_symbol = ui.select(
                                label='Activo / Par', 
                                options=['BTCUSDT', 'ETHUSDT', 'SOLUSDT'], 
                                value='BTCUSDT'
                            ).classes('w-full text-xs')

                            with ui.row().classes('w-full gap-2'):
                                self.sel_algo_side = ui.select(
                                    label='Lado', 
                                    options=['BUY', 'SELL'], 
                                    value='BUY'
                                ).classes('w-1/2 text-xs')
                                self.sel_algo_type = ui.select(
                                    label='Algoritmo', 
                                    options=['TWAP', 'POV'], 
                                    value='TWAP'
                                ).classes('w-1/2 text-xs')

                            self.input_algo_qty = ui.number(
                                label='Cantidad Total (Contratos / Monedas)', 
                                value=0.01, 
                                min=0.001, 
                                step=0.001
                            ).classes('w-full text-xs')

                            with ui.row().classes('w-full gap-2'):
                                self.input_algo_duration = ui.number(
                                    label='Duración (minutos)', 
                                    value=5, 
                                    min=1, 
                                    step=1
                                ).classes('w-1/2 text-xs')
                                self.input_algo_slices = ui.number(
                                    label='N° Tajadas (Slices)', 
                                    value=8, 
                                    min=2, 
                                    step=1
                                ).classes('w-1/2 text-xs')

                            self.sel_algo_mode = ui.select(
                                label='Entorno de Ejecución', 
                                options={'SIMULATION': 'Simulación Cuantitativa (Seguro)', 'BINANCE_TESTNET': 'Binance Futures Testnet'}, 
                                value='SIMULATION'
                            ).classes('w-full text-xs')

                            self.btn_start_algo = ui.button(
                                '▶ Iniciar Ejecución Algorítmica', 
                                on_click=self._start_algo_execution
                            ).props('color=amber-500 text-color=black font-bold').classes('w-full py-2.5 rounded-lg text-xs mt-2')

                            self.btn_cancel_algo = ui.button(
                                '⏹ Detener / Cancelar Algoritmo', 
                                on_click=self._cancel_algo_execution
                            ).props('color=rose-600 outline').classes('w-full py-2 rounded-lg text-xs')
                            self.btn_cancel_algo.set_visibility(False)

                        # Monitor y Progreso en Tiempo Real (Columnas 2 y 3)
                        with ui.column().classes('col-span-2 bg-[#0d121f] p-4 rounded-xl border border-[#1e293b] space-y-4 shadow-md'):
                            with ui.row().classes('w-full justify-between items-center pb-2 border-b border-[#1e293b]'):
                                with ui.row().classes('items-center gap-2'):
                                    ui.icon('speed', size='sm', color='emerald-400')
                                    ui.label('TELEMETRÍA DE EJECUCIÓN EN VIVO').classes('text-xs font-bold font-mono text-emerald-400')
                                self.algo_status_badge = ui.badge('INACTIVO', color='gray-700').classes('text-[10px] font-mono')

                            # Barra de progreso
                            with ui.column().classes('w-full gap-1'):
                                with ui.row().classes('w-full justify-between text-xs font-mono'):
                                    self.lbl_algo_progress_text = ui.label('Progreso: 0.0%').classes('text-slate-300 font-bold')
                                    self.lbl_algo_slices_text = ui.label('Tajadas: 0 / 0').classes('text-slate-400')
                                self.progress_bar = ui.linear_progress(value=0.0).props('color=amber-400 stripe').classes('w-full h-3 rounded-full')

                            # KPIs de Ejecución
                            with ui.grid(columns=4).classes('w-full gap-2 pt-2'):
                                with ui.column().classes('bg-[#151c2e] p-2.5 rounded-lg border border-[#1e293b] gap-0.5'):
                                    ui.label('PRECIO LLEGADA').classes('text-[9px] text-slate-400 font-mono')
                                    self.lbl_arrival_price = ui.label('$ --').classes('text-sm font-bold font-mono text-slate-200')
                                with ui.column().classes('bg-[#151c2e] p-2.5 rounded-lg border border-[#1e293b] gap-0.5'):
                                    ui.label('VWAP EJECUTADO').classes('text-[9px] text-slate-400 font-mono')
                                    self.lbl_executed_vwap = ui.label('$ --').classes('text-sm font-bold font-mono text-emerald-400')
                                with ui.column().classes('bg-[#151c2e] p-2.5 rounded-lg border border-[#1e293b] gap-0.5'):
                                    ui.label('SLIPPAGE (BPS)').classes('text-[9px] text-slate-400 font-mono')
                                    self.lbl_slippage_bps = ui.label('-- bps').classes('text-sm font-bold font-mono text-amber-400')
                                with ui.column().classes('bg-[#151c2e] p-2.5 rounded-lg border border-[#1e293b] gap-0.5'):
                                    ui.label('TOTAL EJECUTADO').classes('text-[9px] text-slate-400 font-mono')
                                    self.lbl_executed_qty = ui.label('-- / --').classes('text-sm font-bold font-mono text-blue-400')

                            # Historial de Tajadas
                            ui.label('Registro de Tajadas (Slices):').classes('text-xs font-mono text-slate-400 font-bold pt-2')
                            self.slices_table_container = ui.column().classes('w-full max-h-56 overflow-y-auto bg-[#080c14] p-2 rounded-lg border border-[#1e293b]')

            # Carga inicial de opciones
            ui.timer(0.4, self._refresh_options_async, once=True)

    # ──────────────────────────────────────────────────────────────
    # Métodos de Opciones Vanilla
    # ──────────────────────────────────────────────────────────────

    def _change_asset(self, asset: str):
        self.current_asset = asset
        self.current_expiry = None
        asyncio.create_task(self._refresh_options_async())

    def _change_expiry(self, exp_code: str):
        self.current_expiry = exp_code
        asyncio.create_task(self._refresh_options_async())

    async def _refresh_options_async(self):
        """Descarga la cadena de opciones y actualiza la matriz y los gráficos Plotly."""
        if self.is_loading_options:
            return
        self.is_loading_options = True
        self.btn_refresh.props('loading')

        loop = asyncio.get_event_loop()
        asset = self.current_asset
        exp = self.current_expiry

        try:
            matrix = await loop.run_in_executor(
                None,
                lambda: self.options_provider.get_options_chain_matrix(asset, exp)
            )
            self.options_data = matrix
            self._update_options_ui(matrix)
        except Exception as e:
            logger.error("Error cargando opciones: %s", e)
        finally:
            self.btn_refresh.props(remove='loading')
            self.is_loading_options = False

    def _update_options_ui(self, d: Dict[str, Any]):
        idx_p = d.get("index_price", 0.0)
        expirations = d.get("available_expirations", [])
        cur_exp = d.get("current_expiry", "")

        self.lbl_index_price.set_text(f"Índice {self.current_asset}: ${idx_p:,.2f}")
        chain = d.get("chain", [])
        self.lbl_strikes_count.set_text(f"Strikes: {len(chain)}")

        # Actualizar botones de expiraciones
        self.expiry_container.clear()
        with self.expiry_container:
            for e in expirations[:8]:
                is_active = (e["code"] == cur_exp)
                style = 'bg-amber-500 text-black font-bold' if is_active else 'text-slate-400 hover:text-white bg-[#151c2e]'
                if is_active:
                    self.lbl_active_expiry.set_text(f"DTE: {e['dte']} días ({e['date_str']})")
                ui.button(
                    f"{e['date_str']} ({e['dte']}d)",
                    on_click=lambda code=e["code"]: self._change_expiry(code)
                ).props('dense flat no-caps').classes(f'text-[10px] px-2 py-0.5 rounded border border-[#1e293b] {style}')

        # 1. Gráfico Sonrisa de Volatilidad
        smile = d.get("smile", {})
        stks = smile.get("strikes", [])
        c_iv = smile.get("call_iv", [])
        p_iv = smile.get("put_iv", [])

        fig_smile = go.Figure()
        if stks:
            fig_smile.add_trace(go.Scatter(
                x=stks, y=c_iv,
                mode='lines+markers', line=dict(color='#10b981', width=2),
                marker=dict(size=4), name='Call IV (%)',
                hovertemplate='Strike: $%{x}<br>Call IV: %{y:.1f}%<extra></extra>'
            ))
            fig_smile.add_trace(go.Scatter(
                x=stks, y=p_iv,
                mode='lines+markers', line=dict(color='#f43f5e', width=2),
                marker=dict(size=4), name='Put IV (%)',
                hovertemplate='Strike: $%{x}<br>Put IV: %{y:.1f}%<extra></extra>'
            ))
            if idx_p > 0:
                fig_smile.add_vline(x=idx_p, line_dash="dash", line_color="#38bdf8", annotation_text=f"Spot: ${idx_p:,.0f}", annotation_position="top", annotation_font_size=9)

        fig_smile.update_layout(
            paper_bgcolor='#0d121f', plot_bgcolor='#0d121f',
            margin=dict(l=40, r=20, t=20, b=30),
            font=dict(color='#94a3b8', family='monospace', size=10),
            xaxis=dict(gridcolor='#1e293b', zeroline=False, title='Strike (USD)'),
            yaxis=dict(gridcolor='#1e293b', zeroline=False, title='Volatilidad Implícita %'),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        self.chart_smile.update_figure(fig_smile)

        # 2. Gráfico de Griegas (Delta & Vega)
        greeks = d.get("greeks", {})
        g_stks = greeks.get("strikes", [])
        c_delta = greeks.get("call_delta", [])
        p_delta = greeks.get("put_delta", [])
        vegas = greeks.get("vega", [])

        fig_g = go.Figure()
        if g_stks:
            fig_g.add_trace(go.Scatter(
                x=g_stks, y=c_delta,
                mode='lines', line=dict(color='#3b82f6', width=2),
                name='Delta Call', hovertemplate='Strike: $%{x}<br>Delta Call: %{y:.3f}<extra></extra>'
            ))
            fig_g.add_trace(go.Scatter(
                x=g_stks, y=p_delta,
                mode='lines', line=dict(color='#eab308', width=2),
                name='Delta Put', hovertemplate='Strike: $%{x}<br>Delta Put: %{y:.3f}<extra></extra>'
            ))
            fig_g.add_trace(go.Scatter(
                x=g_stks, y=vegas,
                mode='lines', line=dict(color='#a855f7', width=1.5, dash='dot'),
                name='Vega', hovertemplate='Strike: $%{x}<br>Vega: %{y:.3f}<extra></extra>'
            ))
            fig_g.add_hline(y=0, line_color="#64748b", line_width=1)

        fig_g.update_layout(
            paper_bgcolor='#0d121f', plot_bgcolor='#0d121f',
            margin=dict(l=40, r=20, t=20, b=30),
            font=dict(color='#94a3b8', family='monospace', size=10),
            xaxis=dict(gridcolor='#1e293b', zeroline=False, title='Strike (USD)'),
            yaxis=dict(gridcolor='#1e293b', zeroline=False, title='Sensibilidad Griegas'),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        self.chart_greeks.update_figure(fig_g)

        # 3. Renderizar Tabla Options Chain Matrix
        self.chain_container.clear()
        with self.chain_container:
            # Cabecera
            with ui.row().classes('w-full justify-between bg-[#151c2e] p-2 rounded text-[11px] font-mono font-bold text-slate-400 border-b border-[#1e293b]'):
                with ui.row().classes('w-5/12 justify-between pr-2 border-r border-[#1e293b] text-emerald-400'):
                    ui.label('CALL IV')
                    ui.label('DELTA')
                    ui.label('THETA')
                    ui.label('PRECIO CALL')
                with ui.row().classes('w-2/12 justify-center text-amber-400'):
                    ui.label('STRIKE (USD)')
                with ui.row().classes('w-5/12 justify-between pl-2 border-l border-[#1e293b] text-rose-400'):
                    ui.label('PRECIO PUT')
                    ui.label('THETA')
                    ui.label('DELTA')
                    ui.label('PUT IV')

            # Filas de Strikes
            for row in chain[:25]:
                stk = row["strike"]
                c = row["call"]
                p = row["put"]
                is_atm = abs(stk - idx_p) < (idx_p * 0.015)
                row_bg = "bg-amber-500/10 border-amber-500/30" if is_atm else "hover:bg-white/5 border-transparent"

                with ui.row().classes(f'w-full justify-between items-center p-1.5 rounded text-[11px] font-mono border {row_bg}'):
                    # Datos Call
                    with ui.row().classes('w-5/12 justify-between pr-2 text-slate-300'):
                        ui.label(f"{c['mark_iv']:.1f}%" if c and c['mark_iv']>0 else "--").classes('text-emerald-400')
                        ui.label(f"{c['delta']:+.3f}" if c else "--")
                        ui.label(f"{c['theta']:.1f}" if c else "--").classes('text-slate-400')
                        ui.label(f"${c['mark_price']:,.1f}" if c else "--").classes('font-bold text-white')

                    # Strike Central
                    with ui.row().classes('w-2/12 justify-center'):
                        strike_color = "text-amber-400 font-bold" if is_atm else "text-slate-200 font-semibold"
                        ui.label(f"${stk:,.0f}").classes(strike_color)

                    # Datos Put
                    with ui.row().classes('w-5/12 justify-between pl-2 text-slate-300'):
                        ui.label(f"${p['mark_price']:,.1f}" if p else "--").classes('font-bold text-white')
                        ui.label(f"{p['theta']:.1f}" if p else "--").classes('text-slate-400')
                        ui.label(f"{p['delta']:+.3f}" if p else "--")
                        ui.label(f"{p['mark_iv']:.1f}%" if p and p['mark_iv']>0 else "--").classes('text-rose-400')

    # ──────────────────────────────────────────────────────────────
    # Métodos de Ejecución Algorítmica (TWAP & POV)
    # ──────────────────────────────────────────────────────────────

    def _start_algo_execution(self):
        """Inicia una orden TWAP con callback en tiempo real a la interfaz."""
        sym = self.sel_algo_symbol.value
        side = self.sel_algo_side.value
        qty = float(self.input_algo_qty.value or 0.01)
        dur = float(self.input_algo_duration.value or 5)
        slices = int(self.input_algo_slices.value or 8)
        mode = self.sel_algo_mode.value

        self.btn_start_algo.set_visibility(False)
        self.btn_cancel_algo.set_visibility(True)
        self.algo_status_badge.set_text('EJECUTANDO...')
        self.algo_status_badge.props('color=amber-600')
        self.slices_table_container.clear()

        # Lanzar TWAP asíncrono
        asyncio.create_task(
            algo_engine.execute_twap_async(
                symbol=sym,
                side=side,
                total_quantity=qty,
                duration_minutes=dur,
                num_slices=slices,
                mode=mode,
                status_callback=self._on_algo_task_update
            )
        )

    def _cancel_algo_execution(self):
        """Detiene el algoritmo en curso."""
        for t in list(algo_engine.active_tasks.values()):
            t.cancel()
        self.btn_cancel_algo.set_visibility(False)
        self.btn_start_algo.set_visibility(True)
        self.algo_status_badge.set_text('CANCELADO')
        self.algo_status_badge.props('color=rose-800')

    def _on_algo_task_update(self, task: AlgoExecutionTask):
        """Callback invocado en cada tajada completada."""
        self.active_algo_task = task
        pct = task.progress_pct
        self.progress_bar.set_value(pct / 100.0)
        self.lbl_algo_progress_text.set_text(f"Progreso: {pct:.1f}% ({task.mode})")
        self.lbl_algo_slices_text.set_text(f"Tajadas: {task.slices_completed} / {task.num_slices}")

        self.lbl_arrival_price.set_text(f"${task.arrival_price:,.2f}")
        self.lbl_executed_vwap.set_text(f"${task.executed_vwap:,.2f}")
        self.lbl_slippage_bps.set_text(f"{task.slippage_bps:+.1f} bps")
        self.lbl_executed_qty.set_text(f"{task.executed_quantity:.4f} / {task.total_quantity:.4f}")

        # Renderizar última tajada en la lista
        if task.slices_history:
            last = task.slices_history[-1]
            with self.slices_table_container:
                with ui.row().classes('w-full justify-between items-center py-1 px-2 border-b border-white/5 text-[11px] font-mono'):
                    ui.label(f"Tajada #{last['slice_num']} ({last['timestamp']})").classes('text-slate-400')
                    ui.label(f"Qty: {last['quantity']:.4f} @ ${last['price']:,.2f}").classes('text-white font-bold')
                    ui.label(f"VWAP: ${last['accum_vwap']:,.2f}").classes('text-emerald-400')

        if task.status == "COMPLETED":
            self.algo_status_badge.set_text('COMPLETADO ✅')
            self.algo_status_badge.props('color=emerald-800')
            self.btn_cancel_algo.set_visibility(False)
            self.btn_start_algo.set_visibility(True)

    def _empty_fig(self, text: str) -> go.Figure:
        fig = go.Figure()
        fig.update_layout(
            paper_bgcolor='#0d121f', plot_bgcolor='#0d121f',
            annotations=[dict(
                text=text, xref="paper", yref="paper",
                x=0.5, y=0.5, showarrow=False,
                font=dict(size=12, color="#64748b", family="monospace")
            )],
            xaxis=dict(visible=False), yaxis=dict(visible=False),
            margin=dict(l=20, r=20, t=20, b=20)
        )
        return fig


def render_options_algo_page():
    """Función de entrada para instanciar y renderizar la página de opciones en NiceGUI."""
    page = OptionsAlgoPage()
    page.render()
    return page
