"""
Página de Análisis Gráfico de Derivados (Futuros USDⓈ-M y COIN-M) de Binance.
Visualiza métricas cuantitativas en tiempo real con gráficos Plotly:
- Historial de Precios OHLCV (Velas Japonesas) y Volumen
- Funding Rate actual e histórico con APR proyectado
- Open Interest (Interés Abierto) vs Precio
- Ratios Long/Short de Top Traders (Ballenas) vs Retail
- Presión de Grandes Tomadores (Taker Buy/Sell Volume)
- Soporte para rango de fechas personalizado y persistencia local en Parquet/CSV.
"""
import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import plotly.graph_objects as go
from nicegui import ui

from data_layer.data_sources.binance_derivatives_provider import BinanceDerivativesProvider

logger = logging.getLogger("DerivativesAnalyzerPage")


class DerivativesAnalyzerPage:
    """Controlador y Renderizador del Dashboard Gráfico de Derivados Binance."""

    def __init__(self):
        self.provider = BinanceDerivativesProvider()
        self.current_symbol = "BTCUSDT"
        self.current_period = "1h"
        
        # Rango de fechas por defecto: últimos 30 días a hoy
        today = datetime.now()
        thirty_days_ago = today - timedelta(days=30)
        self.start_date_str = thirty_days_ago.strftime("%Y-%m-%d")
        self.end_date_str = today.strftime("%Y-%m-%d")
        
        self.data: Dict[str, Any] = {}
        self.is_loading = False

    def render(self):
        """Construye la interfaz visual en NiceGUI."""
        with ui.column().classes('w-full h-full p-4 lg:p-6 space-y-4 bg-[#080c14] text-slate-200'):
            # ──────────────────────────────────────────────────────────
            # 1. Header con Controles de Activo, Temporalidad y Fechas
            # ──────────────────────────────────────────────────────────
            with ui.column().classes('w-full bg-[#0d121f] p-4 rounded-xl border border-[#1e293b] gap-3'):
                # Fila superior: Título y selectores de símbolo/periodo
                with ui.row().classes('w-full justify-between items-center flex-wrap gap-2'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('query_stats', size='md', color='amber-400')
                        with ui.column().classes('gap-0'):
                            ui.label('ANÁLISIS CUANTITATIVO DE DERIVADOS').classes('text-base font-bold tracking-wider text-amber-400 font-mono')
                            ui.label('Binance Futures USDⓈ-M • Funding Rate • Open Interest • Ratios Ballenas/Retail • Flujo Taker').classes('text-xs text-slate-400')

                    with ui.row().classes('items-center gap-2 flex-wrap'):
                        # Selector dinámico de Símbolo
                        self.symbol_buttons_row = ui.row().classes('bg-[#151c2e] p-1 rounded-lg border border-[#1e293b]')
                        self._render_symbol_buttons()

                        # Selector dinámico de Periodo para Ratios y OI
                        self.period_buttons_row = ui.row().classes('bg-[#151c2e] p-1 rounded-lg border border-[#1e293b]')
                        self._render_period_buttons()

                        # Botón de Actualizar
                        self.btn_refresh = ui.button(
                            'Actualizar', 
                            icon='refresh', 
                            on_click=self._refresh_data_async
                        ).props('dense outline color=amber-400').classes('text-xs text-amber-400 px-3 py-1.5 rounded-lg')

                # Fila inferior: Rango de Fechas, Presets y Exportación Local
                with ui.row().classes('w-full justify-between items-center pt-2 border-t border-[#1e293b]/70 flex-wrap gap-3'):
                    with ui.row().classes('items-center gap-3 flex-wrap'):
                        ui.icon('date_range', size='xs', color='slate-400')
                        ui.label('Rango:').classes('text-xs font-mono text-slate-400 font-bold')
                        
                        ui.label('Desde:').classes('text-xs font-mono text-slate-500')
                        self.input_start_date = ui.input(value=self.start_date_str).props('type=date dense outlined').classes('w-36 text-xs bg-[#151c2e] text-slate-200 border-[#1e293b]')
                        self.input_start_date.on('change', self._on_date_changed)

                        ui.label('Hasta:').classes('text-xs font-mono text-slate-500')
                        self.input_end_date = ui.input(value=self.end_date_str).props('type=date dense outlined').classes('w-36 text-xs bg-[#151c2e] text-slate-200 border-[#1e293b]')
                        self.input_end_date.on('change', self._on_date_changed)

                        # Presets de fecha rápidos
                        with ui.row().classes('bg-[#151c2e] p-1 rounded-lg border border-[#1e293b] gap-1'):
                            for preset in ["7D", "30D", "90D", "YTD", "1A"]:
                                ui.button(
                                    preset, 
                                    on_click=lambda p=preset: self._apply_preset(p)
                                ).props('dense flat no-caps').classes('text-[11px] text-slate-300 hover:text-white px-2 py-0.5 rounded')

                    with ui.row().classes('items-center gap-3'):
                        # Badge de almacenamiento (Caché Parquet vs API)
                        self.badge_cache = ui.label('🌐 Binance API').classes('text-[11px] font-mono px-2.5 py-1 rounded-full bg-blue-950/60 text-blue-400 border border-blue-800')

                        # Botón exportar CSV
                        ui.button(
                            'Descargar CSV', 
                            icon='download', 
                            on_click=self._export_csv_action
                        ).props('dense outline color=emerald-400').classes('text-xs text-emerald-400 px-3 py-1.5 rounded-lg')

            # ──────────────────────────────────────────────────────────
            # 2. Tarjetas de KPIs Cuantitativos Superiores
            # ──────────────────────────────────────────────────────────
            with ui.grid(columns=4).classes('w-full gap-4'):
                # KPI 1: Funding Rate & APR
                with ui.column().classes('bg-[#0d121f] p-4 rounded-xl border border-[#1e293b] gap-1 shadow-md'):
                    ui.label('FUNDING RATE (8H)').classes('text-[11px] font-mono text-slate-400 font-bold uppercase')
                    self.kpi_funding_val = ui.label('-- %').classes('text-xl font-bold font-mono text-emerald-400')
                    self.kpi_funding_apr = ui.label('APR: -- % anualizado').classes('text-xs text-slate-400 font-mono')
                    self.kpi_funding_next = ui.label('Siguiente cobro: --').classes('text-[10px] text-amber-400 font-mono')

                # KPI 2: Open Interest
                with ui.column().classes('bg-[#0d121f] p-4 rounded-xl border border-[#1e293b] gap-1 shadow-md'):
                    ui.label('OPEN INTEREST TOTAL').classes('text-[11px] font-mono text-slate-400 font-bold uppercase')
                    self.kpi_oi_val = ui.label('$ -- USD').classes('text-xl font-bold font-mono text-blue-400')
                    self.kpi_oi_change = ui.label('Variación: -- %').classes('text-xs text-slate-400 font-mono')
                    self.kpi_mark_price = ui.label('Mark Price: $ --').classes('text-[10px] text-slate-400 font-mono')

                # KPI 3: Sentimiento Ballenas vs Retail
                with ui.column().classes('bg-[#0d121f] p-4 rounded-xl border border-[#1e293b] gap-1 shadow-md'):
                    ui.label('TOP TRADERS L/S (BALLENAS)').classes('text-[11px] font-mono text-slate-400 font-bold uppercase')
                    self.kpi_top_ls_val = ui.label('-- Ratio').classes('text-xl font-bold font-mono text-amber-400')
                    self.kpi_glob_ls_val = ui.label('Retail Global: -- Ratio').classes('text-xs text-slate-400 font-mono')
                    self.kpi_divergence = ui.label('Divergencia: --').classes('text-[10px] text-slate-400 font-mono')

                # KPI 4: Flujo Taker (Grandes Tomadores)
                with ui.column().classes('bg-[#0d121f] p-4 rounded-xl border border-[#1e293b] gap-1 shadow-md'):
                    ui.label('PRESIÓN TAKER (BUY/SELL)').classes('text-[11px] font-mono text-slate-400 font-bold uppercase')
                    self.kpi_taker_val = ui.label('-- Ratio').classes('text-xl font-bold font-mono text-purple-400')
                    self.kpi_taker_posture = ui.label('Postura: --').classes('text-xs text-slate-400 font-mono')
                    self.kpi_basis_val = ui.label('Basis: -- %').classes('text-[10px] text-slate-400 font-mono')

            # ──────────────────────────────────────────────────────────
            # 3. Gráfico Principal de Precio Histórico OHLCV & Volumen
            # ──────────────────────────────────────────────────────────
            with ui.column().classes('w-full bg-[#0d121f] p-4 rounded-xl border border-[#1e293b] shadow-md'):
                with ui.row().classes('w-full justify-between items-center pb-2 border-b border-[#1e293b]'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('candlestick_chart', size='sm', color='amber-400')
                        self.lbl_chart_price_title = ui.label('HISTORIAL DE PRECIO OHLCV & VOLUMEN').classes('text-xs font-bold font-mono text-amber-400')
                    ui.label('Velas Japonesas y volumen sincronizados con el periodo y rango de fechas').classes('text-[10px] text-slate-500 font-mono')
                self.chart_price = ui.plotly(self._empty_fig('Cargando Historial OHLCV...')).classes('w-full h-80')

            # ──────────────────────────────────────────────────────────
            # 4. Gráficos Plotly Interactivos (Grid 2x2)
            # ──────────────────────────────────────────────────────────
            with ui.grid(columns=2).classes('w-full gap-4'):
                # Gráfico 1: Funding Rate Histórico
                with ui.column().classes('bg-[#0d121f] p-4 rounded-xl border border-[#1e293b] shadow-md'):
                    with ui.row().classes('w-full justify-between items-center pb-2 border-b border-[#1e293b]'):
                        ui.label('Evolución de Funding Rate (Cobros en Rango)').classes('text-xs font-bold font-mono text-slate-300')
                        ui.label('Verde = Longs pagan a Shorts | Rojo = Shorts pagan').classes('text-[10px] text-slate-500')
                    self.chart_funding = ui.plotly(self._empty_fig('Cargando Funding Rate...')).classes('w-full h-80')

                # Gráfico 2: Open Interest vs Mark Price
                with ui.column().classes('bg-[#0d121f] p-4 rounded-xl border border-[#1e293b] shadow-md'):
                    with ui.row().classes('w-full justify-between items-center pb-2 border-b border-[#1e293b]'):
                        ui.label('Interés Abierto (OI en USD) vs Tiempo').classes('text-xs font-bold font-mono text-slate-300')
                        ui.label('Acumulación y desapalancamiento').classes('text-[10px] text-slate-500')
                    self.chart_oi = ui.plotly(self._empty_fig('Cargando Open Interest...')).classes('w-full h-80')

                # Gráfico 3: Divergencia Top Traders vs Global
                with ui.column().classes('bg-[#0d121f] p-4 rounded-xl border border-[#1e293b] shadow-md'):
                    with ui.row().classes('w-full justify-between items-center pb-2 border-b border-[#1e293b]'):
                        ui.label('Divergencia Long/Short: Ballenas vs Retail').classes('text-xs font-bold font-mono text-slate-300')
                        ui.label('Línea 1.0 = Neutral').classes('text-[10px] text-slate-500')
                    self.chart_ls = ui.plotly(self._empty_fig('Cargando Ratios Long/Short...')).classes('w-full h-80')

                # Gráfico 4: Flujo Taker (Volumen Comprador vs Vendedor Agresivo)
                with ui.column().classes('bg-[#0d121f] p-4 rounded-xl border border-[#1e293b] shadow-md'):
                    with ui.row().classes('w-full justify-between items-center pb-2 border-b border-[#1e293b]'):
                        ui.label('Volumen Taker Agresivo (Buy vs Sell)').classes('text-xs font-bold font-mono text-slate-300')
                        ui.label('Presión neta de mercado').classes('text-[10px] text-slate-500')
                    self.chart_taker = ui.plotly(self._empty_fig('Cargando Flujo Taker...')).classes('w-full h-80')

            # Carga inicial asíncrona
            ui.timer(0.3, self._refresh_data_async, once=True)

    # ──────────────────────────────────────────────────────────────
    # Métodos y Actualizaciones
    # ──────────────────────────────────────────────────────────────

    def _render_symbol_buttons(self):
        """Renderiza los botones de selección de activo con el estado activo actualizado."""
        self.symbol_buttons_row.clear()
        with self.symbol_buttons_row:
            for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]:
                is_active = (s == self.current_symbol)
                btn_style = 'bg-amber-500 text-black font-bold' if is_active else 'text-slate-400 hover:text-white'
                ui.button(
                    s.replace("USDT", ""), 
                    on_click=lambda sym=s: self._change_symbol(sym)
                ).props('dense flat no-caps').classes(f'text-xs px-2.5 py-1 rounded {btn_style}')

    def _render_period_buttons(self):
        """Renderiza los botones de timeframe con el estado activo actualizado."""
        self.period_buttons_row.clear()
        with self.period_buttons_row:
            for p in ["15m", "1h", "4h", "1d"]:
                is_active = (p == self.current_period)
                btn_style = 'bg-blue-600 text-white font-bold' if is_active else 'text-slate-400 hover:text-white'
                ui.button(
                    p, 
                    on_click=lambda per=p: self._change_period(per)
                ).props('dense flat no-caps').classes(f'text-xs px-2.5 py-1 rounded {btn_style}')

    def _change_symbol(self, sym: str):
        if self.current_symbol == sym:
            return
        self.current_symbol = sym
        self._render_symbol_buttons()
        asyncio.create_task(self._refresh_data_async())

    def _change_period(self, per: str):
        if self.current_period == per:
            return
        self.current_period = per
        self._render_period_buttons()
        asyncio.create_task(self._refresh_data_async())

    def _on_date_changed(self):
        """Manejador cuando el usuario cambia manualmente las fechas en los inputs."""
        if hasattr(self, 'input_start_date') and self.input_start_date.value:
            self.start_date_str = self.input_start_date.value
        if hasattr(self, 'input_end_date') and self.input_end_date.value:
            self.end_date_str = self.input_end_date.value
        asyncio.create_task(self._refresh_data_async())

    def _apply_preset(self, preset: str):
        """Aplica rangos temporales rápidos (7D, 30D, 90D, YTD, 1A)."""
        today = datetime.now()
        if preset == "7D":
            self.start_date_str = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        elif preset == "30D":
            self.start_date_str = (today - timedelta(days=30)).strftime("%Y-%m-%d")
        elif preset == "90D":
            self.start_date_str = (today - timedelta(days=90)).strftime("%Y-%m-%d")
        elif preset == "YTD":
            self.start_date_str = f"{today.year}-01-01"
        elif preset == "1A":
            self.start_date_str = (today - timedelta(days=365)).strftime("%Y-%m-%d")
        self.end_date_str = today.strftime("%Y-%m-%d")

        if hasattr(self, 'input_start_date'):
            self.input_start_date.value = self.start_date_str
        if hasattr(self, 'input_end_date'):
            self.input_end_date.value = self.end_date_str

        asyncio.create_task(self._refresh_data_async())

    def _export_csv_action(self):
        """Exporta los datos a CSV en data/derivatives/ y lanza la descarga en el navegador."""
        if not self.data or not self.data.get("klines"):
            ui.notify("No hay datos cargados para exportar.", type="warning")
            return
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.current_symbol}_derivatives_{self.current_period}_{timestamp_str}.csv"
        os.makedirs(os.path.join("data", "derivatives"), exist_ok=True)
        filepath = os.path.join("data", "derivatives", filename)
        ok = self.provider.export_dashboard_to_csv(self.data, filepath)
        if ok:
            ui.notify(f"Archivo exportado: {filename}", type="positive")
            ui.download(filepath, filename=filename)
        else:
            ui.notify("Error al exportar archivo CSV", type="negative")

    async def _refresh_data_async(self):
        """Descarga los datos de derivados en background y actualiza Plotly con el periodo y rango seleccionados."""
        if self.is_loading:
            return
        self.is_loading = True
        self.btn_refresh.props('loading')

        loop = asyncio.get_event_loop()
        clean_sym = self.current_symbol
        period = self.current_period
        s_date = self.start_date_str
        e_date = self.end_date_str

        try:
            # Llamada síncrona en hilo separado para no bloquear event loop
            data = await loop.run_in_executor(
                None, 
                lambda: self.provider.get_aggregated_derivatives_dashboard(
                    clean_sym, 
                    period=period,
                    start_date_str=s_date,
                    end_date_str=e_date
                )
            )
            self.data = data

            # Actualizar badge de caché vs red
            if data.get("from_cache"):
                self.badge_cache.set_text("⚡ Caché Local (Parquet)")
                self.badge_cache.classes(replace="text-[11px] font-mono px-2.5 py-1 rounded-full bg-emerald-950/60 text-emerald-400 border border-emerald-800")
            else:
                self.badge_cache.set_text("🌐 Binance API")
                self.badge_cache.classes(replace="text-[11px] font-mono px-2.5 py-1 rounded-full bg-blue-950/60 text-blue-400 border border-blue-800")

            self._update_kpis(data)
            self._update_charts(data)
        except Exception as e:
            logger.error("Error en _refresh_data_async de derivados: %s", e)
        finally:
            self.btn_refresh.props(remove='loading')
            self.is_loading = False

    def _update_kpis(self, d: Dict[str, Any]):
        s = d.get("summary", {})
        premium = d.get("premium", {})

        # KPI 1
        rate_pct = s.get("latest_funding_pct", 0.0)
        apr = s.get("annualized_apr", 0.0)
        rate_color = 'text-emerald-400' if rate_pct >= 0 else 'text-rose-400'
        sign = "+" if rate_pct > 0 else ""
        self.kpi_funding_val.set_text(f"{sign}{rate_pct:.4f} %")
        self.kpi_funding_val.classes(replace=rate_color)
        self.kpi_funding_apr.set_text(f"APR Proyectado: {sign}{apr:.2f} %")

        next_time = premium.get("next_funding_time", 0)
        if next_time > 0:
            dt_next = datetime.fromtimestamp(next_time / 1000.0)
            self.kpi_funding_next.set_text(f"Próximo cobro: {dt_next.strftime('%H:%M:%S')} UTC")

        # KPI 2
        oi_usd = s.get("latest_oi_usd", 0.0)
        oi_chg = s.get("oi_change_pct", 0.0)
        chg_sign = "+" if oi_chg >= 0 else ""
        chg_color = 'text-emerald-400' if oi_chg >= 0 else 'text-rose-400'
        self.kpi_oi_val.set_text(f"${oi_usd:,.0f} USD")
        self.kpi_oi_change.set_text(f"Variación: {chg_sign}{oi_chg:.2f}% ({self.current_period})")
        self.kpi_oi_change.classes(replace=chg_color)
        self.kpi_mark_price.set_text(f"Mark: ${s.get('mark_price', 0.0):,.2f} | Index: ${s.get('index_price', 0.0):,.2f}")

        # KPI 3
        top_ratio = s.get("latest_top_ratio", 1.0)
        top_long = s.get("latest_top_long", 50.0)
        glob_ratio = s.get("latest_glob_ratio", 1.0)
        div = s.get("sentiment_divergence", 0.0)
        self.kpi_top_ls_val.set_text(f"{top_ratio:.2f} ({top_long:.1f}% Long)")
        self.kpi_glob_ls_val.set_text(f"Retail Global: {glob_ratio:.2f} ({s.get('latest_glob_long', 50):.1f}% Long)")
        div_text = "Ballenas más alcistas que retail 📈" if div > 0.05 else ("Retail más alcista que ballenas ⚠️" if div < -0.05 else "Sentimiento alineado ⚖️")
        self.kpi_divergence.set_text(div_text)

        # KPI 4
        taker_ratio = s.get("latest_taker_ratio", 1.0)
        basis = s.get("basis_pct", 0.0)
        self.kpi_taker_val.set_text(f"{taker_ratio:.2f}x")
        posture = "Presión Compradora 🟢" if taker_ratio > 1.05 else ("Presión Vendedora 🔴" if taker_ratio < 0.95 else "Equilibrio ⚪")
        self.kpi_taker_posture.set_text(f"Postura: {posture}")
        self.kpi_basis_val.set_text(f"Basis Spread: {basis:+.3f}%")

    def _update_charts(self, d: Dict[str, Any]):
        # 0. Chart Principal OHLCV Candlestick + Volume
        klines = d.get("klines", [])
        if klines:
            times_k = [datetime.fromtimestamp(x["timestamp"] / 1000.0) for x in klines]
            opens = [x["open"] for x in klines]
            highs = [x["high"] for x in klines]
            lows = [x["low"] for x in klines]
            closes = [x["close"] for x in klines]
            volumes = [x["volume"] for x in klines]

            fig_p = go.Figure()
            fig_p.add_trace(go.Candlestick(
                x=times_k,
                open=opens,
                high=highs,
                low=lows,
                close=closes,
                name='Precio (USDT)',
                increasing_line_color='#10b981',
                decreasing_line_color='#f43f5e',
                yaxis='y1'
            ))
            vol_colors = ['rgba(16, 185, 129, 0.4)' if c >= o else 'rgba(244, 63, 94, 0.4)' for o, c in zip(opens, closes)]
            fig_p.add_trace(go.Bar(
                x=times_k,
                y=volumes,
                name='Volumen',
                marker_color=vol_colors,
                yaxis='y2',
                opacity=0.6,
                hovertemplate='%{x|%d %b %H:%M}<br>Volumen: %{y:,.2f}<extra></extra>'
            ))

            max_vol = max(volumes) if volumes else 1.0
            fig_p.update_layout(
                paper_bgcolor='#0d121f',
                plot_bgcolor='#0d121f',
                margin=dict(l=50, r=50, t=20, b=30),
                font=dict(color='#94a3b8', family='monospace', size=10),
                xaxis=dict(gridcolor='#1e293b', rangeslider=dict(visible=False)),
                yaxis=dict(gridcolor='#1e293b', title='Precio (USDT)', side='left'),
                yaxis2=dict(
                    title='Volumen',
                    side='right',
                    overlaying='y',
                    showgrid=False,
                    range=[0, max_vol * 4]
                ),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            self.chart_price.update_figure(fig_p)

        # 1. Chart Funding Rate
        funding_hist = d.get("funding_hist", [])
        if funding_hist:
            times = [datetime.fromtimestamp(x["funding_time"]/1000.0) for x in funding_hist]
            rates = [x["funding_rate_pct"] for x in funding_hist]
            colors = ['#10b981' if r >= 0 else '#f43f5e' for r in rates]

            fig_f = go.Figure()
            fig_f.add_trace(go.Bar(
                x=times, y=rates,
                marker_color=colors,
                name='Funding Rate (%)',
                hovertemplate='%{x|%d %b %H:%M}<br>Funding: %{y:.4f}%<extra></extra>'
            ))
            fig_f.add_hline(y=0, line_dash="dash", line_color="#64748b", line_width=1)
            # Umbral de sobrecalentamiento (+0.03%)
            fig_f.add_hline(y=0.03, line_dash="dot", line_color="#eab308", annotation_text="Sobrecalentamiento Long (>0.03%)", annotation_position="top left", annotation_font_size=9)
            fig_f.update_layout(
                paper_bgcolor='#0d121f', plot_bgcolor='#0d121f',
                margin=dict(l=40, r=20, t=20, b=30),
                font=dict(color='#94a3b8', family='monospace', size=10),
                xaxis=dict(gridcolor='#1e293b', zeroline=False),
                yaxis=dict(gridcolor='#1e293b', zeroline=False, title='Funding Rate %'),
                showlegend=False
            )
            self.chart_funding.update_figure(fig_f)

        # 2. Chart Open Interest vs Precio
        oi_hist = d.get("oi_hist", [])
        if oi_hist:
            times_oi = [datetime.fromtimestamp(x["timestamp"]/1000.0) for x in oi_hist]
            oi_vals = [x["sum_open_interest_usd"] / 1e9 for x in oi_hist] # en Billones USD

            fig_oi = go.Figure()
            fig_oi.add_trace(go.Scatter(
                x=times_oi, y=oi_vals,
                mode='lines', fill='tozeroy',
                line=dict(color='#3b82f6', width=2),
                fillcolor='rgba(59, 130, 246, 0.15)',
                name='Open Interest (Billion USD)',
                hovertemplate='%{x|%d %b %H:%M}<br>OI: $%{y:.2f}B USD<extra></extra>'
            ))
            fig_oi.update_layout(
                paper_bgcolor='#0d121f', plot_bgcolor='#0d121f',
                margin=dict(l=40, r=20, t=20, b=30),
                font=dict(color='#94a3b8', family='monospace', size=10),
                xaxis=dict(gridcolor='#1e293b', zeroline=False),
                yaxis=dict(gridcolor='#1e293b', zeroline=False, title='OI (Billones USD)'),
                showlegend=False
            )
            self.chart_oi.update_figure(fig_oi)

        # 3. Chart Ratios Long/Short Divergente
        top_ls = d.get("top_ls", [])
        glob_ls = d.get("glob_ls", [])
        if top_ls and glob_ls:
            times_ls = [datetime.fromtimestamp(x["timestamp"]/1000.0) for x in top_ls]
            top_r = [x["long_short_ratio"] for x in top_ls]
            glob_r = [x["long_short_ratio"] for x in glob_ls[:len(top_ls)]]

            fig_ls = go.Figure()
            fig_ls.add_trace(go.Scatter(
                x=times_ls, y=top_r,
                mode='lines', line=dict(color='#f59e0b', width=2.5),
                name='Top Traders (Ballenas)',
                hovertemplate='%{x|%d %b %H:%M}<br>Top Traders L/S: %{y:.2f}<extra></extra>'
            ))
            fig_ls.add_trace(go.Scatter(
                x=times_ls, y=glob_r,
                mode='lines', line=dict(color='#94a3b8', width=1.5, dash='dash'),
                name='Cuentas Globales (Retail)',
                hovertemplate='%{x|%d %b %H:%M}<br>Global Retail L/S: %{y:.2f}<extra></extra>'
            ))
            fig_ls.add_hline(y=1.0, line_dash="dot", line_color="#ef4444", line_width=1, annotation_text="Ratio 1.0 (50/50)", annotation_font_size=9)
            fig_ls.update_layout(
                paper_bgcolor='#0d121f', plot_bgcolor='#0d121f',
                margin=dict(l=40, r=20, t=20, b=30),
                font=dict(color='#94a3b8', family='monospace', size=10),
                xaxis=dict(gridcolor='#1e293b', zeroline=False),
                yaxis=dict(gridcolor='#1e293b', zeroline=False, title='Ratio Long / Short'),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            self.chart_ls.update_figure(fig_ls)

        # 4. Chart Taker Flow
        taker_ls = d.get("taker_ls", [])
        if taker_ls:
            times_tk = [datetime.fromtimestamp(x["timestamp"]/1000.0) for x in taker_ls]
            buy_v = [x["buy_volume"] for x in taker_ls]
            sell_v = [x["sell_volume"] for x in taker_ls]

            fig_tk = go.Figure()
            fig_tk.add_trace(go.Bar(
                x=times_tk, y=buy_v,
                marker_color='#10b981',
                name='Taker Buy Vol',
                hovertemplate='%{x|%d %b %H:%M}<br>Compras agresivas: %{y:,.1f}<extra></extra>'
            ))
            fig_tk.add_trace(go.Bar(
                x=times_tk, y=[-v for v in sell_v],
                marker_color='#f43f5e',
                name='Taker Sell Vol',
                hovertemplate='%{x|%d %b %H:%M}<br>Ventas agresivas: %{y:,.1f}<extra></extra>'
            ))
            fig_tk.add_hline(y=0, line_color="#64748b", line_width=1)
            fig_tk.update_layout(
                barmode='relative',
                paper_bgcolor='#0d121f', plot_bgcolor='#0d121f',
                margin=dict(l=40, r=20, t=20, b=30),
                font=dict(color='#94a3b8', family='monospace', size=10),
                xaxis=dict(gridcolor='#1e293b', zeroline=False),
                yaxis=dict(gridcolor='#1e293b', zeroline=False, title='Volumen Taker'),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            self.chart_taker.update_figure(fig_tk)

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


def render_derivatives_analyzer_page():
    """Función de entrada para instanciar y renderizar la página en NiceGUI."""
    page = DerivativesAnalyzerPage()
    page.render()
    return page
