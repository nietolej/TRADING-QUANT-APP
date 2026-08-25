from nicegui import ui, run
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from datetime import datetime
from strategy_engine.mle_thermometer import MLEThermometer
from strategy_engine.mle_optimizer import MLEOptimizer

class MLEThermometerPage:
    def __init__(self):
        self.thermometer = MLEThermometer(z_score_window=90)
        self.optimizer = MLEOptimizer(self.thermometer)
        self.mle_timer = None
        self.show_price = True
        self.show_norm = True
        self.show_raw = False
        
        # Umbrales dinámicos
        self.th_long = 70
        self.th_short = 30
        self.last_opt_data = None
        self.last_full_data = {}

    async def _update_thermometer_ui(self):
        try:
            # Ejecutamos el cálculo sincrónico en un hilo separado para no bloquear
            data = await run.io_bound(self.thermometer.get_thermometer_value)
            self.last_full_data = data
            
            if hasattr(self, 'mle_gauge'):
                theta = data.get('theta', 50.0)
                self.mle_gauge.set_value(theta / 100.0)  # progress usa 0.0 a 1.0
                
                # Cambiar color según umbrales activos
                if theta >= self.th_long:
                    color = 'green'
                    status = f'Zona Favorable LONG (>{self.th_long}%)'
                    status_class = 'text-emerald-400'
                elif theta <= self.th_short:
                    color = 'red'
                    status = f'Zona Alerta SHORT (<{self.th_short}%)'
                    status_class = 'text-red-400'
                else:
                    color = 'yellow'
                    status = 'Zona Neutral / Rango'
                    status_class = 'text-amber-400'
                    
                self.mle_gauge.props(f'color="{color}"')
                self.mle_value_label.set_text(f"{theta:.1f}%")
                self.mle_status_badge.set_text(status)
                self.mle_status_badge.classes(replace=f'text-xs font-bold px-3 py-1 rounded-full border border-slate-700 {status_class} bg-slate-900/80')
                self.mle_details.set_text(f"Poder Adq: {data.get('n_ssr', 0):.1f} | Velocidad: {data.get('n_inf', 0):.1f} | Apalanc: {data.get('n_lev', 0):.1f}")
                
            btc_hist = data.get('history_btc', pd.Series())
            theta_hist = data.get('history_theta', pd.Series())
            ssr_hist = data.get('history_ssr', pd.Series())
            raw_ssr_hist = data.get('history_raw_ssr', pd.Series())
            inf_hist = data.get('history_inf', pd.Series())
            raw_inf_hist = data.get('history_raw_inf', pd.Series())
            lev_hist = data.get('history_lev', pd.Series())
            raw_lev_hist = data.get('history_raw_lev', pd.Series())
            
            # Actualizar gráfico principal: Semáforo vs Precio BTC
            if hasattr(self, 'chart_main') and theta_hist is not None and not theta_hist.empty:
                self.chart_main.update_figure(self._build_main_mle_chart(
                    "Semáforo de Liquidez Estructural (MLE) vs Precio BTC",
                    theta_hist,
                    btc_hist,
                    theta
                ))
            
            # Actualizar sub-gráficas
            if hasattr(self, 'chart_ssr') and ssr_hist is not None and not ssr_hist.empty:
                raw = data.get('raw_ssr')
                title = f'Poder Adquisitivo (N_ssr) | Z-Score: {raw:.3f}' if raw is not None else 'Poder Adquisitivo (N_ssr)'
                self.chart_ssr.update_figure(self._build_chart(title, ssr_hist, raw_ssr_hist, '#10b981', btc_hist, 'Z-Score'))
            
            if hasattr(self, 'chart_inf') and inf_hist is not None and not inf_hist.empty:
                raw = data.get('raw_inf')
                title = f'Velocidad (N_inf) | Inflow: {raw:,.0f} USD' if raw is not None else 'Velocidad (N_inf)'
                self.chart_inf.update_figure(self._build_chart(title, inf_hist, raw_inf_hist, '#38bdf8', btc_hist, 'USD'))
                
            if hasattr(self, 'chart_lev') and lev_hist is not None and not lev_hist.empty:
                raw = data.get('raw_lev')
                title = f'Apalancamiento (N_lev) | Ratio: {raw:.3f}' if raw is not None else 'Apalancamiento (N_lev)'
                self.chart_lev.update_figure(self._build_chart(title, lev_hist, raw_lev_hist, '#ef4444', btc_hist, 'Ratio'))
                
            # ─── ACTUALIZAR TABLAS DE DATOS ───
            self._update_tables(data)

        except Exception as e:
            print(f"Error actualizando Termómetro: {e}")

    def _update_tables(self, data: dict):
        """Genera las filas tabulares ordenadas por fecha descendente."""
        try:
            theta_s = data.get('history_theta', pd.Series())
            btc_s = data.get('history_btc', pd.Series())
            ssr_s = data.get('history_ssr', pd.Series())
            raw_ssr_s = data.get('history_raw_ssr', pd.Series())
            inf_s = data.get('history_inf', pd.Series())
            raw_inf_s = data.get('history_raw_inf', pd.Series())
            lev_s = data.get('history_lev', pd.Series())
            raw_lev_s = data.get('history_raw_lev', pd.Series())

            if theta_s.empty:
                return

            df_all = pd.DataFrame(index=theta_s.index)
            df_all['theta'] = theta_s
            df_all['btc'] = btc_s
            df_all['ssr'] = ssr_s
            df_all['raw_ssr'] = raw_ssr_s
            df_all['inf'] = inf_s
            df_all['raw_inf'] = raw_inf_s
            df_all['lev'] = lev_s
            df_all['raw_lev'] = raw_lev_s
            df_all = df_all.sort_index(ascending=False)

            # 1. Tabla Principal
            if hasattr(self, 'table_main'):
                main_rows = []
                for dt, row in df_all.iterrows():
                    val_th = row['theta']
                    st = "Favorable Long" if val_th >= self.th_long else ("Alerta Short" if val_th <= self.th_short else "Neutral")
                    main_rows.append({
                        "date": str(dt)[:10],
                        "theta": f"{val_th:.1f}%" if pd.notna(val_th) else "-",
                        "status": st,
                        "btc": f"${row['btc']:,.0f}" if pd.notna(row['btc']) else "-",
                        "ssr": f"{row['ssr']:.1f}%" if pd.notna(row['ssr']) else "-",
                        "inf": f"{row['inf']:.1f}%" if pd.notna(row['inf']) else "-",
                        "lev": f"{row['lev']:.1f}%" if pd.notna(row['lev']) else "-",
                    })
                self.table_main.rows = main_rows

            # 2. Tabla SSR
            if hasattr(self, 'table_ssr'):
                ssr_rows = []
                for dt, row in df_all.iterrows():
                    ssr_rows.append({
                        "date": str(dt)[:10],
                        "ssr": f"{row['ssr']:.1f}%" if pd.notna(row['ssr']) else "-",
                        "raw_ssr": f"{row['raw_ssr']:.3f}" if pd.notna(row['raw_ssr']) else "-",
                        "btc": f"${row['btc']:,.0f}" if pd.notna(row['btc']) else "-",
                    })
                self.table_ssr.rows = ssr_rows

            # 3. Tabla Inflows
            if hasattr(self, 'table_inf'):
                inf_rows = []
                for dt, row in df_all.iterrows():
                    inf_rows.append({
                        "date": str(dt)[:10],
                        "inf": f"{row['inf']:.1f}%" if pd.notna(row['inf']) else "-",
                        "raw_inf": f"${row['raw_inf']:,.0f}" if pd.notna(row['raw_inf']) else "-",
                        "btc": f"${row['btc']:,.0f}" if pd.notna(row['btc']) else "-",
                    })
                self.table_inf.rows = inf_rows

            # 4. Tabla Apalancamiento
            if hasattr(self, 'table_lev'):
                lev_rows = []
                for dt, row in df_all.iterrows():
                    lev_rows.append({
                        "date": str(dt)[:10],
                        "lev": f"{row['lev']:.1f}%" if pd.notna(row['lev']) else "-",
                        "raw_lev": f"{row['raw_lev']:.4f}" if pd.notna(row['raw_lev']) else "-",
                        "btc": f"${row['btc']:,.0f}" if pd.notna(row['btc']) else "-",
                    })
                self.table_lev.rows = lev_rows

        except Exception as e:
            print(f"Error generando filas de tabla: {e}")

    def _export_csv(self, filename="mle_liquidez_datos.csv"):
        """Exporta todos los datos graficados a un archivo CSV descargable."""
        try:
            data = self.last_full_data
            if not data:
                ui.notify("No hay datos disponibles para exportar.", type='warning')
                return

            theta_s = data.get('history_theta', pd.Series())
            btc_s = data.get('history_btc', pd.Series())
            ssr_s = data.get('history_ssr', pd.Series())
            raw_ssr_s = data.get('history_raw_ssr', pd.Series())
            inf_s = data.get('history_inf', pd.Series())
            raw_inf_s = data.get('history_raw_inf', pd.Series())
            lev_s = data.get('history_lev', pd.Series())
            raw_lev_s = data.get('history_raw_lev', pd.Series())

            df = pd.DataFrame(index=theta_s.index)
            df['Fecha'] = df.index.astype(str)
            df['Semaforo_MLE_Pct'] = theta_s
            df['Precio_BTC_USD'] = btc_s
            df['Poder_Adquisitivo_Norm_Pct'] = ssr_s
            df['SSR_ZScore_Crudo'] = raw_ssr_s
            df['Velocidad_Inflows_Norm_Pct'] = inf_s
            df['Inflows_USD_Crudo'] = raw_inf_s
            df['Apalancamiento_Norm_Pct'] = lev_s
            df['Apalancamiento_Ratio_Crudo'] = raw_lev_s
            
            csv_str = df.to_csv(index=False)
            ui.download(csv_str.encode('utf-8'), filename)
            ui.notify(f"Archivo {filename} generado exitosamente.", type='positive')
        except Exception as e:
            ui.notify(f"Error exportando CSV: {e}", type='negative')

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

    def _apply_config(self, cfg: dict):
        """Aplica una configuración óptima a los inputs del semáforo y recálcula en vivo."""
        try:
            self.mle_window_input.value = cfg['window']
            self.w_ssr_input.value = cfg['w_ssr']
            self.w_inf_input.value = cfg['w_inf']
            self.w_lev_input.value = cfg['w_lev']
            self.th_long = cfg.get('th_long', 70)
            self.th_short = cfg.get('th_short', 30)
            
            self.thermometer.z_score_window = cfg['window']
            self.thermometer.set_weights(cfg['w_ssr'], cfg['w_inf'], cfg['w_lev'])
            self.thermometer.last_update = None
            
            ui.notify(
                f"Configuración aplicada: Ventana {cfg['window']}d | SSR {cfg['w_ssr']}% | Vel {cfg['w_inf']}% | Apal {cfg['w_lev']}% (Sharpe: {cfg['sharpe']})",
                type='positive',
                position='top'
            )
            ui.timer(0.1, self._update_thermometer_ui, once=True)
        except Exception as ex:
            ui.notify(f"Error al aplicar configuración: {ex}", type='negative')

    async def _run_optimization(self):
        """Ejecuta el proceso asíncrono de optimización y actualiza la tabla y tarjetas de resultados."""
        self.opt_spinner.set_visibility(True)
        self.opt_btn.props('loading')
        self.opt_results_container.set_visibility(False)
        
        try:
            obj = self.opt_objective_select.value
            mode = self.opt_mode_select.value
            days = int(self.opt_days_select.value)
            
            res = await run.io_bound(
                self.optimizer.run_optimization,
                windows=[30, 60, 90, 180, 365, 730],
                weight_step=10,
                objective=obj,
                mode=mode,
                eval_days=days
            )
            
            if res.get("status") == "success":
                self.last_opt_data = res
                best = res["best_config"]
                
                # Actualizar tarjeta de Mejor Configuración
                self.opt_best_title.set_text(
                    f"🏆 Mejor Configuración Encontrada: Ventana {best['window']} Días"
                )
                self.opt_best_weights.set_text(
                    f"SSR: {best['w_ssr']}% | Velocidad: {best['w_inf']}% | Apalancamiento: {best['w_lev']}%"
                )
                self.opt_best_thresholds.set_text(
                    f"Zona Favorable Long: >{best['th_long']}% | Zona Alerta Short: <{best['th_short']}%"
                )
                self.opt_best_metrics.set_text(
                    f"Sharpe: {best['sharpe']} | Retorno: +{best['total_return_pct']}% | WinRate Long: {best['win_rate_long']}% | WinRate Short: {best['win_rate_short']}% | Max DD: -{best['max_drawdown_pct']}%"
                )
                
                # Actualizar botón de aplicar mejor
                self.opt_apply_best_btn.on_click(lambda b=best: self._apply_config(b))
                
                # Actualizar diagnóstico de indicadores
                impact = res.get("indicator_impact", {})
                self.diag_ssr.set_text(f"Poder Adquisitivo (SSR): Correlación Forward {impact.get('ssr_power', 0.0):+.3f}")
                self.diag_inf.set_text(f"Velocidad Inflows: Correlación Forward {impact.get('inf_velocity', 0.0):+.3f}")
                self.diag_lev.set_text(f"Riesgo Apalancamiento: Correlación Forward {impact.get('lev_risk', 0.0):+.3f}")

                # Actualizar filas de la tabla ranking
                rows = []
                for idx, r in enumerate(res["top_results"][:10], start=1):
                    rows.append({
                        "rank": f"#{idx}",
                        "window": f"{r['window']}d",
                        "weights": f"{r['w_ssr']}% / {r['w_inf']}% / {r['w_lev']}%",
                        "thresholds": f">{r['th_long']}% / <{r['th_short']}%",
                        "sharpe": f"{r['sharpe']:.2f}",
                        "return": f"{r['total_return_pct']:+.1f}%",
                        "win_long": f"{r['win_rate_long']:.1f}%",
                        "win_short": f"{r['win_rate_short']:.1f}%",
                        "max_dd": f"-{r['max_drawdown_pct']:.1f}%",
                        "raw_data": r
                    })
                self.opt_table.rows = rows
                
                self.opt_results_container.set_visibility(True)
                ui.notify(f"Optimización completada. Se evaluaron {res['total_evaluated']} combinaciones.", type='positive')
            else:
                ui.notify(res.get("message", "Error durante la optimización."), type='negative')
        except Exception as e:
            ui.notify(f"Error ejecutando optimizador: {e}", type='negative')
        finally:
            self.opt_spinner.set_visibility(False)
            self.opt_btn.props(remove='loading')

    def _build_empty_chart(self, title):
        fig = go.Figure()
        fig.update_layout(
            template='plotly_dark',
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=40, r=40, t=40, b=30),
            title=dict(text=title, font=dict(size=14, color='#cbd5e1'))
        )
        return fig

    def _build_main_mle_chart(self, title, theta_series, btc_series, current_theta):
        """
        Construye el gráfico dual de Semáforo MLE (%) vs Precio BTC con umbrales activos.
        """
        fig = go.Figure()
        x_vals = theta_series.index.astype(str).tolist()

        # Zonas de fondo coloreadas
        fig.add_hrect(y0=self.th_long, y1=100, fillcolor="rgba(16, 185, 129, 0.08)", line_width=0, layer="below")
        fig.add_hrect(y0=self.th_short, y1=self.th_long, fillcolor="rgba(245, 158, 11, 0.04)", line_width=0, layer="below")
        fig.add_hrect(y0=0, y1=self.th_short, fillcolor="rgba(239, 68, 68, 0.08)", line_width=0, layer="below")

        # Líneas de umbral
        fig.add_hline(y=self.th_long, line_dash="dash", line_color="rgba(16, 185, 129, 0.5)", line_width=1, annotation_text=f"Favorable LONG (>{self.th_long}%)", annotation_font_size=10, annotation_font_color="#10b981")
        fig.add_hline(y=self.th_short, line_dash="dash", line_color="rgba(239, 68, 68, 0.5)", line_width=1, annotation_text=f"Alerta SHORT (<{self.th_short}%)", annotation_font_size=10, annotation_font_color="#ef4444")

        # Traza 1: Semáforo MLE
        fig.add_trace(go.Scatter(
            x=x_vals,
            y=theta_series.tolist(),
            mode='lines',
            name='Semáforo MLE (0-100%)',
            line=dict(color='#f59e0b', width=2.5),
            fill='tozeroy',
            fillcolor='rgba(245, 158, 11, 0.08)',
            yaxis='y1'
        ))

        # Traza 2: Precio de BTC
        if btc_series is not None and not btc_series.empty:
            x_btc = btc_series.index.astype(str).tolist()
            y_btc = btc_series.tolist()
            fig.add_trace(go.Scatter(
                x=x_btc,
                y=y_btc,
                mode='lines',
                name='Precio BTC (USD)',
                line=dict(color='#00e5ff', width=1.8),
                yaxis='y2'
            ))

        # Marcador del valor actual
        if len(theta_series) > 0:
            last_date = x_vals[-1]
            fig.add_trace(go.Scatter(
                x=[last_date],
                y=[current_theta],
                mode='markers+text',
                name='Actual',
                marker=dict(size=9, color='#f59e0b', line=dict(color='#ffffff', width=1.5)),
                text=[f" {current_theta:.1f}%"],
                textposition="top right",
                textfont=dict(color='#ffffff', size=11, family='JetBrains Mono'),
                showlegend=False,
                yaxis='y1'
            ))

        fig.update_layout(
            template='plotly_dark',
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=45, r=55, t=45, b=30),
            title=dict(
                text=title,
                font=dict(size=14, color='#ffffff', family='Plus Jakarta Sans')
            ),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1,
                font=dict(size=11, color='#cbd5e1')
            ),
            xaxis=dict(
                gridcolor='rgba(255,255,255,0.06)',
                showline=True,
                linecolor='#1e293b'
            ),
            yaxis=dict(
                title="Semáforo MLE (%)",
                title_font=dict(color='#f59e0b', size=11),
                tickfont=dict(color='#f59e0b', size=10),
                range=[0, 100],
                gridcolor='rgba(255,255,255,0.06)',
                side='left'
            ),
            yaxis2=dict(
                title="Precio BTC (USD)",
                title_font=dict(color='#00e5ff', size=11),
                tickfont=dict(color='#00e5ff', size=10),
                showgrid=False,
                overlaying='y',
                side='right',
                tickprefix="$",
                tickformat=",.0f"
            ),
            hovermode='x unified'
        )

        return fig

    def _build_chart(self, title, series_norm, series_raw, color, btc_series=None, raw_y_title="Valor"):
        fig = go.Figure()
        x_vals = series_norm.index.astype(str).tolist()
        
        fig.add_trace(go.Scatter(
            x=x_vals,
            y=series_norm.tolist(),
            mode='lines',
            name='Parámetro MLE (%)',
            line=dict(color=color, width=2),
            fill='tozeroy',
            fillcolor=color.replace(')', ', 0.1)').replace('rgb', 'rgba') if 'rgb' in color else "rgba(100, 100, 100, 0.15)",
            yaxis='y1',
            visible=self.show_norm
        ))
        
        if series_raw is not None and not series_raw.empty:
            fig.add_trace(go.Scatter(
                x=x_vals,
                y=series_raw.tolist(),
                mode='lines',
                name=f'Valor Crudo',
                line=dict(color='#ffffff', width=1, dash='dash'),
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
                line=dict(color='rgba(255, 255, 255, 0.35)', width=1, dash='dot'),
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
                annotation_font_size=11,
                annotation_font_color=color,
                yref='y1'
            )
        
        has_3_axes = self.show_price and self.show_raw
        domain_end = 0.9 if has_3_axes else 1.0
        
        fig.update_layout(
            template='plotly_dark',
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=40, r=40 if not has_3_axes else 80, t=45, b=30),
            title=dict(text=title, font=dict(size=13, color='#ffffff')),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10)),
            xaxis=dict(gridcolor='rgba(255,255,255,0.06)', domain=[0, domain_end]),
            yaxis=dict(title="Normalizado (0-100)", range=[0, 100], gridcolor='rgba(255,255,255,0.06)', side='left'),
            yaxis2=dict(title="Precio BTC", showgrid=False, overlaying='y', side='right', position=domain_end if not has_3_axes else 1.0, tickprefix="$", tickformat=",.0f"),
            yaxis3=dict(title=raw_y_title, showgrid=False, overlaying='y', side='right', position=domain_end)
        )
        
        return fig

    def render(self):
        with ui.row().classes('w-full justify-between items-center mb-4'):
            with ui.row().classes('items-center gap-3'):
                ui.icon('thermostat', size='1.8rem').classes('text-amber-400')
                ui.label('Filtro de Liquidez Estructural (MLE)').classes('text-2xl font-extrabold text-white tracking-tight font-heading')
            with ui.row().classes('items-center gap-2'):
                ui.button('Exportar Todo CSV', icon='download', on_click=lambda: self._export_csv()).props('dense no-caps').classes('bg-[#111827] hover:bg-[#1e293b] text-amber-400 border border-amber-500/40 text-xs font-bold px-3 py-1 rounded-lg font-mono')
                ui.label('Modelo Cuantitativo On-Chain').classes('text-xs font-semibold text-slate-400 bg-[#111827] px-3 py-1 rounded-full border border-[#1e293b]')

        # ─── MÓDULO SUPERIOR: SEMÁFORO (IZQUIERDA) + GRÁFICO INDEXADO Y TABLA (DERECHA) ───
        with ui.row().classes('w-full gap-5 items-stretch mb-6'):
            
            # COLUMNA IZQUIERDA: SEMÁFORO COMPACTO CON PARÁMETROS
            with ui.card().classes('w-full lg:w-[360px] bg-[#111827] border border-[#1e293b] rounded-xl p-5 shadow-xl flex flex-col justify-between items-center shrink-0'):
                # Fila de Título y Ventana
                with ui.row().classes('w-full justify-between items-center mb-3 pb-2 border-b border-[#1e293b]'):
                    ui.label('Semáforo Actual').classes('text-sm font-bold text-slate-300 uppercase tracking-wider')
                    with ui.row().classes('items-center gap-1.5'):
                        ui.label('Ventana:').classes('text-xs text-slate-400 font-semibold')
                        self.mle_window_input = ui.number(value=90, on_change=self._on_mle_window_change).props('dense outlined').classes('w-18 text-xs font-mono')

                # Inputs de Pesos Compactos
                with ui.row().classes('w-full justify-between gap-1 mb-4 bg-[#0a0e17] p-2 rounded-lg border border-[#1e293b]'):
                    with ui.column().classes('items-center gap-0 flex-1'):
                        ui.label('SSR (%)').classes('text-[10px] text-slate-400 font-bold uppercase')
                        self.w_ssr_input = ui.number(value=40, on_change=self._on_weights_change).props('dense outlined').classes('w-full text-xs font-mono')
                    with ui.column().classes('items-center gap-0 flex-1'):
                        ui.label('Vel (%)').classes('text-[10px] text-slate-400 font-bold uppercase')
                        self.w_inf_input = ui.number(value=30, on_change=self._on_weights_change).props('dense outlined').classes('w-full text-xs font-mono')
                    with ui.column().classes('items-center gap-0 flex-1'):
                        ui.label('Apal (%)').classes('text-[10px] text-slate-400 font-bold uppercase')
                        self.w_lev_input = ui.number(value=30, on_change=self._on_weights_change).props('dense outlined').classes('w-full text-xs font-mono')

                # Gauge Circular Reducido (160px)
                self.mle_gauge = ui.circular_progress(value=0.5, show_value=False, size='160px').props('thickness=0.18 color="yellow" track-color="grey-9"')
                
                # Valor y Estado
                self.mle_value_label = ui.label('50.0%').classes('text-3xl font-black text-white mt-3 font-mono tracking-tight')
                self.mle_status_badge = ui.label('Calculando...').classes('text-xs font-bold px-3 py-1 rounded-full border border-slate-700 text-amber-400 bg-slate-900/80 mt-1')
                self.mle_details = ui.label('Poder Adquisitivo: - | Velocidad: - | Apalancamiento: -').classes('text-[11px] text-slate-400 mt-3 text-center font-mono')

            # COLUMNA DERECHA: GRÁFICO O TABLA DE DATOS DEL SEMÁFORO
            with ui.card().classes('flex-1 bg-[#111827] border border-[#1e293b] rounded-xl p-4 shadow-xl min-w-[320px]'):
                with ui.row().classes('w-full justify-between items-center mb-2 pb-2 border-b border-[#1e293b]'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('ssid_chart', size='1.2rem').classes('text-amber-400')
                        ui.label('Histórico Semáforo MLE vs Precio BTC').classes('text-sm font-bold text-white font-heading')
                    
                    with ui.tabs().props('dense no-caps inline-label').classes('bg-[#0a0e17] text-slate-300 rounded-lg p-0.5 border border-[#1e293b]') as main_tabs:
                        t_main_chart = ui.tab('chart', label='Gráfico', icon='show_chart').classes('text-xs py-1 px-3')
                        t_main_table = ui.tab('table', label='Tabla de Datos', icon='table_chart').classes('text-xs py-1 px-3')
                
                with ui.tab_panels(main_tabs, value=t_main_chart).classes('w-full bg-transparent p-0'):
                    with ui.tab_panel(t_main_chart).classes('p-0 w-full'):
                        self.chart_main = ui.plotly(self._build_empty_chart('Cargando Histórico del Semáforo MLE...')).classes('w-full h-[370px]')
                    
                    with ui.tab_panel(t_main_table).classes('p-0 w-full'):
                        main_cols = [
                            {'name': 'date', 'label': 'Fecha', 'field': 'date', 'align': 'left'},
                            {'name': 'theta', 'label': 'Semáforo MLE (%)', 'field': 'theta', 'align': 'center'},
                            {'name': 'status', 'label': 'Zona / Señal', 'field': 'status', 'align': 'center'},
                            {'name': 'btc', 'label': 'Precio BTC', 'field': 'btc', 'align': 'right'},
                            {'name': 'ssr', 'label': 'SSR (%)', 'field': 'ssr', 'align': 'right'},
                            {'name': 'inf', 'label': 'Velocidad (%)', 'field': 'inf', 'align': 'right'},
                            {'name': 'lev', 'label': 'Apalanc. (%)', 'field': 'lev', 'align': 'right'},
                        ]
                        self.table_main = ui.table(columns=main_cols, rows=[], pagination=10).classes('w-full').props('dense flat')
                        self.table_main.add_slot('body-cell-status', '''
                            <q-td :props="props">
                                <q-badge :color="props.value.includes('Long') ? 'positive' : (props.value.includes('Short') ? 'negative' : 'warning')" :label="props.value" class="font-bold font-mono" />
                            </q-td>
                        ''')

        # ─── SECCIÓN: AUTO-AJUSTE Y OPTIMIZADOR CUANTITATIVO ───
        with ui.card().classes('w-full bg-[#111827] border border-amber-500/40 rounded-xl p-5 shadow-2xl mb-6'):
            with ui.row().classes('w-full justify-between items-center mb-4 pb-3 border-b border-[#1e293b]'):
                with ui.row().classes('items-center gap-2.5'):
                    ui.icon('auto_fix_high', size='1.6rem').classes('text-amber-400')
                    with ui.column().classes('gap-0'):
                        ui.label('Auto-Tuning Cuantitativo del Filtro MLE').classes('text-lg font-extrabold text-white font-heading')
                        ui.label('Encuentra la combinación matemática óptima de Ventana, Pesos y Umbrales para operar Long y Short').classes('text-xs text-slate-400')
                
                with ui.row().classes('items-center gap-2'):
                    self.opt_spinner = ui.spinner(size='1.5rem', color='amber')
                    self.opt_spinner.set_visibility(False)
                    self.opt_btn = ui.button('Optimizar Parámetros', icon='rocket_launch', on_click=self._run_optimization).props('no-caps').classes('bg-amber-500 hover:bg-amber-600 text-slate-950 font-bold px-4 py-1.5 rounded-lg shadow-lg font-mono')

            # Controles de Configuración del Optimizador
            with ui.row().classes('w-full gap-4 items-center mb-4 bg-[#0a0e17] p-3 rounded-lg border border-[#1e293b]'):
                with ui.column().classes('gap-1 flex-1'):
                    ui.label('Modo de Operación:').classes('text-[11px] font-bold text-slate-400 uppercase tracking-wider')
                    self.opt_mode_select = ui.select(
                        options={
                            'long_short': '🟢 / 🔴 Long y Short (Estrategia Completa)',
                            'long_only': '🟢 Solo Long (Compras en Liquidez Alta)',
                            'short_only': '🔴 Solo Short (Cobertura en Estrés)'
                        },
                        value='long_short'
                    ).props('dense outlined').classes('w-full text-xs')

                with ui.column().classes('gap-1 flex-1'):
                    ui.label('Función Objetivo:').classes('text-[11px] font-bold text-slate-400 uppercase tracking-wider')
                    self.opt_objective_select = ui.select(
                        options={
                            'sharpe': 'Sharpe Ratio (Riesgo / Rendimiento)',
                            'return': 'Retorno Acumulado Total (%)',
                            'win_rate': 'Tasa de Acierto (Win Rate %)',
                            'correlation': 'Capacidad Predictiva Forward'
                        },
                        value='sharpe'
                    ).props('dense outlined').classes('w-full text-xs')

                with ui.column().classes('gap-1 flex-1'):
                    ui.label('Histórico a Evaluar:').classes('text-[11px] font-bold text-slate-400 uppercase tracking-wider')
                    self.opt_days_select = ui.select(
                        options={
                            '365': 'Último 1 Año (365 días)',
                            '730': 'Últimos 2 Años (730 días)',
                            '0': 'Todo el Histórico Disponible'
                        },
                        value='730'
                    ).props('dense outlined').classes('w-full text-xs')

            # Contenedor de Resultados del Optimizador
            self.opt_results_container = ui.column().classes('w-full gap-4')
            self.opt_results_container.set_visibility(False)
            
            with self.opt_results_container:
                # Tarjeta de la Mejor Configuración
                with ui.card().classes('w-full bg-gradient-to-r from-amber-500/10 via-[#111827] to-emerald-500/10 border border-amber-500/50 p-4 rounded-xl shadow-lg'):
                    with ui.row().classes('w-full justify-between items-center'):
                        with ui.column().classes('gap-1'):
                            self.opt_best_title = ui.label('🏆 Configuración Óptima Encontrada').classes('text-base font-extrabold text-amber-400 font-heading')
                            self.opt_best_weights = ui.label('SSR: - | Vel: - | Apal: -').classes('text-sm font-semibold text-white font-mono')
                            self.opt_best_thresholds = ui.label('Long > - | Short < -').classes('text-xs text-slate-300 font-mono')
                            self.opt_best_metrics = ui.label('Sharpe: - | Retorno: - | WinRate: -').classes('text-xs font-bold text-emerald-400 font-mono mt-1')
                        
                        self.opt_apply_best_btn = ui.button('⚡ Aplicar al Semáforo', icon='check_circle').props('no-caps').classes('bg-emerald-500 hover:bg-emerald-600 text-slate-950 font-bold px-4 py-2 rounded-lg shadow-md font-mono')

                # Diagnóstico de Poder de Cada Indicador
                with ui.row().classes('w-full gap-3 items-center bg-[#0a0e17] p-3 rounded-lg border border-[#1e293b]'):
                    ui.label('Sensibilidad de Componentes:').classes('text-xs font-bold text-slate-400 uppercase')
                    self.diag_ssr = ui.label('SSR: -').classes('text-xs font-mono text-emerald-400 bg-[#111827] px-2.5 py-1 rounded border border-[#1e293b]')
                    self.diag_inf = ui.label('Inflows: -').classes('text-xs font-mono text-cyan-400 bg-[#111827] px-2.5 py-1 rounded border border-[#1e293b]')
                    self.diag_lev = ui.label('Apalancamiento: -').classes('text-xs font-mono text-red-400 bg-[#111827] px-2.5 py-1 rounded border border-[#1e293b]')

                # Tabla con el Ranking de Mejores Configuraciones
                ui.label('Top 10 Configuraciones Evaluadas:').classes('text-sm font-bold text-slate-300 mt-2')
                columns = [
                    {'name': 'rank', 'label': '#', 'field': 'rank', 'align': 'left'},
                    {'name': 'window', 'label': 'Ventana', 'field': 'window', 'align': 'center'},
                    {'name': 'weights', 'label': 'Pesos (SSR / Vel / Apal)', 'field': 'weights', 'align': 'center'},
                    {'name': 'thresholds', 'label': 'Umbrales (Long / Short)', 'field': 'thresholds', 'align': 'center'},
                    {'name': 'sharpe', 'label': 'Sharpe Ratio', 'field': 'sharpe', 'align': 'right'},
                    {'name': 'return', 'label': 'Retorno Total', 'field': 'return', 'align': 'right'},
                    {'name': 'win_long', 'label': 'Win Rate Long', 'field': 'win_long', 'align': 'right'},
                    {'name': 'win_short', 'label': 'Win Rate Short', 'field': 'win_short', 'align': 'right'},
                    {'name': 'max_dd', 'label': 'Max DD', 'field': 'max_dd', 'align': 'right'},
                    {'name': 'action', 'label': 'Acción', 'field': 'action', 'align': 'center'},
                ]
                
                self.opt_table = ui.table(columns=columns, rows=[], row_key='rank').classes('w-full').props('dense flat')
                
                # Slot personalizado para botón de acción en cada fila
                self.opt_table.add_slot('body-cell-rank', '''
                    <q-td :props="props">
                        <q-badge color="amber-9" text-color="black" :label="props.value" class="font-bold font-mono" />
                    </q-td>
                ''')
                self.opt_table.add_slot('body-cell-action', '''
                    <q-td :props="props">
                        <q-btn size="xs" color="amber" text-color="black" label="Usar" dense no-caps class="q-px-sm font-bold font-mono" @click="() => $parent.$emit('apply_row', props.row)" />
                    </q-td>
                ''')
                self.opt_table.on('apply_row', lambda msg: self._apply_config(msg.args.get('raw_data', {})))

        # ─── CONTROLES DE VISUALIZACIÓN INFERIORES ───
        with ui.row().classes('w-full justify-between items-center p-3.5 bg-[#111827] rounded-xl border border-[#1e293b] mb-6 shadow-md'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('tune', size='1.2rem').classes('text-amber-400')
                ui.label('Desglose de Parámetros Individuales').classes('font-bold text-sm text-slate-200 uppercase tracking-wider')
            with ui.row().classes('gap-5 items-center'):
                self.cb_norm = ui.checkbox('% Normalizado', value=True, on_change=self._on_display_change).classes('text-xs text-slate-300 font-semibold')
                self.cb_price = ui.checkbox('Precio BTC', value=True, on_change=self._on_display_change).classes('text-xs text-slate-300 font-semibold')
                self.cb_raw = ui.checkbox('Valor Crudo', value=False, on_change=self._on_display_change).classes('text-xs text-slate-300 font-semibold')

        # ─── GRÁFICAS Y TABLAS DE COMPONENTES INDIVIDUALES ───
        with ui.column().classes('w-full gap-5 mb-8'):
            
            # 1. PODER ADQUISITIVO (N_SSR)
            with ui.card().classes('bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-lg w-full'):
                with ui.row().classes('w-full justify-between items-center mb-2 pb-2 border-b border-[#1e293b]'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('account_balance_wallet', size='1.2rem').classes('text-emerald-400')
                        ui.label('Poder Adquisitivo (N_ssr) - Stablecoin Supply Ratio').classes('text-sm font-bold text-white font-heading')
                    
                    with ui.tabs().props('dense no-caps inline-label').classes('bg-[#0a0e17] text-slate-300 rounded-lg p-0.5 border border-[#1e293b]') as ssr_tabs:
                        t_ssr_chart = ui.tab('chart', label='Gráfico', icon='show_chart').classes('text-xs py-1 px-3')
                        t_ssr_table = ui.tab('table', label='Tabla de Datos', icon='table_chart').classes('text-xs py-1 px-3')
                
                with ui.tab_panels(ssr_tabs, value=t_ssr_chart).classes('w-full bg-transparent p-0'):
                    with ui.tab_panel(t_ssr_chart).classes('p-0 w-full'):
                        self.chart_ssr = ui.plotly(self._build_empty_chart('Poder Adquisitivo (N_ssr)')).classes('w-full h-72')
                    with ui.tab_panel(t_ssr_table).classes('p-0 w-full'):
                        ssr_cols = [
                            {'name': 'date', 'label': 'Fecha', 'field': 'date', 'align': 'left'},
                            {'name': 'ssr', 'label': 'Poder Adquisitivo (%)', 'field': 'ssr', 'align': 'center'},
                            {'name': 'raw_ssr', 'label': 'Z-Score SSR (Crudo)', 'field': 'raw_ssr', 'align': 'right'},
                            {'name': 'btc', 'label': 'Precio BTC', 'field': 'btc', 'align': 'right'},
                        ]
                        self.table_ssr = ui.table(columns=ssr_cols, rows=[], pagination=8).classes('w-full').props('dense flat')

            # 2. VELOCIDAD DE INFLOWS (N_INF)
            with ui.card().classes('bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-lg w-full'):
                with ui.row().classes('w-full justify-between items-center mb-2 pb-2 border-b border-[#1e293b]'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('speed', size='1.2rem').classes('text-cyan-400')
                        ui.label('Velocidad de Capital (N_inf) - Delta Inflows Stablecoins').classes('text-sm font-bold text-white font-heading')
                    
                    with ui.tabs().props('dense no-caps inline-label').classes('bg-[#0a0e17] text-slate-300 rounded-lg p-0.5 border border-[#1e293b]') as inf_tabs:
                        t_inf_chart = ui.tab('chart', label='Gráfico', icon='show_chart').classes('text-xs py-1 px-3')
                        t_inf_table = ui.tab('table', label='Tabla de Datos', icon='table_chart').classes('text-xs py-1 px-3')
                
                with ui.tab_panels(inf_tabs, value=t_inf_chart).classes('w-full bg-transparent p-0'):
                    with ui.tab_panel(t_inf_chart).classes('p-0 w-full'):
                        self.chart_inf = ui.plotly(self._build_empty_chart('Velocidad (N_inf)')).classes('w-full h-72')
                    with ui.tab_panel(t_inf_table).classes('p-0 w-full'):
                        inf_cols = [
                            {'name': 'date', 'label': 'Fecha', 'field': 'date', 'align': 'left'},
                            {'name': 'inf', 'label': 'Velocidad (%)', 'field': 'inf', 'align': 'center'},
                            {'name': 'raw_inf', 'label': 'Inflow USD (Crudo)', 'field': 'raw_inf', 'align': 'right'},
                            {'name': 'btc', 'label': 'Precio BTC', 'field': 'btc', 'align': 'right'},
                        ]
                        self.table_inf = ui.table(columns=inf_cols, rows=[], pagination=8).classes('w-full').props('dense flat')

            # 3. APALANCAMIENTO (N_LEV)
            with ui.card().classes('bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-lg w-full'):
                with ui.row().classes('w-full justify-between items-center mb-2 pb-2 border-b border-[#1e293b]'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('shield', size='1.2rem').classes('text-red-400')
                        ui.label('Apalancamiento y Riesgo de Cascada (N_lev) - Open Interest / Reservas').classes('text-sm font-bold text-white font-heading')
                    
                    with ui.tabs().props('dense no-caps inline-label').classes('bg-[#0a0e17] text-slate-300 rounded-lg p-0.5 border border-[#1e293b]') as lev_tabs:
                        t_lev_chart = ui.tab('chart', label='Gráfico', icon='show_chart').classes('text-xs py-1 px-3')
                        t_lev_table = ui.tab('table', label='Tabla de Datos', icon='table_chart').classes('text-xs py-1 px-3')
                
                with ui.tab_panels(lev_tabs, value=t_lev_chart).classes('w-full bg-transparent p-0'):
                    with ui.tab_panel(t_lev_chart).classes('p-0 w-full'):
                        self.chart_lev = ui.plotly(self._build_empty_chart('Apalancamiento (N_lev)')).classes('w-full h-72')
                    with ui.tab_panel(t_lev_table).classes('p-0 w-full'):
                        lev_cols = [
                            {'name': 'date', 'label': 'Fecha', 'field': 'date', 'align': 'left'},
                            {'name': 'lev', 'label': 'Apalancamiento (%)', 'field': 'lev', 'align': 'center'},
                            {'name': 'raw_lev', 'label': 'Ratio OI/Reserva (Crudo)', 'field': 'raw_lev', 'align': 'right'},
                            {'name': 'btc', 'label': 'Precio BTC', 'field': 'btc', 'align': 'right'},
                        ]
                        self.table_lev = ui.table(columns=lev_cols, rows=[], pagination=8).classes('w-full').props('dense flat')

        # ─── GUÍA EXPLICATIVA ───
        with ui.card().classes('w-full bg-[#111827] border border-[#1e293b] p-5 rounded-xl mb-10'):
            ui.label('¿Cómo funciona la Ecuación del Filtro MLE?').classes('text-base font-bold text-amber-400 mb-2 font-heading')
            ui.markdown('''
            - **Poder Adquisitivo (SSR):** Mide el *Stablecoin Supply Ratio*. Si hay abundantes reservas de stablecoins respecto al Market Cap de BTC, existe mayor capital líquido disponible para impulsar compras.
            - **Velocidad de Inflow:** Mide la tasa de emisión y flujo de entrada neto de stablecoins al mercado cripto.
            - **Riesgo de Apalancamiento:** Compara el *Open Interest* de futuros contra las reservas estables. Menor apalancamiento relativo reduce la probabilidad de liquidaciones en cascada.
            - **Auto-Ajuste Cuantitativo:** Encuentra mediante búsqueda de hiperparámetros los pesos exactos y umbrales con mayor correlación y rendimiento histórico para optimizar entradas en Long y coberturas en Short.
            ''').classes('text-slate-300 text-xs leading-relaxed')

        # Timer para el Termómetro de Mercado (cada 60 segundos)
        self.mle_timer = ui.timer(60.0, self._update_thermometer_ui)
        # Llamar la primera vez directamente (de forma asíncrona)
        ui.timer(1.0, self._update_thermometer_ui, once=True)
        
        ui.context.client.on_disconnect(lambda: self.mle_timer.deactivate() if self.mle_timer else None)

def render_mle_thermometer_page():
    page = MLEThermometerPage()
    page.render()
    return page
