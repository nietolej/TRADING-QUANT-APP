from nicegui import ui, run
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from strategy_engine.mle_thermometer import MLEThermometer

class MLEThermometerPage:
    def __init__(self):
        self.thermometer = MLEThermometer(z_score_window=90)
        self.mle_timer = None
        self.show_price = True
        self.show_norm = True
        self.show_raw = False

    async def _update_thermometer_ui(self):
        try:
            # Ejecutamos el cálculo sincrónico en un hilo separado para no bloquear
            data = await run.io_bound(self.thermometer.get_thermometer_value)
            
            if hasattr(self, 'mle_gauge'):
                theta = data.get('theta', 50.0)
                self.mle_gauge.set_value(theta / 100.0)  # progress usa 0.0 a 1.0
                
                # Cambiar color según theta
                if theta > 70:
                    color = 'green'
                    status = 'Liquidez Abundante (Bajo Riesgo)'
                elif theta > 30:
                    color = 'yellow'
                    status = 'Neutral'
                else:
                    color = 'red'
                    status = 'Alerta (Riesgo Alto)'
                    
                self.mle_gauge.props(f'color="{color}"')
                self.mle_value_label.set_text(f"{theta:.1f}% - {status}")
                self.mle_details.set_text(f"Poder Adquisitivo (N_ssr): {data.get('n_ssr', 0):.1f} | Velocidad (N_inf): {data.get('n_inf', 0):.1f} | Apalancamiento (N_lev): {data.get('n_lev', 0):.1f}")
                
            # Actualizar gráficas si existen y los títulos con los valores crudos
            btc_hist = data.get('history_btc')
            
            if hasattr(self, 'chart_ssr') and 'history_ssr' in data and not data['history_ssr'].empty:
                raw = data.get('raw_ssr')
                title = f'Poder Adquisitivo (N_ssr) | Valor crudo Z-Score: {raw:.3f}' if raw is not None else 'Poder Adquisitivo (N_ssr)'
                self.chart_ssr.update_figure(self._build_chart(title, data['history_ssr'], data.get('history_raw_ssr'), '#10b981', btc_hist, 'Z-Score'))
            
            if hasattr(self, 'chart_inf') and 'history_inf' in data and not data['history_inf'].empty:
                raw = data.get('raw_inf')
                title = f'Velocidad (N_inf) | Flujo crudo Inflow: {raw:,.0f} USD' if raw is not None else 'Velocidad (N_inf)'
                self.chart_inf.update_figure(self._build_chart(title, data['history_inf'], data.get('history_raw_inf'), '#3b82f6', btc_hist, 'USD'))
                
            if hasattr(self, 'chart_lev') and 'history_lev' in data and not data['history_lev'].empty:
                raw = data.get('raw_lev')
                title = f'Apalancamiento (N_lev) | Ratio crudo OI/Reserva: {raw:.3f}' if raw is not None else 'Apalancamiento (N_lev)'
                self.chart_lev.update_figure(self._build_chart(title, data['history_lev'], data.get('history_raw_lev'), '#ef4444', btc_hist, 'Ratio'))
                
        except Exception as e:
            print(f"Error actualizando Termómetro: {e}")

    def _on_weights_change(self, e):
        try:
            ssr_w = float(self.w_ssr_input.value)
            inf_w = float(self.w_inf_input.value)
            lev_w = float(self.w_lev_input.value)
            self.thermometer.set_weights(ssr_w, inf_w, lev_w)
            ui.timer(0.1, self._update_thermometer_ui, once=True)
        except Exception:
            pass

    def _on_mle_window_change(self, e):
        try:
            val = int(e.value)
            if val > 0:
                self.thermometer.z_score_window = val
                self.thermometer.last_update = None # Forzar recálculo inmediato
                ui.timer(0.1, self._update_thermometer_ui, once=True)
        except Exception:
            pass

    def _on_display_change(self, e):
        self.show_price = self.cb_price.value
        self.show_norm = self.cb_norm.value
        self.show_raw = self.cb_raw.value
        ui.timer(0.1, self._update_thermometer_ui, once=True)

    def _build_empty_chart(self, title):
        fig = go.Figure()
        fig.update_layout(
            template='plotly_dark',
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=40, r=40, t=40, b=30),
            title=dict(text=title, font=dict(size=14, color='white'))
        )
        return fig

    def _build_chart(self, title, series_norm, series_raw, color, btc_series=None, raw_y_title="Valor"):
        fig = go.Figure()
        
        # Convertir indice a string para evitar errores JSON
        x_vals = series_norm.index.astype(str).tolist()
        
        fig.add_trace(go.Scatter(
            x=x_vals,
            y=series_norm.tolist(),
            mode='lines',
            name='Parámetro MLE (%)',
            line=dict(color=color, width=2),
            fill='tozeroy',
            fillcolor=color.replace(')', ', 0.1)').replace('rgb', 'rgba') if 'rgb' in color else "rgba(100, 100, 100, 0.2)",
            yaxis='y1',
            visible=self.show_norm
        ))
        
        if series_raw is not None and not series_raw.empty:
            fig.add_trace(go.Scatter(
                x=x_vals,
                y=series_raw.tolist(),
                mode='lines',
                name=f'Valor Crudo',
                line=dict(color='white', width=1, dash='dash'),
                yaxis='y3',
                visible=self.show_raw
            ))
        
        if btc_series is not None and not btc_series.empty:
            x_btc = btc_series.index.astype(str).tolist()
            y_btc = btc_series.tolist()
            fig.add_trace(go.Scatter(
                x=x_btc,
                y=y_btc,
                mode='lines',
                name='Precio BTC',
                line=dict(color='rgba(255, 255, 255, 0.3)', width=1, dash='dot'),
                yaxis='y2',
                visible=self.show_price
            ))
            
        if self.show_norm:
            current_val = series_norm.tolist()[-1] if len(series_norm) > 0 else 50
            fig.add_hline(
                y=current_val, 
                line_dash="dot", 
                line_color=color, 
                line_width=1.5,
                annotation_text=f"Actual: {current_val:.1f}",
                annotation_position="bottom left",
                annotation_font_size=12,
                annotation_font_color=color,
                yref='y1'
            )
        
        has_3_axes = self.show_price and self.show_raw
        domain_end = 0.9 if has_3_axes else 1.0
        
        fig.update_layout(
            template='plotly_dark',
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=40, r=40 if not has_3_axes else 80, t=50, b=30),
            title=dict(text=title, font=dict(size=14, color='white')),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            xaxis=dict(gridcolor='rgba(255,255,255,0.1)', domain=[0, domain_end]),
            yaxis=dict(title="Normalizado (0-100)", range=[0, 100], gridcolor='rgba(255,255,255,0.1)', side='left'),
            yaxis2=dict(title="Precio BTC", showgrid=False, overlaying='y', side='right', position=domain_end if not has_3_axes else 1.0),
            yaxis3=dict(title=raw_y_title, showgrid=False, overlaying='y', side='right', position=domain_end)
        )
        
        return fig

    def render(self):
        ui.label('Termómetro de Mercado (MLE)').classes('text-3xl font-bold text-white mb-6')
        
        with ui.card().classes('bg-gray-800 text-white p-8 w-full max-w-2xl mx-auto items-center mt-10'):
            with ui.row().classes('w-full justify-between items-center mb-4'):
                ui.label('Filtro de Liquidez Estructural').classes('text-2xl font-bold text-cyan-400')
                self.mle_window_input = ui.number('Ventana (Días)', value=90, on_change=self._on_mle_window_change).classes('w-24')
            
            with ui.row().classes('w-full justify-center gap-4 mb-4'):
                self.w_ssr_input = ui.number('Peso Poder Adquisitivo (%)', value=40, on_change=self._on_weights_change).classes('w-32')
                self.w_inf_input = ui.number('Peso Velocidad (%)', value=30, on_change=self._on_weights_change).classes('w-32')
                self.w_lev_input = ui.number('Peso Apalancamiento (%)', value=30, on_change=self._on_weights_change).classes('w-32')
            
            self.mle_gauge = ui.circular_progress(value=0.5, show_value=False, size='240px').props('thickness=0.2 color="yellow" track-color="grey-9"')
            self.mle_value_label = ui.label('50.0% - Calculando...').classes('text-2xl font-bold mt-6')
            self.mle_details = ui.label('Poder Adquisitivo: - | Velocidad: - | Apalancamiento: -').classes('text-sm text-gray-400 mt-4 text-center')
            
            ui.markdown('''
            **¿Cómo funciona?**
            - **Poder Adquisitivo (40%):** Mide el Stablecoin Supply Ratio (SSR). Si hay muchas stablecoins en relación al Market Cap de BTC, hay mucha "pólvora seca".
            - **Velocidad (30%):** Mide el flujo reciente de crecimiento del mercado de stablecoins (Inflows).
            - **Apalancamiento (30%):** Compara el Open Interest de Futuros contra las reservas estables. Menos apalancamiento relativo = menos riesgo de liquidaciones en cascada.
            ''').classes('mt-8 text-slate-300 text-sm')

        with ui.column().classes('w-full max-w-6xl mx-auto mt-6'):
            with ui.row().classes('w-full justify-center gap-6 items-center p-4 bg-gray-800 rounded-lg'):
                ui.label('Opciones de Visualización:').classes('font-bold text-gray-300')
                self.cb_norm = ui.checkbox('% Termómetro', value=True, on_change=self._on_display_change)
                self.cb_price = ui.checkbox('Precio BTC', value=True, on_change=self._on_display_change)
                self.cb_raw = ui.checkbox('Valor Parámetro Crudo', value=False, on_change=self._on_display_change)
                
        with ui.column().classes('w-full max-w-6xl mx-auto gap-6 mt-6 mb-12'):
            with ui.card().classes('bg-gray-800 p-6 w-full'):
                self.chart_ssr = ui.plotly(self._build_empty_chart('Poder Adquisitivo (N_ssr)')).classes('w-full h-80')
            with ui.card().classes('bg-gray-800 p-6 w-full'):
                self.chart_inf = ui.plotly(self._build_empty_chart('Velocidad (N_inf)')).classes('w-full h-80')
            with ui.card().classes('bg-gray-800 p-6 w-full'):
                self.chart_lev = ui.plotly(self._build_empty_chart('Apalancamiento (N_lev)')).classes('w-full h-80')

        # Timer para el Termómetro de Mercado (cada 60 segundos)
        self.mle_timer = ui.timer(60.0, self._update_thermometer_ui)
        # Llamar la primera vez directamente (de forma asíncrona)
        ui.timer(1.0, self._update_thermometer_ui, once=True)
        
        ui.context.client.on_disconnect(lambda: self.mle_timer.deactivate() if self.mle_timer else None)

def render_mle_thermometer_page():
    page = MLEThermometerPage()
    page.render()
    return page
