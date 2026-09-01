"""
Página de Análisis Cuantitativo de los Ciclos de Halving de Bitcoin.
Permite comparar las trayectorias de precios indexadas por días relativos (H=0, H+1, H+2...),
evaluar correlaciones estadísticas entre ciclos, medir rendimientos por horizontes
y analizar modelos de rendimientos decrecientes y proyecciones.
"""

import asyncio
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timezone
from nicegui import ui, background_tasks
from data_layer.halving_analyzer import BTCHalvingAnalyzer, HALVING_EVENTS
from data_layer.stablecoin_backtester import StablecoinBacktester

# Singleton o instancia global para caché rápida
_analyzer_instance = None

def get_analyzer() -> BTCHalvingAnalyzer:
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = BTCHalvingAnalyzer()
    return _analyzer_instance


def render_halving_analyzer():
    """
    Renderiza la vista interactiva del Módulo de Análisis de Halvings.
    """
    analyzer = get_analyzer()

    # Estado de la vista
    state = {
        "scale_mode": "multiplier",      # 'multiplier', 'percentage', 'usd'
        "y_axis_type": "log",            # 'log', 'linear'
        "time_window": "post_800",       # 'full_1000', 'post_800', 'year_1', 'extended'
        "selected_cycles": ["H1", "H2", "H3", "H4", "bench"],
        "is_loading": False
    }

    # Contenedor raíz
    with ui.column().classes('w-full h-full p-3 md:p-6 gap-6'):

        # --- HEADER PRINCIPAL ---
        with ui.row().classes('w-full items-center justify-between flex-wrap gap-4 border-b border-[#1e293b] pb-4'):
            with ui.column().classes('gap-1'):
                with ui.row().classes('items-center gap-2'):
                    with ui.row().classes('items-center justify-center w-8 h-8 rounded-lg bg-amber-500/20 border border-amber-500/40 text-amber-400'):
                        ui.icon('timelapse', size='1.4rem')
                    ui.label('ANÁLISIS DE CICLOS DE HALVING BTC').classes('text-lg md:text-xl font-extrabold text-white tracking-tight font-heading')
                    ui.badge('MOTOR CUANTITATIVO', color='amber').classes('text-[10px] font-mono font-bold tracking-wider')
                ui.label('Comparación normalizada de ciclos de Bitcoin (H=0, H+1, H+2...), correlación inter-ciclos, horizontes de retorno y modelo de decaimiento.').classes('text-xs text-slate-400 font-normal')

            with ui.row().classes('items-center gap-3'):
                loading_spinner = ui.spinner('dots', size='md', color='amber')
                loading_spinner.set_visibility(False)
                
                refresh_btn = ui.button('Sincronizar Datos', icon='refresh').props('flat outline text-color=amber-400 size=sm').classes('border border-amber-500/40 rounded-lg text-xs font-semibold px-3 py-1.5 hover:bg-amber-500/10 transition-all')

        # --- KPI CARDS SUPERIORES ---
        kpi_container = ui.row().classes('w-full grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4')

        # --- CONTROLES DE GRÁFICO ---
        with ui.row().classes('w-full items-center justify-between flex-wrap gap-3 bg-[#111827] p-3 rounded-xl border border-[#1e293b] shadow-md'):
            with ui.row().classes('items-center flex-wrap gap-3'):
                ui.label('MÉTRICA:').classes('text-[11px] font-bold text-slate-400 font-mono self-center')
                scale_select = ui.select(
                    options={
                        'multiplier': 'Múltiplo Normalizado (1.0x Base)',
                        'percentage': 'Retorno Porcentual (+%)',
                        'usd': 'Precio Absoluto ($ USD)'
                    },
                    value=state['scale_mode']
                ).props('dense outlined dark options-dense').classes('w-60 text-xs')

                ui.label('ESCALA Y:').classes('text-[11px] font-bold text-slate-400 font-mono self-center ml-2')
                y_axis_select = ui.select(
                    options={
                        'log': 'Logarítmica (Recomendada)',
                        'linear': 'Lineal'
                    },
                    value=state['y_axis_type']
                ).props('dense outlined dark options-dense').classes('w-48 text-xs')

                ui.label('VENTANA:').classes('text-[11px] font-bold text-slate-400 font-mono self-center ml-2')
                window_select = ui.select(
                    options={
                        'post_800': 'Post-Halving (0 a +800 días)',
                        'year_1': 'Primer Año Post-Halving (0 a +365 días)',
                        'full_1000': 'Pre + Post (-180 a +1000 días)',
                        'extended': 'Ciclo Extendido (-365 a +1400 días)'
                    },
                    value=state['time_window']
                ).props('dense outlined dark options-dense').classes('w-64 text-xs')

            with ui.row().classes('items-center gap-2 flex-wrap'):
                ui.label('CICLOS:').classes('text-[11px] font-bold text-slate-400 font-mono self-center')
                chk_h1 = ui.checkbox('H1 (2012)', value=True).props('dense dark color=cyan').classes('text-xs text-sky-400 font-semibold')
                chk_h2 = ui.checkbox('H2 (2016)', value=True).props('dense dark color=purple').classes('text-xs text-purple-400 font-semibold')
                chk_h3 = ui.checkbox('H3 (2020)', value=True).props('dense dark color=green').classes('text-xs text-emerald-400 font-semibold')
                chk_h4 = ui.checkbox('H4 (2024 Actual)', value=True).props('dense dark color=amber').classes('text-xs text-amber-400 font-bold')
                chk_bench = ui.checkbox('Promedio Histórico', value=True).props('dense dark color=grey').classes('text-xs text-slate-300 font-medium')

        # --- CONTENEDOR DEL GRÁFICO PRINCIPAL ---
        chart_container = ui.column().classes('w-full bg-[#111827] p-4 rounded-xl border border-[#1e293b] shadow-lg')

        # --- PESTAÑAS DE ANÁLISIS CUANTITATIVO ---
        with ui.tabs().classes('w-full text-slate-300 border-b border-[#1e293b]') as tabs:
            tab_growth = ui.tab('growth', label='Crecimiento / Decrecimiento por Temporalidad', icon='trending_up')
            tab_stables = ui.tab('stables', label='Capitalización de Stablecoins por Halving', icon='account_balance_wallet')
            tab_backtest = ui.tab('backtest', label='Backtesting Cuantitativo (Stablecoins + EMA)', icon='psychology')
            tab_horizons = ui.tab('horizons', label='Rendimientos por Horizonte', icon='bar_chart')
            tab_corr = ui.tab('correlation', label='Matriz de Correlación y Similitud', icon='grid_view')
            tab_decay = ui.tab('decay', label='Decaimiento y Proyecciones', icon='auto_graph')
            tab_table = ui.tab('table', label='Tabla Cuantitativa Detallada', icon='table_chart')

        with ui.tab_panels(tabs, value='growth').classes('w-full bg-transparent p-0'):
            with ui.tab_panel('growth'):
                growth_container = ui.column().classes('w-full')

            with ui.tab_panel('stables'):
                stables_container = ui.column().classes('w-full')

            with ui.tab_panel('backtest'):
                backtest_container = ui.column().classes('w-full')

            with ui.tab_panel('horizons'):
                horizons_container = ui.column().classes('w-full')

            with ui.tab_panel('correlation'):
                corr_container = ui.column().classes('w-full')

            with ui.tab_panel('decay'):
                decay_container = ui.column().classes('w-full')

            with ui.tab_panel('table'):
                table_container = ui.column().classes('w-full')

        # -------------------------------------------------------------
        # FUNCIONES DE RENDERIZADO REACTIVO
        # -------------------------------------------------------------

        def render_kpi_cards():
            kpi_container.clear()
            metrics = analyzer.calculate_cycle_metrics()
            h4_meta = next((m for m in metrics if m["id"] == "H4"), None)
            corrs = analyzer.calculate_correlation_matrix()
            decay_model = analyzer.calculate_diminishing_returns_model()

            with kpi_container:
                # Card 1: Ciclo 4 (Actual)
                with ui.card().classes('bg-[#0f172a] border border-amber-500/30 p-4 rounded-xl shadow-md'):
                    with ui.row().classes('w-full justify-between items-center mb-1'):
                        ui.label('CICLO 4 (ACTUAL)').classes('text-[11px] font-bold text-amber-400 tracking-wider font-mono')
                        ui.icon('bolt', size='1.2rem').classes('text-amber-400')
                    if h4_meta:
                        curr_day = h4_meta['current_days']
                        curr_p = h4_meta['current_price']
                        curr_ret = h4_meta['current_return_pct']
                        curr_mult = h4_meta['current_multiplier']
                        color_ret = 'text-emerald-400' if curr_ret >= 0 else 'text-rose-400'
                        ui.label(f'Día H+{curr_day}').classes('text-2xl font-black text-white font-mono')
                        with ui.row().classes('items-baseline gap-2 mt-1'):
                            ui.label(f'${curr_p:,.2f}').classes('text-sm font-bold text-slate-200 font-mono')
                            ui.label(f'{curr_mult:.2f}x ({curr_ret:+.1f}%)').classes(f'text-xs font-black {color_ret} font-mono')
                        ui.label(f"Precio Halving: ${h4_meta['halving_price']:,.2f}").classes('text-[10px] text-slate-400 font-mono mt-1')

                # Card 2: Promedio Histórico a Pico
                completed_metrics = [m for m in metrics if m["is_completed"]]
                avg_mult = np.mean([m["peak_multiplier"] for m in completed_metrics]) if completed_metrics else 0
                avg_days = int(np.mean([m["days_to_peak"] for m in completed_metrics])) if completed_metrics else 0
                with ui.card().classes('bg-[#0f172a] border border-[#1e293b] p-4 rounded-xl shadow-md'):
                    with ui.row().classes('w-full justify-between items-center mb-1'):
                        ui.label('PICO PROMEDIO HISTÓRICO').classes('text-[11px] font-bold text-sky-400 tracking-wider font-mono')
                        ui.icon('trending_up', size='1.2rem').classes('text-sky-400')
                    ui.label(f'{avg_mult:.1f}x').classes('text-2xl font-black text-white font-mono')
                    with ui.row().classes('items-baseline gap-2 mt-1'):
                        ui.label(f'Promedio: +{((avg_mult-1)*100):,.0f}%').classes('text-xs font-bold text-slate-300 font-mono')
                    ui.label(f'Días hasta el pico: ~{avg_days} días').classes('text-[10px] text-slate-400 font-mono mt-1')

                # Card 3: Similitud y Correlación Ciclo Actual
                sim_scores = corrs.get('similarity_scores', {})
                best_match = max(sim_scores.items(), key=lambda x: x[1]['correlation']) if sim_scores else ("N/A", {})
                match_name = f"Halving {best_match[0][1]}" if best_match[0] != "N/A" else "N/A"
                match_corr = best_match[1].get('correlation', 0.0) if best_match[0] != "N/A" else 0.0
                match_pct = best_match[1].get('similarity_pct', 0.0) if best_match[0] != "N/A" else 0.0
                with ui.card().classes('bg-[#0f172a] border border-[#1e293b] p-4 rounded-xl shadow-md'):
                    with ui.row().classes('w-full justify-between items-center mb-1'):
                        ui.label('MAYOR CORRELACIÓN').classes('text-[11px] font-bold text-purple-400 tracking-wider font-mono')
                        ui.icon('insights', size='1.2rem').classes('text-purple-400')
                    ui.label(f'{match_name}').classes('text-2xl font-black text-white font-mono')
                    with ui.row().classes('items-baseline gap-2 mt-1'):
                        ui.label(f'Coef. Pearson: r = {match_corr:.3f}').classes('text-xs font-bold text-purple-300 font-mono')
                    ui.label(f'Similitud direccional: {match_pct:.1f}%').classes('text-[10px] text-slate-400 font-mono mt-1')

                # Card 4: Proyección Modelo de Rendimientos Decrecientes
                scenarios = decay_model.get('scenarios', {})
                base_target = scenarios.get('base_model', {}).get('target_price', 0.0)
                base_mult = scenarios.get('base_model', {}).get('multiplier', 0.0)
                peak_window = decay_model.get('estimated_peak_window', {}).get('avg_date', 'Q3-Q4')
                with ui.card().classes('bg-[#0f172a] border border-[#1e293b] p-4 rounded-xl shadow-md'):
                    with ui.row().classes('w-full justify-between items-center mb-1'):
                        ui.label('TARGET MODELO BASE').classes('text-[11px] font-bold text-emerald-400 tracking-wider font-mono')
                        ui.icon('flag', size='1.2rem').classes('text-emerald-400')
                    ui.label(f'${base_target:,.0f}').classes('text-2xl font-black text-emerald-400 font-mono')
                    with ui.row().classes('items-baseline gap-2 mt-1'):
                        ui.label(f'Multiplicador: {base_mult:.2f}x').classes('text-xs font-bold text-slate-300 font-mono')
                    ui.label(f'Ventana estimada pico: {peak_window}').classes('text-[10px] text-slate-400 font-mono mt-1')

        def build_main_figure() -> go.Figure:
            # Configurar ventana
            win = state['time_window']
            if win == 'post_800':
                pre_d, post_d = 0, 800
            elif win == 'year_1':
                pre_d, post_d = 0, 365
            elif win == 'full_1000':
                pre_d, post_d = 180, 1000
            else:  # extended
                pre_d, post_d = 365, 1400

            series_dict = analyzer.get_halving_series(pre_days=pre_d, post_days=post_d)
            scale_mode = state['scale_mode']
            y_type = state['y_axis_type']
            selected_cycles = state['selected_cycles']

            fig = go.Figure()

            # Configuración de Y y hover según el modo de escala
            y_col = 'multiplier'
            y_title = 'Múltiplo de Precio (Base = 1.0x en Halving)'
            hover_template_val = "%{y:.2f}x"
            if scale_mode == 'percentage':
                y_col = 'pct_return'
                y_title = 'Rendimiento Acumulado (%)'
                hover_template_val = "%{y:+.2f}%"
            elif scale_mode == 'usd':
                y_col = 'close'
                y_title = 'Precio BTC (USD)'
                hover_template_val = "$%{y:,.2f}"

            # Curvas individuales por Halving
            for h_id in ["H1", "H2", "H3", "H4"]:
                if h_id not in selected_cycles or h_id not in series_dict:
                    continue

                df_c = series_dict[h_id]
                h_name = df_c.attrs['name']
                h_color = df_c.attrs['color']
                h_price = df_c.attrs['halving_price']
                is_h4 = (h_id == "H4")

                line_width = 3.5 if is_h4 else 2.0
                line_dash = 'solid'

                # Formato conciso y legible para la leyenda
                short_year = h_name.split('(')[1].split(')')[0] if '(' in h_name else ''
                legend_label = f"<b>{h_id} ({short_year})</b> · ${h_price:,.0f}"

                fig.add_trace(go.Scatter(
                    x=df_c['rel_day'],
                    y=df_c[y_col],
                    mode='lines',
                    name=legend_label,
                    line=dict(color=h_color, width=line_width, dash=line_dash),
                    hovertemplate=(
                        f"<b>{h_name}</b><br>"
                        + "Día: <b>H%{x:+}</b><br>"
                        + f"Valor: <b>{hover_template_val}</b><br>"
                        + "Precio Real: $%{customdata[0]:,.2f}<br>"
                        + "Fecha: %{customdata[1]}<extra></extra>"
                    ),
                    customdata=np.stack((df_c['close'], df_c['timestamp'].dt.strftime('%Y-%m-%d')), axis=-1)
                ))

                # Si es Halving 4, agregar un punto brillante en el último día
                if is_h4 and not df_c.empty:
                    last_row = df_c.iloc[-1]
                    fig.add_trace(go.Scatter(
                        x=[last_row['rel_day']],
                        y=[last_row[y_col]],
                        mode='markers+text',
                        name='H4 Posición Actual',
                        marker=dict(size=10, color='#f59e0b', symbol='diamond', line=dict(color='#ffffff', width=2)),
                        text=[f"H+{last_row['rel_day']}"],
                        textposition='top right',
                        textfont=dict(color='#fbbf24', size=11, family='JetBrains Mono'),
                        showlegend=False,
                        hoverinfo='skip'
                    ))

            # Curva Promedio Histórica (Benchmark de H1, H2, H3)
            if 'bench' in selected_cycles and scale_mode != 'usd':
                bench_df = analyzer.get_benchmark_trajectory(series_dict, max_days=post_d)
                if not bench_df.empty:
                    bench_y_col = 'multiplier_mean' if scale_mode == 'multiplier' else 'pct_return_mean'
                    bench_med_col = 'multiplier_median' if scale_mode == 'multiplier' else 'pct_return_median'

                    # Banda Min/Max
                    if scale_mode == 'multiplier':
                        fig.add_trace(go.Scatter(
                            x=bench_df['rel_day'],
                            y=bench_df['multiplier_max'],
                            mode='lines',
                            line=dict(width=0),
                            showlegend=False,
                            hoverinfo='skip'
                        ))
                        fig.add_trace(go.Scatter(
                            x=bench_df['rel_day'],
                            y=bench_df['multiplier_min'],
                            mode='lines',
                            line=dict(width=0),
                            fill='tonexty',
                            fillcolor='rgba(148, 163, 184, 0.08)',
                            name='Rango Histórico (Min-Max)',
                            hoverinfo='skip'
                        ))

                    # Línea Promedio
                    fig.add_trace(go.Scatter(
                        x=bench_df['rel_day'],
                        y=bench_df[bench_y_col],
                        mode='lines',
                        name='<b>Promedio Histórico (H1-H3)</b>',
                        line=dict(color='#94a3b8', width=2.0, dash='dash'),
                        hovertemplate="<b>Promedio Histórico</b><br>Día: H%{x:+}<br>Promedio: <b>" + hover_template_val + "</b><extra></extra>"
                    ))

            # Línea vertical en H=0 (Día del Halving)
            fig.add_vline(
                x=0,
                line_width=1.5,
                line_dash='dash',
                line_color='#f59e0b',
                annotation_text="<b>DÍA DEL HALVING (H=0)</b>",
                annotation_position="top left",
                annotation_font=dict(color='#fbbf24', size=10, family='JetBrains Mono')
            )

            # Si es modo multiplicador, línea de referencia en 1.0x
            if scale_mode == 'multiplier':
                fig.add_hline(
                    y=1.0,
                    line_width=1.0,
                    line_dash='dot',
                    line_color='#475569',
                    annotation_text="Base 1.0x",
                    annotation_position="bottom right",
                    annotation_font=dict(color='#64748b', size=9)
                )

            # Layout Bloomberg Obsidian Dark con Leyenda Inferior Centrada y Espaciosa
            fig.update_layout(
                title=dict(
                    text=f"<b>Trayectoria Comparativa de Ciclos de Halving BTC</b> — <span style='color:#f59e0b'>{y_title}</span>",
                    font=dict(color='#ffffff', size=13, family='Space Grotesk, sans-serif'),
                    x=0.01,
                    y=0.98
                ),
                paper_bgcolor='#111827',
                plot_bgcolor='#0a0e17',
                font=dict(family='Plus Jakarta Sans, sans-serif', color='#94a3b8', size=11),
                height=560,
                margin=dict(l=55, r=25, t=55, b=85),
                hovermode='x unified',
                legend=dict(
                    orientation='h',
                    yanchor='top',
                    y=-0.16,
                    xanchor='center',
                    x=0.5,
                    bgcolor='rgba(15, 23, 42, 0.95)',
                    bordercolor='#1e293b',
                    borderwidth=1,
                    font=dict(size=11, color='#e2e8f0', family='JetBrains Mono')
                ),
                xaxis=dict(
                    title=dict(text='Días Relativos al Halving (H=0: Día del Halving | H+1, H+2...)', font=dict(color='#94a3b8', size=11)),
                    gridcolor='#1e293b',
                    zerolinecolor='#334155',
                    tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10),
                    ticksuffix=' d',
                    showgrid=True
                ),
                yaxis=dict(
                    title=dict(text=y_title, font=dict(color='#94a3b8', size=11)),
                    type='log' if y_type == 'log' else 'linear',
                    gridcolor='#1e293b',
                    zerolinecolor='#334155',
                    tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10),
                    showgrid=True
                )
            )

            return fig

        def render_main_chart():
            chart_container.clear()
            with chart_container:
                fig = build_main_figure()
                ui.plotly(fig).classes('w-full h-full')

        # -------------------------------------------------------------
        # PESTAÑA 1: CRECIMIENTO Y DECRECIMIENTO POR TEMPORALIDAD
        # -------------------------------------------------------------
        # -------------------------------------------------------------
        # PESTAÑA 1: CRECIMIENTO Y DECRECIMIENTO POR TEMPORALIDAD
        # -------------------------------------------------------------
        growth_state = {
            "asset_type": "btc",          # 'btc', 'stablecoins'
            "timeframe": "semester",      # 'day', 'week', 'month', 'quarter', 'semester', 'year'
            "step_size": 1,               # 1, 2, 3, 4, 6, 12...
            "metric": "periodic_delta",   # 'periodic_delta', 'cumulative_pct', 'cumulative_mult', 'dual_view', 'heatmap'
            "max_days": 1080,
            "selected_cycles": ["H1", "H2", "H3", "H4", "bench"],
            "y_scale": "linear"
        }

        cycle_meta_map = {
            "H1": {"name": "Halving 1 (2012)", "color": "#38bdf8"},
            "H2": {"name": "Halving 2 (2016)", "color": "#a855f7"},
            "H3": {"name": "Halving 3 (2020)", "color": "#10b981"},
            "H4": {"name": "Halving 4 (2024 Actual)", "color": "#f59e0b"},
            "bench": {"name": "Promedio Histórico", "color": "#94a3b8"}
        }

        def format_currency_val(val: float, is_stables: bool = False) -> str:
            if val is None:
                return "-"
            if is_stables:
                if abs(val) >= 1e9:
                    return f"${val/1e9:,.2f}B"
                elif abs(val) >= 1e6:
                    return f"${val/1e6:,.1f}M"
                else:
                    return f"${val:,.0f}"
            else:
                return f"${val:,.2f}"

        def build_growth_figure(growth_data: dict, g_state: dict) -> go.Figure:
            periods = growth_data.get("periods", [])
            cycles = growth_data.get("cycles", {})
            benchmark = growth_data.get("benchmark", [])
            metric = g_state.get("metric", "periodic_delta")
            sel_cycles = g_state.get("selected_cycles", ["H1", "H2", "H3", "H4", "bench"])
            y_scale = g_state.get("y_scale", "linear")
            is_stables = (g_state.get("asset_type") == "stablecoins")
            val_type_label = "Capitalización Stablecoins" if is_stables else "Precio BTC"

            x_labels = [p["label_short"] for p in periods]
            x_full_labels = [p["label_full"] for p in periods]

            if metric == "dual_view":
                fig = make_subplots(
                    rows=2, cols=1,
                    shared_xaxes=True,
                    vertical_spacing=0.10,
                    row_heights=[0.55, 0.45],
                    subplot_titles=[
                        f'Variación Periódica Δ% ({val_type_label} por Intervalo)',
                        f'Crecimiento Acumulado (% desde el día del Halving H0)'
                    ]
                )

                for cid in ["H1", "H2", "H3", "H4"]:
                    if cid in sel_cycles and cid in cycles:
                        c_list = cycles[cid]
                        c_info = cycle_meta_map[cid]
                        y_delta = [p["periodic_return_pct"] for p in c_list]
                        y_cum = [p["cumulative_return_pct"] for p in c_list]
                        
                        customdata = [
                            [
                                format_currency_val(p.get("start_price", 0), is_stables),
                                format_currency_val(p.get("end_price", 0), is_stables),
                                p.get("cumulative_return_pct", 0),
                                p.get("cumulative_multiplier", 1.0),
                                x_full_labels[i] if i < len(x_full_labels) else "",
                                format_currency_val(p.get("net_inflow_usd", 0), is_stables)
                            ]
                            for i, p in enumerate(c_list)
                        ]

                        hover_p1 = (
                            f"<b>{c_info['name']}</b><br>"
                            + "Período: %{customdata[4]}<br>"
                            + f"Inicio: %{{customdata[0]}} | Fin: %{{customdata[1]}}<br>"
                            + (f"Inyección Neta: %{{customdata[5]}}<br>" if is_stables else "")
                            + "Variación Período: <b>%{y:+.2f}%</b><extra></extra>"
                        )

                        fig.add_trace(
                            go.Bar(
                                x=x_labels,
                                y=y_delta,
                                name=f"{c_info['name']} (Δ%)",
                                marker_color=c_info["color"],
                                customdata=customdata,
                                hovertemplate=hover_p1,
                                showlegend=True
                            ),
                            row=1, col=1
                        )

                        fig.add_trace(
                            go.Scatter(
                                x=x_labels,
                                y=y_cum,
                                mode='lines+markers',
                                name=f"{c_info['name']} (Acumulado)",
                                line=dict(color=c_info["color"], width=2.5),
                                marker=dict(size=5),
                                customdata=customdata,
                                hovertemplate=(
                                    f"<b>{c_info['name']} (Acumulado)</b><br>"
                                    + "Período: %{customdata[4]}<br>"
                                    + "Crecimiento Acumulado: <b>+%{y:,.1f}%</b> (%{customdata[3]:.2f}x)<extra></extra>"
                                ),
                                showlegend=False
                            ),
                            row=2, col=1
                        )

                if "bench" in sel_cycles and benchmark:
                    y_b_delta = [b["mean_periodic_pct"] for b in benchmark]
                    y_b_cum = [b["mean_cumulative_pct"] for b in benchmark]
                    fig.add_trace(
                        go.Bar(
                            x=x_labels,
                            y=y_b_delta,
                            name="Promedio Histórico (Δ%)",
                            marker_color="#64748b",
                            opacity=0.8,
                            hovertemplate="<b>Promedio Histórico</b><br>Variación Media: <b>%{y:+.2f}%</b><extra></extra>"
                        ),
                        row=1, col=1
                    )
                    fig.add_trace(
                        go.Scatter(
                            x=x_labels,
                            y=y_b_cum,
                            mode='lines+markers',
                            name="Promedio Acumulado",
                            line=dict(color="#94a3b8", width=2, dash='dash'),
                            marker=dict(size=4),
                            hovertemplate="<b>Promedio Acumulado</b><br>Retorno: <b>+%{y:,.1f}%</b><extra></extra>",
                            showlegend=False
                        ),
                        row=2, col=1
                    )

                fig.update_layout(
                    paper_bgcolor='#111827',
                    plot_bgcolor='#0a0e17',
                    font=dict(family='Plus Jakarta Sans, sans-serif', color='#94a3b8', size=11),
                    height=580,
                    barmode='group',
                    margin=dict(l=55, r=25, t=50, b=75),
                    legend=dict(
                        orientation='h',
                        yanchor='top',
                        y=-0.14,
                        xanchor='center',
                        x=0.5,
                        bgcolor='rgba(15, 23, 42, 0.95)',
                        bordercolor='#1e293b',
                        borderwidth=1,
                        font=dict(size=10, color='#e2e8f0', family='JetBrains Mono')
                    )
                )
                fig.update_yaxes(gridcolor='#1e293b', zerolinecolor='#cbd5e1', zerolinewidth=1.5, tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10), row=1, col=1)
                fig.update_yaxes(gridcolor='#1e293b', tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10), row=2, col=1)
                fig.update_xaxes(gridcolor='#1e293b', tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10))
                return fig

            elif metric == "heatmap":
                fig = go.Figure()
                z_matrix = []
                y_labels = []
                for cid in ["H1", "H2", "H3", "H4"]:
                    if cid in sel_cycles and cid in cycles:
                        c_list = cycles[cid]
                        c_info = cycle_meta_map[cid]
                        y_labels.append(c_info["name"])
                        z_matrix.append([p["periodic_return_pct"] if p["has_data"] else None for p in c_list])

                if "bench" in sel_cycles and benchmark:
                    y_labels.append("Promedio Histórico")
                    z_matrix.append([b["mean_periodic_pct"] if b["has_data"] else None for b in benchmark])

                fig.add_trace(go.Heatmap(
                    z=z_matrix,
                    x=x_labels,
                    y=y_labels,
                    colorscale='RdYlGn',
                    zmid=0,
                    text=[[f"{v:+.1f}%" if v is not None else "" for v in row] for row in z_matrix],
                    texttemplate="%{text}",
                    textfont=dict(family='JetBrains Mono', size=10, color='#ffffff'),
                    colorbar=dict(
                        title=dict(text='Variación Δ%', font=dict(color='#cbd5e1', size=10)),
                        tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=9),
                        ticksuffix='%'
                    )
                ))

                fig.update_layout(
                    paper_bgcolor='#111827',
                    plot_bgcolor='#0a0e17',
                    font=dict(family='Plus Jakarta Sans, sans-serif', color='#94a3b8', size=11),
                    height=380,
                    margin=dict(l=140, r=25, t=30, b=50),
                    xaxis=dict(gridcolor='#1e293b', tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10)),
                    yaxis=dict(gridcolor='#1e293b', tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10))
                )
                return fig

            elif metric == "cumulative_pct":
                fig = go.Figure()
                for cid in ["H1", "H2", "H3", "H4"]:
                    if cid in sel_cycles and cid in cycles:
                        c_list = cycles[cid]
                        c_info = cycle_meta_map[cid]
                        y_vals = [p["cumulative_return_pct"] for p in c_list]
                        customdata = [
                            [
                                format_currency_val(p.get("end_price", 0), is_stables),
                                p.get("cumulative_multiplier", 1.0),
                                x_full_labels[i] if i < len(x_full_labels) else ""
                            ]
                            for i, p in enumerate(c_list)
                        ]
                        fig.add_trace(go.Scatter(
                            x=x_labels,
                            y=y_vals,
                            mode='lines+markers',
                            name=c_info["name"],
                            line=dict(color=c_info["color"], width=2.5),
                            marker=dict(size=6),
                            customdata=customdata,
                            hovertemplate=(
                                f"<b>{c_info['name']}</b><br>"
                                + "Período: %{customdata[2]}<br>"
                                + f"{val_type_label}: %{{customdata[0]}}<br>"
                                + "Crecimiento Acumulado: <b>+%{y:,.1f}%</b> (%{customdata[1]:.2f}x)<extra></extra>"
                            )
                        ))

                if "bench" in sel_cycles and benchmark:
                    y_b_cum = [b["mean_cumulative_pct"] for b in benchmark]
                    fig.add_trace(go.Scatter(
                        x=x_labels,
                        y=y_b_cum,
                        mode='lines+markers',
                        name="Promedio Histórico",
                        line=dict(color="#94a3b8", width=2, dash='dash'),
                        marker=dict(size=5),
                        hovertemplate="<b>Promedio Histórico</b><br>Retorno Acumulado: <b>+%{y:,.1f}%</b><extra></extra>"
                    ))

                fig.update_layout(
                    paper_bgcolor='#111827',
                    plot_bgcolor='#0a0e17',
                    font=dict(family='Plus Jakarta Sans, sans-serif', color='#94a3b8', size=11),
                    height=520,
                    margin=dict(l=55, r=25, t=35, b=75),
                    hovermode='x unified',
                    legend=dict(
                        orientation='h',
                        yanchor='top',
                        y=-0.16,
                        xanchor='center',
                        x=0.5,
                        bgcolor='rgba(15, 23, 42, 0.95)',
                        bordercolor='#1e293b',
                        borderwidth=1,
                        font=dict(size=10, color='#e2e8f0', family='JetBrains Mono')
                    ),
                    xaxis=dict(gridcolor='#1e293b', tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10)),
                    yaxis=dict(
                        title=f'Crecimiento Acumulado (%) — {val_type_label}',
                        type='log' if y_scale == 'log' else 'linear',
                        gridcolor='#1e293b',
                        ticksuffix='%',
                        tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10)
                    )
                )
                return fig

            elif metric == "cumulative_mult":
                fig = go.Figure()
                for cid in ["H1", "H2", "H3", "H4"]:
                    if cid in sel_cycles and cid in cycles:
                        c_list = cycles[cid]
                        c_info = cycle_meta_map[cid]
                        y_vals = [p["cumulative_multiplier"] for p in c_list]
                        customdata = [
                            [
                                format_currency_val(p.get("end_price", 0), is_stables),
                                p.get("cumulative_return_pct", 0),
                                x_full_labels[i] if i < len(x_full_labels) else ""
                            ]
                            for i, p in enumerate(c_list)
                        ]
                        fig.add_trace(go.Scatter(
                            x=x_labels,
                            y=y_vals,
                            mode='lines+markers',
                            name=c_info["name"],
                            line=dict(color=c_info["color"], width=2.5),
                            marker=dict(size=6),
                            customdata=customdata,
                            hovertemplate=(
                                f"<b>{c_info['name']}</b><br>"
                                + "Período: %{customdata[2]}<br>"
                                + f"{val_type_label}: %{{customdata[0]}}<br>"
                                + "Múltiplo: <b>%{y:.2f}x</b> (+%{customdata[1]:,.1f}%)<extra></extra>"
                            )
                        ))

                if "bench" in sel_cycles and benchmark:
                    y_b_mult = [b["mean_cumulative_multiplier"] for b in benchmark]
                    fig.add_trace(go.Scatter(
                        x=x_labels,
                        y=y_b_mult,
                        mode='lines+markers',
                        name="Promedio Histórico",
                        line=dict(color="#94a3b8", width=2, dash='dash'),
                        marker=dict(size=5),
                        hovertemplate="<b>Promedio Histórico</b><br>Múltiplo: <b>%{y:.2f}x</b><extra></extra>"
                    ))

                fig.update_layout(
                    paper_bgcolor='#111827',
                    plot_bgcolor='#0a0e17',
                    font=dict(family='Plus Jakarta Sans, sans-serif', color='#94a3b8', size=11),
                    height=520,
                    margin=dict(l=55, r=25, t=35, b=75),
                    hovermode='x unified',
                    legend=dict(
                        orientation='h',
                        yanchor='top',
                        y=-0.16,
                        xanchor='center',
                        x=0.5,
                        bgcolor='rgba(15, 23, 42, 0.95)',
                        bordercolor='#1e293b',
                        borderwidth=1,
                        font=dict(size=10, color='#e2e8f0', family='JetBrains Mono')
                    ),
                    xaxis=dict(gridcolor='#1e293b', tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10)),
                    yaxis=dict(
                        title=f'Múltiplo Normalizado (Mcap_t / Mcap_H0)',
                        type='log' if y_scale == 'log' else 'linear',
                        gridcolor='#1e293b',
                        ticksuffix='x',
                        tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10)
                    )
                )
                return fig

            elif metric == "absolute_usd":
                fig = go.Figure()
                for cid in ["H1", "H2", "H3", "H4"]:
                    if cid in sel_cycles and cid in cycles:
                        c_list = cycles[cid]
                        c_info = cycle_meta_map[cid]
                        y_vals = [p["end_price"] / 1e9 if (p.get("end_price") is not None and is_stables) else p.get("end_price") for p in c_list]
                        customdata = [
                            [
                                format_currency_val(p.get("start_price", 0), is_stables),
                                format_currency_val(p.get("end_price", 0), is_stables),
                                p.get("periodic_return_pct", 0),
                                p.get("cumulative_return_pct", 0),
                                x_full_labels[i] if i < len(x_full_labels) else ""
                            ]
                            for i, p in enumerate(c_list)
                        ]
                        fig.add_trace(go.Bar(
                            x=x_labels,
                            y=y_vals,
                            name=c_info["name"],
                            marker_color=c_info["color"],
                            customdata=customdata,
                            hovertemplate=(
                                f"<b>{c_info['name']}</b><br>"
                                + "Período: %{customdata[4]}<br>"
                                + f"Cierre: %{{customdata[1]}} (Inicio: %{{customdata[0]}})<br>"
                                + "Variación Periódica: <b>%{customdata[2]:+.2f}%</b><extra></extra>"
                            )
                        ))

                fig.update_layout(
                    paper_bgcolor='#111827',
                    plot_bgcolor='#0a0e17',
                    font=dict(family='Plus Jakarta Sans, sans-serif', color='#94a3b8', size=11),
                    height=520,
                    barmode='group',
                    margin=dict(l=55, r=25, t=35, b=75),
                    legend=dict(
                        orientation='h',
                        yanchor='top',
                        y=-0.16,
                        xanchor='center',
                        x=0.5,
                        bgcolor='rgba(15, 23, 42, 0.95)',
                        bordercolor='#1e293b',
                        borderwidth=1,
                        font=dict(size=10, color='#e2e8f0', family='JetBrains Mono')
                    ),
                    xaxis=dict(
                        title=dict(text='Períodos Relativos Post-Halving', font=dict(color='#94a3b8', size=11)),
                        gridcolor='#1e293b',
                        tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10)
                    ),
                    yaxis=dict(
                        title='Capitalización de Mercado ($ Billones USD)' if is_stables else 'Precio BTC ($ USD)',
                        type='log' if y_scale == 'log' else 'linear',
                        gridcolor='#1e293b',
                        ticksuffix='B' if is_stables else '',
                        tickprefix='' if is_stables else '$',
                        tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10)
                    )
                )
                return fig

            else:
                # Default: Periodic Delta (Variación Periódica Δ% en Barras Agrupadas)
                fig = go.Figure()
                for cid in ["H1", "H2", "H3", "H4"]:
                    if cid in sel_cycles and cid in cycles:
                        c_list = cycles[cid]
                        c_info = cycle_meta_map[cid]
                        y_vals = [p["periodic_return_pct"] for p in c_list]
                        customdata = [
                            [
                                format_currency_val(p.get("start_price", 0), is_stables),
                                format_currency_val(p.get("end_price", 0), is_stables),
                                p.get("cumulative_return_pct", 0),
                                p.get("cumulative_multiplier", 1.0),
                                p.get("is_in_progress", False),
                                x_full_labels[i] if i < len(x_full_labels) else "",
                                format_currency_val(p.get("net_inflow_usd", 0), is_stables)
                            ]
                            for i, p in enumerate(c_list)
                        ]
                        hover_temp = (
                            f"<b>{c_info['name']}</b><br>"
                            + "Período: %{customdata[5]}<br>"
                            + f"Inicio: %{{customdata[0]}} | Cierre: %{{customdata[1]}}<br>"
                            + (f"Inyección Neta: %{{customdata[6]}}<br>" if is_stables else "")
                            + "Variación Periódica: <b>%{y:+.2f}%</b><br>"
                            + "Crecimiento Acumulado: +%{customdata[2]:,.1f}% (%{customdata[3]:.2f}x)<extra></extra>"
                        )
                        fig.add_trace(go.Bar(
                            x=x_labels,
                            y=y_vals,
                            name=c_info["name"],
                            marker_color=c_info["color"],
                            customdata=customdata,
                            hovertemplate=hover_temp
                        ))

                if "bench" in sel_cycles and benchmark:
                    y_b_delta = [b["mean_periodic_pct"] for b in benchmark]
                    fig.add_trace(go.Bar(
                        x=x_labels,
                        y=y_b_delta,
                        name="Promedio Histórico",
                        marker_color="#64748b",
                        opacity=0.85,
                        hovertemplate="<b>Promedio Histórico</b><br>Variación Media: <b>%{y:+.2f}%</b><extra></extra>"
                    ))

                fig.update_layout(
                    paper_bgcolor='#111827',
                    plot_bgcolor='#0a0e17',
                    font=dict(family='Plus Jakarta Sans, sans-serif', color='#94a3b8', size=11),
                    height=520,
                    barmode='group',
                    margin=dict(l=55, r=25, t=35, b=75),
                    legend=dict(
                        orientation='h',
                        yanchor='top',
                        y=-0.16,
                        xanchor='center',
                        x=0.5,
                        bgcolor='rgba(15, 23, 42, 0.95)',
                        bordercolor='#1e293b',
                        borderwidth=1,
                        font=dict(size=10, color='#e2e8f0', family='JetBrains Mono')
                    ),
                    xaxis=dict(
                        title=dict(text='Períodos Relativos Post-Halving', font=dict(color='#94a3b8', size=11)),
                        gridcolor='#1e293b',
                        tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10)
                    ),
                    yaxis=dict(
                        title=f'Variación Periódica (Δ% Crecimiento / Decrecimiento de {val_type_label})',
                        gridcolor='#1e293b',
                        zeroline=True,
                        zerolinecolor='#cbd5e1',
                        zerolinewidth=1.5,
                        ticksuffix='%',
                        tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10)
                    )
                )
                return fig

        def build_growth_table_html(growth_data: dict, g_state: dict) -> str:
            periods = growth_data.get("periods", [])
            cycles = growth_data.get("cycles", {})
            benchmark = growth_data.get("benchmark", [])
            is_stables = (g_state.get("asset_type") == "stablecoins")

            html = '''
            <table class="w-full text-left text-xs font-mono border-collapse">
                <thead>
                    <tr class="border-b border-[#1e293b] text-slate-400 text-[11px]">
                        <th class="py-2 px-2.5">Período</th>
                        <th class="py-2 px-2.5">Días Relativos</th>
                        <th class="py-2 px-2 text-right text-sky-400">H1 (2012)</th>
                        <th class="py-2 px-2 text-right text-purple-400">H2 (2016)</th>
                        <th class="py-2 px-2 text-right text-emerald-400">H3 (2020)</th>
                        <th class="py-2 px-2 text-right text-amber-400">H4 (Actual)</th>
                        <th class="py-2 px-2.5 text-right text-slate-300">Promedio</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-[#1e293b]/60">
            '''

            for idx, p in enumerate(periods):
                p_label = p["label_short"]
                d_range = f"H+{p['start_day']} a H+{p['end_day']}d"

                def format_cell(cid: str) -> str:
                    if cid == "H1" and is_stables:
                        return "<span class='text-slate-600' title='Las stablecoins no existían en 2012 (creadas a finales de 2014)'>-</span>"
                    if cid not in cycles or idx >= len(cycles[cid]):
                        return "<span class='text-slate-600'>-</span>"
                    c_period = cycles[cid][idx]
                    if not c_period.get("has_data") or c_period.get("periodic_return_pct") is None:
                        return "<span class='text-slate-600'>-</span>"
                    val = c_period["periodic_return_pct"]
                    in_prog = " <span class='text-amber-400 text-[9px]'>*</span>" if c_period.get("is_in_progress") else ""
                    
                    # Tooltip con valores en $
                    p_start_str = format_currency_val(c_period.get("start_price", 0), is_stables)
                    p_end_str = format_currency_val(c_period.get("end_price", 0), is_stables)
                    cell_title = f"{p_start_str} -> {p_end_str}"

                    if val > 0:
                        return f"<span class='text-emerald-400 font-bold' title='{cell_title}'>+{val:.1f}%{in_prog}</span>"
                    elif val < 0:
                        return f"<span class='text-rose-400 font-bold' title='{cell_title}'>{val:.1f}%{in_prog}</span>"
                    else:
                        return f"<span class='text-slate-400' title='{cell_title}'>0.0%{in_prog}</span>"

                b_val_str = "<span class='text-slate-600'>-</span>"
                if idx < len(benchmark) and benchmark[idx].get("has_data") and benchmark[idx].get("mean_periodic_pct") is not None:
                    b_val = benchmark[idx]["mean_periodic_pct"]
                    if b_val > 0:
                        b_val_str = f"<span class='text-emerald-400/90 font-medium'>+{b_val:.1f}%</span>"
                    elif b_val < 0:
                        b_val_str = f"<span class='text-rose-400/90 font-medium'>{b_val:.1f}%</span>"
                    else:
                        b_val_str = "<span class='text-slate-400'>0.0%</span>"

                html += f'''
                    <tr class="hover:bg-slate-800/40 transition-colors">
                        <td class="py-2 px-2.5 font-bold text-white">{p_label}</td>
                        <td class="py-2 px-2.5 text-slate-400 text-[11px]">{d_range}</td>
                        <td class="py-2 px-2 text-right">{format_cell("H1")}</td>
                        <td class="py-2 px-2 text-right">{format_cell("H2")}</td>
                        <td class="py-2 px-2 text-right">{format_cell("H3")}</td>
                        <td class="py-2 px-2 text-right font-bold">{format_cell("H4")}</td>
                        <td class="py-2 px-2.5 text-right font-semibold">{b_val_str}</td>
                    </tr>
                '''

            html += '</tbody></table>'
            return html

        def format_btc_price_val(val: float) -> str:
            if val is None:
                return "-"
            if abs(val) >= 1000:
                return f"${val:,.0f}"
            elif abs(val) >= 1:
                return f"${val:,.2f}"
            else:
                return f"${val:,.4f}"

        def build_stables_detailed_table_html(stables_data: dict, btc_data: dict = None) -> str:
            periods = stables_data.get("periods", [])
            s_cycles = stables_data.get("cycles", {})
            b_cycles = btc_data.get("cycles", {}) if btc_data else {}

            html = '''
            <table class="w-full text-left text-xs font-mono border-collapse">
                <thead>
                    <tr class="border-b border-[#1e293b] text-slate-400 text-[11px]">
                        <th class="py-2.5 px-3">Período</th>
                        <th class="py-2.5 px-3">Días Relativos</th>
                        <th class="py-2.5 px-3 text-right text-purple-400 min-w-[280px]">H2 (2016) Mcap & Precio BTC</th>
                        <th class="py-2.5 px-3 text-right text-emerald-400 min-w-[280px]">H3 (2020) Mcap & Precio BTC</th>
                        <th class="py-2.5 px-3 text-right text-amber-400 min-w-[280px]">H4 (Actual) Mcap & Precio BTC</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-[#1e293b]/60">
            '''

            for idx, p in enumerate(periods):
                p_label = p["label_short"]
                d_range = f"H+{p['start_day']} a H+{p['end_day']}d"

                def format_cycle_col(cid: str) -> str:
                    s_c = s_cycles.get(cid, [])[idx] if (cid in s_cycles and idx < len(s_cycles[cid])) else {}
                    b_c = b_cycles.get(cid, [])[idx] if (cid in b_cycles and idx < len(b_cycles[cid])) else {}

                    has_s = s_c.get("has_data", False) and s_c.get("start_price") is not None
                    has_b = b_c.get("has_data", False) and b_c.get("start_price") is not None

                    if not has_s and not has_b:
                        return "<span class='text-slate-600'>-</span>"

                    # 1. Capitalización Stablecoins (Mcap Inicial -> Final, Flujo $ e Incremento %)
                    if has_s:
                        s_p0 = s_c.get("start_price", 0)
                        s_p1 = s_c.get("end_price", 0)
                        s_delta = s_c.get("net_inflow_usd", 0)
                        s_pct = s_c.get("periodic_return_pct", 0)
                        s_p0_str = format_currency_val(s_p0, True)
                        s_p1_str = format_currency_val(s_p1, True)
                        s_delta_str = format_currency_val(abs(s_delta), True)
                        s_color = "text-emerald-400" if s_pct >= 0 else "text-rose-400"
                        s_badge_bg = "bg-emerald-950/60 border-emerald-800/60" if s_pct >= 0 else "bg-rose-950/60 border-rose-800/60"
                        s_sign = "+" if s_delta >= 0 else "-"
                        s_in_prog = " <span class='text-amber-400 text-[9px] font-black'>*</span>" if s_c.get("is_in_progress") else ""

                        mcap_block = f'''
                        <div class="flex items-center justify-between gap-1.5 text-[11px] bg-[#0a0e17]/90 px-2 py-1.5 rounded border border-[#1e293b]/70 hover:border-slate-700 transition-colors">
                            <span class="text-slate-400 text-[10.5px]"><b class="text-emerald-400">💧 Mcap:</b> <span class="text-slate-200">{s_p0_str} → {s_p1_str}</span></span>
                            <span class="text-right font-mono font-bold {s_color} whitespace-nowrap">
                                {s_sign}{s_delta_str} <span class="text-[10px] font-extrabold px-1.5 py-0.5 rounded border {s_badge_bg}">({'+' if s_pct >= 0 else ''}{s_pct:.1f}%){s_in_prog}</span>
                            </span>
                        </div>
                        '''
                    else:
                        mcap_block = '''
                        <div class="flex items-center justify-between gap-1.5 text-[11px] bg-[#0a0e17]/50 px-2 py-1.5 rounded border border-[#1e293b]/40">
                            <span class="text-slate-500 text-[10px]"><b class="text-slate-500">💧 Mcap:</b> Inexistente en 2012</span>
                            <span class="text-slate-600 text-[10px]">-</span>
                        </div>
                        '''

                    # 2. Precio Bitcoin (Precio Inicial -> Final, Variación $ e Incremento/Reducción %)
                    if has_b:
                        b_p0 = b_c.get("start_price", 0)
                        b_p1 = b_c.get("end_price", 0)
                        b_delta = b_p1 - b_p0
                        b_pct = b_c.get("periodic_return_pct", 0)
                        b_p0_str = format_btc_price_val(b_p0)
                        b_p1_str = format_btc_price_val(b_p1)
                        b_delta_str = format_btc_price_val(abs(b_delta))
                        b_color = "text-emerald-400" if b_pct >= 0 else "text-rose-400"
                        b_badge_bg = "bg-emerald-950/60 border-emerald-800/60" if b_pct >= 0 else "bg-rose-950/60 border-rose-800/60"
                        b_sign = "+" if b_delta >= 0 else "-"
                        b_in_prog = " <span class='text-amber-400 text-[9px] font-black'>*</span>" if b_c.get("is_in_progress") else ""

                        btc_block = f'''
                        <div class="flex items-center justify-between gap-1.5 text-[11px] bg-[#0a0e17]/90 px-2 py-1.5 rounded border border-[#1e293b]/70 hover:border-slate-700 transition-colors">
                            <span class="text-slate-400 text-[10.5px]"><b class="text-amber-400">🪙 BTC:</b> <span class="text-slate-200">{b_p0_str} → {b_p1_str}</span></span>
                            <span class="text-right font-mono font-bold {b_color} whitespace-nowrap">
                                {b_sign}{b_delta_str} <span class="text-[10px] font-extrabold px-1.5 py-0.5 rounded border {b_badge_bg}">({'+' if b_pct >= 0 else ''}{b_pct:.1f}%){b_in_prog}</span>
                            </span>
                        </div>
                        '''
                    else:
                        btc_block = ""

                    return f'''
                    <div class="space-y-1.5 py-1">
                        {mcap_block}
                        {btc_block}
                    </div>
                    '''

                html += f'''
                    <tr class="hover:bg-slate-800/40 transition-colors">
                        <td class="py-2.5 px-3 font-bold text-white align-middle">{p_label}</td>
                        <td class="py-2.5 px-3 text-slate-400 text-[11px] align-middle">{d_range}</td>
                        <td class="py-2.5 px-3 text-right">{format_cycle_col("H2")}</td>
                        <td class="py-2.5 px-3 text-right">{format_cycle_col("H3")}</td>
                        <td class="py-2.5 px-3 text-right font-semibold">{format_cycle_col("H4")}</td>
                    </tr>
                '''

            html += '</tbody></table>'
            return html

        def build_growth_insights_html(growth_data: dict, g_state: dict) -> str:
            summary = growth_data.get("summary", {})
            is_stables = (g_state.get("asset_type") == "stablecoins")
            step_name = summary.get("step_label", "1 Semestre")
            win_r = summary.get("global_positive_ratio", 0)
            avg_r = summary.get("avg_period_return", 0)
            max_r = summary.get("max_period_return", 0)
            min_r = summary.get("min_period_return", 0)

            h4_periods = growth_data.get("cycles", {}).get("H4", [])
            h4_active = [p for p in h4_periods if p.get("has_data") and p.get("periodic_return_pct") is not None]
            h4_last_ret = h4_active[-1]["periodic_return_pct"] if h4_active else 0
            h4_last_label = growth_data.get("periods", [])[len(h4_active)-1]["label_short"] if h4_active else "-"
            h4_last_inflow = h4_active[-1].get("net_inflow_usd", 0) if h4_active else 0

            if is_stables:
                html = f'''
                <div class="space-y-3 text-xs text-slate-300 leading-relaxed font-sans">
                    <div class="p-3 bg-[#0a0e17] rounded-lg border border-[#1e293b]">
                        <div class="font-bold text-white font-mono text-[11px] mb-1">🌊 Flujo de Liquidez en {step_name}</div>
                        <div>Evaluando la masa monetaria de stablecoins en bloques de <b>{step_name}</b>, el <b>{win_r:.1f}%</b> de los períodos históricos completados registraron expansión neta de liquidez (acuñación positiva de capital fiat hacia cripto).</div>
                    </div>

                    <div class="p-3 bg-[#0a0e17] rounded-lg border border-[#1e293b]">
                        <div class="font-bold text-amber-400 font-mono text-[11px] mb-1">🚀 Impulsos de Emisión vs Contracción</div>
                        <div>La tasa de expansión promedio por período es de <b class="text-emerald-400">{avg_r:+.2f}%</b>, con un pico de emisión récord de <b class="text-emerald-400">+{max_r:.1f}%</b> y una contracción máxima de absorción de <b class="text-rose-400">{min_r:.1f}%</b>.</div>
                    </div>

                    <div class="p-3 bg-[#0a0e17] rounded-lg border border-[#1e293b]">
                        <div class="font-bold text-emerald-400 font-mono text-[11px] mb-1">⚡ Estado Liquidez Ciclo 4 (Actual)</div>
                        <div>En su período más reciente (<b>{h4_last_label}</b>), el Market Cap de stablecoins varió <b class="{'text-emerald-400' if h4_last_ret >= 0 else 'text-rose-400'}">{h4_last_ret:+.2f}%</b> ({format_currency_val(h4_last_inflow, True)} USD), confirmando la sólida base de poder adquisitivo para sostener la estructura alcista del ciclo.</div>
                    </div>
                </div>
                '''
            else:
                html = f'''
                <div class="space-y-3 text-xs text-slate-300 leading-relaxed font-sans">
                    <div class="p-3 bg-[#0a0e17] rounded-lg border border-[#1e293b]">
                        <div class="font-bold text-white font-mono text-[11px] mb-1">📊 Dinámica en {step_name}</div>
                        <div>Evaluando los ciclos en bloques de <b>{step_name}</b>, el <b>{win_r:.1f}%</b> de los períodos históricos completados cerraron con rendimientos positivos (crecimiento neto).</div>
                    </div>

                    <div class="p-3 bg-[#0a0e17] rounded-lg border border-[#1e293b]">
                        <div class="font-bold text-amber-400 font-mono text-[11px] mb-1">🚀 Impulsos y Volatilidad</div>
                        <div>El retorno promedio por período asciende a <b class="text-emerald-400">{avg_r:+.2f}%</b>, con un rally máximo registrado de <b class="text-emerald-400">+{max_r:.1f}%</b> y una contracción máxima por período de <b class="text-rose-400">{min_r:.1f}%</b>.</div>
                    </div>

                    <div class="p-3 bg-[#0a0e17] rounded-lg border border-[#1e293b]">
                        <div class="font-bold text-sky-400 font-mono text-[11px] mb-1">⚡ Estado Ciclo 4 (Actual)</div>
                        <div>En su período más reciente evaluado (<b>{h4_last_label}</b>), el Ciclo 4 exhibe una variación de <b class="{'text-emerald-400' if h4_last_ret >= 0 else 'text-rose-400'}">{h4_last_ret:+.2f}%</b>, alineándose con las pautas de consolidación/expansión observadas en los Halvings 2 y 3.</div>
                    </div>
                </div>
                '''
            return html

        def render_growth_tab():
            growth_container.clear()
            growth_data = analyzer.calculate_periodic_growth_analysis(
                timeframe=growth_state["timeframe"],
                step_size=growth_state["step_size"],
                max_days=growth_state["max_days"],
                asset_type=growth_state.get("asset_type", "btc")
            )

            periods = growth_data.get("periods", [])
            summary = growth_data.get("summary", {})
            is_stables = (growth_state.get("asset_type") == "stablecoins")

            with growth_container:
                # 1. Panel de Controles
                with ui.card().classes('w-full bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-md mt-3'):
                    with ui.row().classes('w-full justify-between items-center flex-wrap gap-2 border-b border-[#1e293b] pb-3 mb-3'):
                        with ui.column().classes('gap-0.5'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('query_stats', size='1.3rem').classes('text-amber-400')
                                ui.label('CRECIMIENTO / DECRECIMIENTO POR TEMPORALIDAD').classes('text-xs font-extrabold text-white font-mono tracking-wider')
                            ui.label('Selecciona el activo (Precio BTC o Capitalización de Stablecoins), la temporalidad (Día, Semana, Mes, Trimestre, Semestre, Año) y cantidad de períodos para comparar la variación periódica (Δ%) o acumulada entre ciclos.').classes('text-[11px] text-slate-400')

                    with ui.row().classes('w-full items-center justify-between flex-wrap gap-3'):
                        # Selectores Principales
                        with ui.row().classes('items-center flex-wrap gap-2.5'):
                            ui.label('VARIABLE / ACTIVO:').classes('text-[11px] font-bold text-amber-400 font-mono self-center')
                            g_asset_select = ui.select(
                                options={
                                    'btc': 'Precio de Bitcoin (BTC)',
                                    'stablecoins': 'Capitalización Stablecoins (Mcap)'
                                },
                                value=growth_state.get('asset_type', 'btc')
                            ).props('dense outlined dark options-dense').classes('w-60 text-xs')

                            ui.label('TEMPORALIDAD:').classes('text-[11px] font-bold text-slate-400 font-mono self-center ml-1')
                            g_tf_select = ui.select(
                                options={
                                    'day': 'Día (1 día base)',
                                    'week': 'Semana (7 días)',
                                    'month': 'Mes (30 días)',
                                    'quarter': 'Trimestre (90 días / 3M)',
                                    'semester': 'Semestre (180 días / 6M)',
                                    'year': 'Año (365 días / 12M)'
                                },
                                value=growth_state['timeframe']
                            ).props('dense outlined dark options-dense').classes('w-52 text-xs')

                            ui.label('CANTIDAD:').classes('text-[11px] font-bold text-slate-400 font-mono self-center ml-1')
                            g_step_select = ui.select(
                                options={
                                    1: '1 Período (Base)',
                                    2: '2 Períodos (x2)',
                                    3: '3 Períodos (x3)',
                                    4: '4 Períodos (x4)',
                                    6: '6 Períodos (x6)',
                                    12: '12 Períodos (x12)'
                                },
                                value=growth_state['step_size']
                            ).props('dense outlined dark options-dense').classes('w-40 text-xs')

                            ui.label('MÉTRICA:').classes('text-[11px] font-bold text-slate-400 font-mono self-center ml-1')
                            g_metric_select = ui.select(
                                options={
                                    'periodic_delta': 'Variación Periódica Δ% (Crecimiento / Decrecimiento)',
                                    'cumulative_pct': 'Crecimiento Acumulado (+% desde H0)',
                                    'cumulative_mult': 'Múltiplo Acumulado (Nx)',
                                    'absolute_usd': 'Valor Absoluto ($ USD / Mcap)',
                                    'dual_view': 'Vista Dual (Barras Δ% + Curva Acumulada)',
                                    'heatmap': 'Mapa de Calor (Matriz de Retornos)'
                                },
                                value=growth_state['metric']
                            ).props('dense outlined dark options-dense').classes('w-64 text-xs')

                        # Ventana y Escala
                        with ui.row().classes('items-center flex-wrap gap-2.5'):
                            ui.label('VENTANA:').classes('text-[11px] font-bold text-slate-400 font-mono self-center')
                            g_win_select = ui.select(
                                options={
                                    365: 'Primer Año (0 a +365d)',
                                    730: '2 Años (0 a +730d)',
                                    1080: 'Ciclo Estándar (0 a +1080d)',
                                    1400: 'Ciclo Extendido (0 a +1400d)'
                                },
                                value=growth_state['max_days']
                            ).props('dense outlined dark options-dense').classes('w-52 text-xs')

                    # Fila de Filtro de Ciclos
                    with ui.row().classes('w-full items-center justify-between flex-wrap gap-2 pt-2 border-t border-[#1e293b]/70 mt-1'):
                        with ui.row().classes('items-center gap-3 flex-wrap'):
                            ui.label('CICLOS:').classes('text-[11px] font-bold text-slate-400 font-mono self-center')
                            g_chk_h1 = ui.checkbox('H1 (2012)', value=('H1' in growth_state['selected_cycles'])).props('dense dark color=cyan').classes('text-xs text-sky-400 font-semibold')
                            g_chk_h2 = ui.checkbox('H2 (2016)', value=('H2' in growth_state['selected_cycles'])).props('dense dark color=purple').classes('text-xs text-purple-400 font-semibold')
                            g_chk_h3 = ui.checkbox('H3 (2020)', value=('H3' in growth_state['selected_cycles'])).props('dense dark color=green').classes('text-xs text-emerald-400 font-semibold')
                            g_chk_h4 = ui.checkbox('H4 (2024 Actual)', value=('H4' in growth_state['selected_cycles'])).props('dense dark color=amber').classes('text-xs text-amber-400 font-bold')
                            g_chk_bench = ui.checkbox('Promedio Histórico', value=('bench' in growth_state['selected_cycles'])).props('dense dark color=grey').classes('text-xs text-slate-300 font-medium')

                # 2. Tarjetas KPI
                with ui.row().classes('w-full grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5 mt-2'):
                    with ui.card().classes('bg-[#0f172a] border border-[#1e293b] p-3.5 rounded-xl shadow-sm'):
                        ui.label('GRANULARIDAD EVALUADA').classes('text-[10px] font-bold text-slate-400 font-mono tracking-wider')
                        ui.label(f"{summary.get('step_label', '1 Semestre')} ({summary.get('interval_days', 180)}d)").classes('text-lg font-black text-amber-400 font-mono')
                        ui.label(f"{len(periods)} períodos analizados post-Halving").classes('text-[10px] text-slate-400 font-mono')

                    with ui.card().classes('bg-[#0f172a] border border-[#1e293b] p-3.5 rounded-xl shadow-sm'):
                        ui.label('TASA GLOBAL DE CRECIMIENTO').classes('text-[10px] font-bold text-slate-400 font-mono tracking-wider')
                        win_r = summary.get('global_positive_ratio', 0)
                        ui.label(f"{win_r:.1f}%").classes('text-lg font-black text-emerald-400 font-mono')
                        ui.label('Períodos con variación Δ% positiva').classes('text-[10px] text-slate-400 font-mono')

                    with ui.card().classes('bg-[#0f172a] border border-[#1e293b] p-3.5 rounded-xl shadow-sm'):
                        ui.label('VARIACIÓN PERIÓDICA PROMEDIO').classes('text-[10px] font-bold text-slate-400 font-mono tracking-wider')
                        avg_r = summary.get('avg_period_return', 0)
                        color_avg = 'text-emerald-400' if avg_r >= 0 else 'text-rose-400'
                        ui.label(f"{avg_r:+.2f}%").classes(f'text-lg font-black {color_avg} font-mono')
                        ui.label('Media histórica por período').classes('text-[10px] text-slate-400 font-mono')

                    with ui.card().classes('bg-[#0f172a] border border-[#1e293b] p-3.5 rounded-xl shadow-sm'):
                        ui.label('MÁX RALLY VS MÁX CORRECCIÓN').classes('text-[10px] font-bold text-slate-400 font-mono tracking-wider')
                        max_r = summary.get('max_period_return', 0)
                        min_r = summary.get('min_period_return', 0)
                        with ui.row().classes('items-center gap-2'):
                            ui.label(f"+{max_r:.1f}%").classes('text-xs font-black text-emerald-400 font-mono')
                            ui.label('|').classes('text-slate-600')
                            ui.label(f"{min_r:.1f}%").classes('text-xs font-black text-rose-400 font-mono')
                        ui.label('Expansión máxima vs retroceso máximo').classes('text-[10px] text-slate-400 font-mono')

                # 3. Gráfico Plotly
                with ui.card().classes('w-full bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-md mt-2'):
                    fig_growth = build_growth_figure(growth_data, growth_state)
                    ui.plotly(fig_growth).classes('w-full h-full')

                # 4. Tabla y Resumen Analítico
                with ui.row().classes('w-full grid grid-cols-1 lg:grid-cols-3 gap-4 mt-2'):
                    with ui.card().classes('lg:col-span-2 bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-md overflow-x-auto'):
                        ui.label('DESGLOSE DE VARIACIÓN POR PERÍODO (Δ%)').classes('text-xs font-bold text-slate-300 font-mono tracking-wider mb-2')
                        ui.html(build_growth_table_html(growth_data, growth_state)).classes('w-full')

                    with ui.card().classes('bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-md'):
                        ui.label('INTERPRETACIÓN CUANTITATIVA').classes('text-xs font-bold text-amber-400 font-mono tracking-wider mb-2')
                        ui.html(build_growth_insights_html(growth_data, growth_state)).classes('w-full')

                # Handlers de reactividad para controles internos de growth
                def on_growth_asset_change(e):
                    growth_state['asset_type'] = e.value
                    render_growth_tab()

                def on_growth_tf_change(e):
                    growth_state['timeframe'] = e.value
                    render_growth_tab()

                def on_growth_step_change(e):
                    growth_state['step_size'] = int(e.value)
                    render_growth_tab()

                def on_growth_metric_change(e):
                    growth_state['metric'] = e.value
                    render_growth_tab()

                def on_growth_win_change(e):
                    growth_state['max_days'] = int(e.value)
                    render_growth_tab()

                def update_growth_cycles():
                    sel = []
                    if g_chk_h1.value: sel.append("H1")
                    if g_chk_h2.value: sel.append("H2")
                    if g_chk_h3.value: sel.append("H3")
                    if g_chk_h4.value: sel.append("H4")
                    if g_chk_bench.value: sel.append("bench")
                    growth_state['selected_cycles'] = sel
                    render_growth_tab()

                g_asset_select.on_value_change(on_growth_asset_change)
                g_tf_select.on_value_change(on_growth_tf_change)
                g_step_select.on_value_change(on_growth_step_change)
                g_metric_select.on_value_change(on_growth_metric_change)
                g_win_select.on_value_change(on_growth_win_change)
                g_chk_h1.on_value_change(lambda _: update_growth_cycles())
                g_chk_h2.on_value_change(lambda _: update_growth_cycles())
                g_chk_h3.on_value_change(lambda _: update_growth_cycles())
                g_chk_h4.on_value_change(lambda _: update_growth_cycles())
                g_chk_bench.on_value_change(lambda _: update_growth_cycles())

        # -------------------------------------------------------------
        # PESTAÑA: CAPITALIZACIÓN DE STABLECOINS POR HALVING
        # -------------------------------------------------------------
        stables_state = {
            "asset_type": "stablecoins",
            "timeframe": "semester",      # 'day', 'week', 'month', 'quarter', 'semester', 'year'
            "step_size": 1,
            "metric": "periodic_delta",   # 'periodic_delta', 'cumulative_pct', 'cumulative_mult', 'absolute_usd', 'dual_view', 'heatmap'
            "max_days": 1080,
            "selected_cycles": ["H2", "H3", "H4", "bench"],
            "y_scale": "linear"
        }

        def render_stables_tab():
            stables_container.clear()
            stables_kpis = analyzer.calculate_stablecoin_summary_kpis()
            stables_data = analyzer.calculate_periodic_growth_analysis(
                timeframe=stables_state["timeframe"],
                step_size=stables_state["step_size"],
                max_days=stables_state["max_days"],
                asset_type="stablecoins"
            )
            btc_data = analyzer.calculate_periodic_growth_analysis(
                timeframe=stables_state["timeframe"],
                step_size=stables_state["step_size"],
                max_days=stables_state["max_days"],
                asset_type="btc"
            )

            periods = stables_data.get("periods", [])
            summary = stables_data.get("summary", {})

            with stables_container:
                # 1. Header explicativo
                with ui.card().classes('w-full bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-md mt-3'):
                    with ui.row().classes('w-full justify-between items-center flex-wrap gap-2 border-b border-[#1e293b] pb-3 mb-3'):
                        with ui.column().classes('gap-0.5'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('account_balance_wallet', size='1.4rem').classes('text-emerald-400')
                                ui.label('CAPITALIZACIÓN DE STABLECOINS SEGÚN PERÍODOS DEL HALVING').classes('text-sm font-extrabold text-white font-mono tracking-wider')
                                ui.badge('LIQUIDEZ ON-CHAIN GLOBAL', color='teal').classes('text-[10px] font-mono font-bold')
                            ui.label('Monitoreo del poder adquisitivo latente (USDT, USDC, DAI...) indexado a partir del bloque de Halving (H=0). Analiza la tasa de inyección de capital en dólares y su correlación con los ciclos alcistas.').classes('text-xs text-slate-400')

                # 2. Tarjetas KPI de Liquidez Global
                with ui.row().classes('w-full grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5'):
                    with ui.card().classes('bg-[#0f172a] border border-emerald-500/30 p-3.5 rounded-xl shadow-sm'):
                        with ui.row().classes('w-full justify-between items-center mb-0.5'):
                            ui.label('CAPITALIZACIÓN TOTAL ACTUAL').classes('text-[10px] font-bold text-emerald-400 font-mono tracking-wider')
                            ui.icon('savings', size='1.1rem').classes('text-emerald-400')
                        ui.label(f"${stables_kpis.get('current_market_cap_billions', 0):,.2f}B").classes('text-2xl font-black text-white font-mono')
                        with ui.row().classes('items-baseline gap-1.5 mt-0.5'):
                            ui.label(f"+{stables_kpis.get('h4_growth_pct', 0):.1f}%").classes('text-xs font-black text-emerald-400 font-mono')
                            ui.label('desde Halving 4 (H0)').classes('text-[10px] text-slate-400 font-mono')

                    with ui.card().classes('bg-[#0f172a] border border-[#1e293b] p-3.5 rounded-xl shadow-sm'):
                        with ui.row().classes('w-full justify-between items-center mb-0.5'):
                            ui.label('INYECCIÓN NETA HALVING 4').classes('text-[10px] font-bold text-amber-400 font-mono tracking-wider')
                            ui.icon('add_circle', size='1.1rem').classes('text-amber-400')
                        ui.label(f"+${stables_kpis.get('h4_inflow_billions', 0):,.2f}B").classes('text-2xl font-black text-amber-400 font-mono')
                        ui.label(f"Dólares acuñados en {stables_kpis.get('h4_current_day', 0)} días").classes('text-[10px] text-slate-400 font-mono mt-0.5')

                    with ui.card().classes('bg-[#0f172a] border border-[#1e293b] p-3.5 rounded-xl shadow-sm'):
                        with ui.row().classes('w-full justify-between items-center mb-0.5'):
                            ui.label('PICO CICLO 3 (2020-2021)').classes('text-[10px] font-bold text-purple-400 font-mono tracking-wider')
                            ui.icon('trending_up', size='1.1rem').classes('text-purple-400')
                        ui.label(f"${stables_kpis.get('h3_peak_mcap_billions', 0):,.2f}B").classes('text-2xl font-black text-white font-mono')
                        with ui.row().classes('items-baseline gap-1.5 mt-0.5'):
                            ui.label(f"+{stables_kpis.get('h3_peak_roi_pct', 0):,.0f}%").classes('text-xs font-black text-purple-400 font-mono')
                            ui.label('expansión en ATH').classes('text-[10px] text-slate-400 font-mono')

                    with ui.card().classes('bg-[#0f172a] border border-[#1e293b] p-3.5 rounded-xl shadow-sm'):
                        with ui.row().classes('w-full justify-between items-center mb-0.5'):
                            ui.label('EXPANSIÓN SEMESTRE 1 PROMEDIO').classes('text-[10px] font-bold text-sky-400 font-mono tracking-wider')
                            ui.icon('speed', size='1.1rem').classes('text-sky-400')
                        ui.label(f"+{stables_kpis.get('avg_sem1_growth_pct', 0):.1f}%").classes('text-2xl font-black text-sky-400 font-mono')
                        ui.label('Media histórica en primeros 180 días').classes('text-[10px] text-slate-400 font-mono mt-0.5')

                # 3. Controles Interactivos
                with ui.card().classes('w-full bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-md'):
                    with ui.row().classes('w-full items-center justify-between flex-wrap gap-3'):
                        with ui.row().classes('items-center flex-wrap gap-2.5'):
                            ui.label('TEMPORALIDAD:').classes('text-[11px] font-bold text-slate-400 font-mono self-center')
                            s_tf_select = ui.select(
                                options={
                                    'semester': 'Semestre (180 días / 6M)',
                                    'quarter': 'Trimestre (90 días / 3M)',
                                    'month': 'Mes (30 días)',
                                    'week': 'Semana (7 días)',
                                    'year': 'Año (365 días / 12M)'
                                },
                                value=stables_state['timeframe']
                            ).props('dense outlined dark options-dense').classes('w-56 text-xs')

                            ui.label('CANTIDAD:').classes('text-[11px] font-bold text-slate-400 font-mono self-center ml-1')
                            s_step_select = ui.select(
                                options={
                                    1: '1 Período (Base)',
                                    2: '2 Períodos (x2)',
                                    3: '3 Períodos (x3)',
                                    4: '4 Períodos (x4)',
                                    6: '6 Períodos (x6)'
                                },
                                value=stables_state['step_size']
                            ).props('dense outlined dark options-dense').classes('w-40 text-xs')

                            ui.label('MÉTRICA:').classes('text-[11px] font-bold text-slate-400 font-mono self-center ml-1')
                            s_metric_select = ui.select(
                                options={
                                    'periodic_delta': 'Variación Periódica Δ% (Crecimiento / Decrecimiento)',
                                    'cumulative_pct': 'Expansión Acumulada (+% desde H0)',
                                    'cumulative_mult': 'Múltiplo de Liquidez (Nx Base)',
                                    'absolute_usd': 'Capitalización de Mercado ($ Billones USD)',
                                    'dual_view': 'Vista Dual (Barras Δ% + Curva Acumulada)',
                                    'heatmap': 'Mapa de Calor de Liquidez'
                                },
                                value=stables_state['metric']
                            ).props('dense outlined dark options-dense').classes('w-72 text-xs')

                        with ui.row().classes('items-center flex-wrap gap-2.5'):
                            ui.label('VENTANA:').classes('text-[11px] font-bold text-slate-400 font-mono self-center')
                            s_win_select = ui.select(
                                options={
                                    730: '2 Años (0 a +730d)',
                                    1080: 'Ciclo Estándar (0 a +1080d)',
                                    1400: 'Ciclo Extendido (0 a +1400d)'
                                },
                                value=stables_state['max_days']
                            ).props('dense outlined dark options-dense').classes('w-52 text-xs')

                    with ui.row().classes('w-full items-center justify-between flex-wrap gap-2 pt-2 border-t border-[#1e293b]/70 mt-1'):
                        with ui.row().classes('items-center gap-3 flex-wrap'):
                            ui.label('CICLOS:').classes('text-[11px] font-bold text-slate-400 font-mono self-center')
                            s_chk_h1 = ui.checkbox('H1 (2012 - Inexistente)', value=('H1' in stables_state['selected_cycles'])).props('dense dark color=cyan').classes('text-xs text-sky-400 font-semibold opacity-50')
                            s_chk_h2 = ui.checkbox('H2 (2016)', value=('H2' in stables_state['selected_cycles'])).props('dense dark color=purple').classes('text-xs text-purple-400 font-semibold')
                            s_chk_h3 = ui.checkbox('H3 (2020)', value=('H3' in stables_state['selected_cycles'])).props('dense dark color=green').classes('text-xs text-emerald-400 font-semibold')
                            s_chk_h4 = ui.checkbox('H4 (2024 Actual)', value=('H4' in stables_state['selected_cycles'])).props('dense dark color=amber').classes('text-xs text-amber-400 font-bold')
                            s_chk_bench = ui.checkbox('Promedio Histórico', value=('bench' in stables_state['selected_cycles'])).props('dense dark color=grey').classes('text-xs text-slate-300 font-medium')

                # 4. Gráfico Plotly de Capitalización de Stablecoins
                with ui.card().classes('w-full bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-md'):
                    fig_stables = build_growth_figure(stables_data, stables_state)
                    ui.plotly(fig_stables).classes('w-full h-full')

                # 5. Tablas Cuantitativas y Análisis
                with ui.row().classes('w-full grid grid-cols-1 lg:grid-cols-3 gap-4'):
                    with ui.card().classes('lg:col-span-2 bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-md overflow-x-auto'):
                        with ui.row().classes('w-full justify-between items-center mb-2'):
                            ui.label('DESGLOSE DE VARIACIÓN POR PERÍODO (Δ%)').classes('text-xs font-bold text-slate-300 font-mono tracking-wider')
                            ui.label('Tasa de cambio porcentual del Market Cap de Stablecoins').classes('text-[10px] text-slate-400 font-mono')
                        ui.html(build_growth_table_html(stables_data, stables_state)).classes('w-full')

                    with ui.card().classes('bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-md'):
                        ui.label('INTERPRETACIÓN DE LIQUIDEZ MACRO').classes('text-xs font-bold text-emerald-400 font-mono tracking-wider mb-2')
                        ui.html(build_growth_insights_html(stables_data, stables_state)).classes('w-full')

                # 6. Tabla Complementaria de Capitalización e Inyección en Dólares ($ Billones) y Acción de Precio BTC
                with ui.card().classes('w-full bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-md overflow-x-auto'):
                    with ui.row().classes('w-full justify-between items-center flex-wrap gap-2 mb-3'):
                        with ui.column().classes('gap-0.5'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('table_chart', size='1.2rem').classes('text-amber-400')
                                ui.label('DETALLE DE CAPITALIZACIÓN (MCAP STABLECOINS) Y ACCIÓN DE PRECIO (BTC)').classes('text-xs font-bold text-slate-200 font-mono tracking-wider')
                            ui.label('Valores iniciales vs finales, incremento/reducción en monto ($) y porcentaje (Δ%) para Capitalización de Stablecoins y Precio de Bitcoin por período.').classes('text-[10px] text-slate-400 font-mono')
                        with ui.row().classes('items-center gap-3 bg-[#0a0e17] px-3 py-1.5 rounded-lg border border-[#1e293b]'):
                            with ui.row().classes('items-center gap-1.5'):
                                ui.icon('water_drop', size='0.9rem').classes('text-emerald-400')
                                ui.label('Mcap Stablecoins').classes('text-[10.5px] font-mono text-emerald-400 font-bold')
                            with ui.row().classes('items-center gap-1.5 ml-2'):
                                ui.icon('currency_bitcoin', size='0.9rem').classes('text-amber-400')
                                ui.label('Precio Bitcoin').classes('text-[10.5px] font-mono text-amber-400 font-bold')

                    ui.html(build_stables_detailed_table_html(stables_data, btc_data)).classes('w-full')

                # Handlers de reactividad para controles internos de stables
                def on_stables_tf_change(e):
                    stables_state['timeframe'] = e.value
                    render_stables_tab()

                def on_stables_step_change(e):
                    stables_state['step_size'] = int(e.value)
                    render_stables_tab()

                def on_stables_metric_change(e):
                    stables_state['metric'] = e.value
                    render_stables_tab()

                def on_stables_win_change(e):
                    stables_state['max_days'] = int(e.value)
                    render_stables_tab()

                def update_stables_cycles():
                    sel = []
                    if s_chk_h1.value: sel.append("H1")
                    if s_chk_h2.value: sel.append("H2")
                    if s_chk_h3.value: sel.append("H3")
                    if s_chk_h4.value: sel.append("H4")
                    if s_chk_bench.value: sel.append("bench")
                    stables_state['selected_cycles'] = sel
                    render_stables_tab()

                s_tf_select.on_value_change(on_stables_tf_change)
                s_step_select.on_value_change(on_stables_step_change)
                s_metric_select.on_value_change(on_stables_metric_change)
                s_win_select.on_value_change(on_stables_win_change)
                s_chk_h1.on_value_change(lambda _: update_stables_cycles())
                s_chk_h2.on_value_change(lambda _: update_stables_cycles())
                s_chk_h3.on_value_change(lambda _: update_stables_cycles())
                s_chk_h4.on_value_change(lambda _: update_stables_cycles())
                s_chk_bench.on_value_change(lambda _: update_stables_cycles())

        # -------------------------------------------------------------
        # PESTAÑA: BACKTESTING CUANTITATIVO (STABLECOINS + EMA)
        # -------------------------------------------------------------
        bt_state = {
            "initial_capital": 10000.0,
            "ema_fast": 20,
            "ema_slow": 50,
            "ema_trend": 100,
            "trend_mode": "price_above", # 'price_above', 'crossover', 'both'
            "flow_window": 14,
            "z_window": 60,
            "z_entry_threshold": 1.0,
            "z_exit_threshold": -0.5,
            "halving_filter_enabled": True,
            "min_post_halving_days": 150,
            "max_post_halving_days": 550,
            "stop_loss_pct": 12.0,
            "take_profit_pct": 60.0,
            "trailing_stop": False,
            "commission_pct": 0.1,
            "slippage_pct": 0.05,
            "last_results": None
        }
        bt_engine = StablecoinBacktester()

        def build_trades_table_html(trades_df: pd.DataFrame) -> str:
            if trades_df.empty:
                return "<div class='text-xs text-slate-500 font-mono py-6 text-center'>No se ejecutaron operaciones en el histórico con los parámetros seleccionados.</div>"

            html = '''
            <table class="w-full text-left text-xs font-mono border-collapse">
                <thead>
                    <tr class="border-b border-[#1e293b] text-slate-400 text-[11px]">
                        <th class="py-2.5 px-2.5">#</th>
                        <th class="py-2.5 px-2.5">Fecha Entrada</th>
                        <th class="py-2.5 px-2.5">Fecha Salida</th>
                        <th class="py-2.5 px-2.5">Días Halving</th>
                        <th class="py-2.5 px-2 text-right">Precio Compra</th>
                        <th class="py-2.5 px-2 text-right">Precio Venta</th>
                        <th class="py-2.5 px-2 text-right">Duración</th>
                        <th class="py-2.5 px-2.5 text-right">PnL ($)</th>
                        <th class="py-2.5 px-2.5 text-right">PnL (%)</th>
                        <th class="py-2.5 px-3 text-right">Motivo de Salida</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-[#1e293b]/60">
            '''

            for _, t in trades_df.iterrows():
                pnl = t['pnl_usd']
                pnl_pct = t['pnl_pct']
                pnl_color = "text-emerald-400" if pnl >= 0 else "text-rose-400"
                pnl_bg = "bg-emerald-950/60 border-emerald-800/60" if pnl >= 0 else "bg-rose-950/60 border-rose-800/60"
                h_days = f"H+{int(t['entry_halving_day'])}d → H+{int(t['exit_halving_day'])}d"

                html += f'''
                    <tr class="hover:bg-slate-800/40 transition-colors">
                        <td class="py-2 px-2.5 font-bold text-slate-400">{int(t['trade_id'])}</td>
                        <td class="py-2 px-2.5 text-slate-300">{t['entry_date']}</td>
                        <td class="py-2 px-2.5 text-slate-300">{t['exit_date']}</td>
                        <td class="py-2 px-2.5 text-slate-400 text-[10.5px]">{h_days}</td>
                        <td class="py-2 px-2 text-right text-slate-200">${t['entry_price']:,.2f}</td>
                        <td class="py-2 px-2 text-right text-slate-200">${t['exit_price']:,.2f}</td>
                        <td class="py-2 px-2 text-right text-slate-400">{int(t['holding_days'])}d</td>
                        <td class="py-2 px-2.5 text-right font-bold {pnl_color}">{'+' if pnl >= 0 else ''}${pnl:+,.2f}</td>
                        <td class="py-2 px-2.5 text-right font-extrabold {pnl_color}">
                            <span class="px-1.5 py-0.5 rounded border {pnl_bg}">{'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%</span>
                        </td>
                        <td class="py-2 px-3 text-right text-slate-400 text-[10.5px]">{t['exit_reason']}</td>
                    </tr>
                '''

            html += '</tbody></table>'
            return html

        def render_backtest_tab():
            backtest_container.clear()

            # Si no hay resultados calculados previamente, ejecutar backtest inicial
            if bt_state["last_results"] is None:
                bt_state["last_results"] = bt_engine.run_backtest(
                    initial_capital=bt_state["initial_capital"],
                    commission_pct=bt_state["commission_pct"],
                    slippage_pct=bt_state["slippage_pct"],
                    ema_fast=bt_state["ema_fast"],
                    ema_slow=bt_state["ema_slow"],
                    ema_trend=bt_state["ema_trend"],
                    trend_mode=bt_state["trend_mode"],
                    flow_window=bt_state["flow_window"],
                    z_window=bt_state["z_window"],
                    z_entry_threshold=bt_state["z_entry_threshold"],
                    z_exit_threshold=bt_state["z_exit_threshold"],
                    halving_filter_enabled=bt_state["halving_filter_enabled"],
                    min_post_halving_days=bt_state["min_post_halving_days"],
                    max_post_halving_days=bt_state["max_post_halving_days"],
                    stop_loss_pct=bt_state["stop_loss_pct"],
                    take_profit_pct=bt_state["take_profit_pct"],
                    trailing_stop=bt_state["trailing_stop"]
                )

            res = bt_state["last_results"]
            m = res["metrics"]
            df_trades = res.get("trades", pd.DataFrame())

            with backtest_container:
                # 1. Header explicativo
                with ui.card().classes('w-full bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-md mt-3'):
                    with ui.row().classes('w-full justify-between items-center flex-wrap gap-2 border-b border-[#1e293b] pb-3 mb-3'):
                        with ui.column().classes('gap-0.5'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('psychology', size='1.4rem').classes('text-amber-400')
                                ui.label('BACKTESTING: STABLECOIN EMISSION & EMA MOMENTUM (SHM)').classes('text-sm font-extrabold text-white font-mono tracking-wider')
                                ui.badge('SISTEMA CUANTITATIVO ON-CHAIN', color='amber').classes('text-[10px] font-mono font-bold')
                            ui.label('Simula y evalúa sistemáticamente la estrategia de impulso por emisión neta de Stablecoins (Z-Score), filtros de tendencia EMA y ventanas de aceleración post-Halving.').classes('text-xs text-slate-400')

                # 2. Panel de Configuración de Parámetros
                with ui.card().classes('w-full bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-md'):
                    ui.label('PARÁMETROS DE LA ESTRATEGIA Y GESTIÓN DE RIESGO').classes('text-xs font-bold text-slate-300 font-mono tracking-wider mb-3')
                    
                    with ui.row().classes('w-full grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3'):
                        # Sección Tendencia
                        with ui.column().classes('gap-1.5 p-3 bg-[#0a0e17] rounded-lg border border-[#1e293b]'):
                            ui.label('📈 TENDENCIA (EMA)').classes('text-[11px] font-bold text-sky-400 font-mono')
                            bt_fast_in = ui.select(
                                options={10: 'EMA 10 (Rápida)', 20: 'EMA 20 (Estándar)', 30: 'EMA 30'},
                                value=bt_state['ema_fast'],
                                label='EMA Rápida'
                            ).props('dense outlined dark').classes('w-full text-xs')
                            
                            bt_slow_in = ui.select(
                                options={35: 'EMA 35', 50: 'EMA 50 (Estándar)', 65: 'EMA 65', 100: 'EMA 100'},
                                value=bt_state['ema_slow'],
                                label='EMA Lenta'
                            ).props('dense outlined dark').classes('w-full text-xs')

                            bt_mode_in = ui.select(
                                options={'price_above': 'Precio > EMA Lenta', 'crossover': 'Cruce EMA Rápida > Lenta', 'both': 'Ambas Condiciones'},
                                value=bt_state['trend_mode'],
                                label='Modo Tendencia'
                            ).props('dense outlined dark').classes('w-full text-xs')

                        # Sección Liquidez Stablecoins
                        with ui.column().classes('gap-1.5 p-3 bg-[#0a0e17] rounded-lg border border-[#1e293b]'):
                            ui.label('💧 LIQUIDEZ ON-CHAIN').classes('text-[11px] font-bold text-emerald-400 font-mono')
                            bt_flow_in = ui.select(
                                options={7: '7 Días (1 Sem)', 14: '14 Días (2 Sem)', 21: '21 Días', 30: '30 Días (1 Mes)'},
                                value=bt_state['flow_window'],
                                label='Ventana Flujo'
                            ).props('dense outlined dark').classes('w-full text-xs')

                            bt_zwin_in = ui.select(
                                options={30: '30 Días', 60: '60 Días (Estándar)', 90: '90 Días', 120: '120 Días'},
                                value=bt_state['z_window'],
                                label='Ventana Z-Score'
                            ).props('dense outlined dark').classes('w-full text-xs')

                            bt_zin_in = ui.select(
                                options={0.5: '+0.5σ (Sensible)', 0.8: '+0.8σ', 1.0: '+1.0σ (Estándar)', 1.2: '+1.2σ', 1.5: '+1.5σ (Fuerte)', 2.0: '+2.0σ'},
                                value=bt_state['z_entry_threshold'],
                                label='Z-Score Entrada'
                            ).props('dense outlined dark').classes('w-full text-xs')

                            bt_zout_in = ui.select(
                                options={0.0: '0.0σ (Neutro)', -0.3: '-0.3σ', -0.5: '-0.5σ (Estándar)', -0.8: '-0.8σ', -1.0: '-1.0σ (Contracción)'},
                                value=bt_state['z_exit_threshold'],
                                label='Z-Score Salida'
                            ).props('dense outlined dark').classes('w-full text-xs')

                        # Sección Filtro Halving
                        with ui.column().classes('gap-1.5 p-3 bg-[#0a0e17] rounded-lg border border-[#1e293b]'):
                            ui.label('⏳ RÉGIMEN HALVING').classes('text-[11px] font-bold text-amber-400 font-mono')
                            bt_hfilter_in = ui.checkbox('Activar Filtro Halving', value=bt_state['halving_filter_enabled']).props('dense dark color=amber').classes('text-xs font-bold text-amber-400 mt-1')
                            
                            with ui.row().classes('w-full items-center gap-2 mt-1'):
                                bt_hmin_in = ui.number('Inicio (H+)', value=bt_state['min_post_halving_days'], min=0, max=1000).props('dense outlined dark').classes('w-1/2 text-xs')
                                bt_hmax_in = ui.number('Fin (H+)', value=bt_state['max_post_halving_days'], min=100, max=1200).props('dense outlined dark').classes('w-1/2 text-xs')
                            ui.label('Ventana histórica recomendada: H+150d a H+550d').classes('text-[10px] text-slate-500 font-mono')

                        # Sección Gestión de Riesgo y Capital
                        with ui.column().classes('gap-1.5 p-3 bg-[#0a0e17] rounded-lg border border-[#1e293b]'):
                            ui.label('🛡️ GESTIÓN DE RIESGO').classes('text-[11px] font-bold text-rose-400 font-mono')
                            with ui.row().classes('w-full items-center gap-2'):
                                bt_sl_in = ui.select(
                                    options={0.0: 'Desactivado', 8.0: '8% SL', 10.0: '10% SL', 12.0: '12% SL', 15.0: '15% SL'},
                                    value=bt_state['stop_loss_pct'],
                                    label='Stop Loss'
                                ).props('dense outlined dark').classes('w-1/2 text-xs')
                                
                                bt_tp_in = ui.select(
                                    options={0.0: 'Desactivado', 30.0: '30% TP', 45.0: '45% TP', 60.0: '60% TP', 100.0: '100% TP'},
                                    value=bt_state['take_profit_pct'],
                                    label='Take Profit'
                                ).props('dense outlined dark').classes('w-1/2 text-xs')

                            bt_cap_in = ui.number('Capital Inicial ($ USD)', value=bt_state['initial_capital'], min=100, step=1000).props('dense outlined dark').classes('w-full text-xs')
                            bt_trail_in = ui.checkbox('Trailing Stop', value=bt_state['trailing_stop']).props('dense dark color=teal').classes('text-xs font-semibold text-slate-300')

                    # Botón Ejecutar
                    with ui.row().classes('w-full justify-end items-center gap-3 mt-3 pt-3 border-t border-[#1e293b]'):
                        bt_spinner = ui.spinner(size='1.3rem').classes('text-amber-400')
                        bt_spinner.set_visibility(False)
                        bt_run_btn = ui.button(
                            'EJECUTAR SIMULACIÓN DE BACKTEST',
                            icon='play_arrow'
                        ).classes('bg-gradient-to-r from-amber-500 to-emerald-500 text-slate-950 font-black px-6 py-2 rounded-xl text-xs shadow-md font-mono hover:brightness-110')

                # 3. Tarjetas KPI de Resultados
                with ui.row().classes('w-full grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3.5'):
                    with ui.card().classes('bg-[#0f172a] border border-emerald-500/30 p-3.5 rounded-xl shadow-sm'):
                        ui.label('CAPITAL FINAL & RETORNO').classes('text-[10px] font-bold text-emerald-400 font-mono tracking-wider')
                        ui.label(f"${m['final_capital']:,.2f}").classes('text-xl font-black text-white font-mono')
                        with ui.row().classes('items-baseline gap-1 mt-0.5'):
                            ret_col = 'text-emerald-400' if m['total_return_pct'] >= 0 else 'text-rose-400'
                            ui.label(f"{'+' if m['total_return_pct'] >= 0 else ''}{m['total_return_pct']:,.1f}%").classes(f'text-xs font-black {ret_col} font-mono')
                            ui.label(f"(B&H: +{m['bnh_return_pct']:,.0f}%)").classes('text-[10px] text-slate-500 font-mono')

                    with ui.card().classes('bg-[#0f172a] border border-[#1e293b] p-3.5 rounded-xl shadow-sm'):
                        ui.label('CAGR ANUALIZADO').classes('text-[10px] font-bold text-sky-400 font-mono tracking-wider')
                        cagr_col = 'text-sky-400' if m['cagr_pct'] >= 0 else 'text-rose-400'
                        ui.label(f"{'+' if m['cagr_pct'] >= 0 else ''}{m['cagr_pct']:.2f}%").classes(f'text-xl font-black {cagr_col} font-mono')
                        ui.label(f"{m['total_days']} días simulados").classes('text-[10px] text-slate-400 font-mono mt-0.5')

                    with ui.card().classes('bg-[#0f172a] border border-[#1e293b] p-3.5 rounded-xl shadow-sm'):
                        ui.label('RATIOS SHARPE & SORTINO').classes('text-[10px] font-bold text-purple-400 font-mono tracking-wider')
                        ui.label(f"{m['sharpe_ratio']} | {m['sortino_ratio']}").classes('text-xl font-black text-purple-400 font-mono')
                        ui.label(f"Calmar Ratio: {m['calmar_ratio']}").classes('text-[10px] text-slate-400 font-mono mt-0.5')

                    with ui.card().classes('bg-[#0f172a] border border-[#1e293b] p-3.5 rounded-xl shadow-sm'):
                        ui.label('MAX DRAWDOWN').classes('text-[10px] font-bold text-rose-400 font-mono tracking-wider')
                        ui.label(f"{m['max_drawdown_pct']:.1f}%").classes('text-xl font-black text-rose-400 font-mono')
                        ui.label('Caída máxima de capital').classes('text-[10px] text-slate-400 font-mono mt-0.5')

                    with ui.card().classes('bg-[#0f172a] border border-[#1e293b] p-3.5 rounded-xl shadow-sm'):
                        ui.label('WIN RATE & PROFIT FACTOR').classes('text-[10px] font-bold text-amber-400 font-mono tracking-wider')
                        ui.label(f"{m['win_rate_pct']:.1f}%").classes('text-xl font-black text-amber-400 font-mono')
                        ui.label(f"PF: {m['profit_factor']} ({m['winning_trades']}W / {m['losing_trades']}L de {m['total_trades']})").classes('text-[10px] text-slate-400 font-mono mt-0.5')

                # 4. Gráfico Plotly de 3 Paneles Sincronizados
                with ui.card().classes('w-full bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-md'):
                    fig_bt = bt_engine.build_backtest_figure(res)
                    ui.plotly(fig_bt).classes('w-full')

                # 5. Tabla de Operaciones (Trades Log)
                with ui.card().classes('w-full bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-md overflow-x-auto'):
                    with ui.row().classes('w-full justify-between items-center mb-2'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('receipt_long', size='1.2rem').classes('text-emerald-400')
                            ui.label('REGISTRO DETALLADO DE OPERACIONES (TRADES LOG)').classes('text-xs font-bold text-slate-200 font-mono tracking-wider')
                        ui.label(f"{len(df_trades)} operaciones completadas").classes('text-[10px] text-slate-400 font-mono')
                    ui.html(build_trades_table_html(df_trades)).classes('w-full')

                # 6. Interpretación Cuantitativa de la Estrategia
                with ui.card().classes('w-full bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-md'):
                    ui.label('INTERPRETACIÓN Y EDGE CUANTITATIVO DE LA ESTRATEGIA').classes('text-xs font-bold text-amber-400 font-mono tracking-wider mb-2')
                    ui.html('''
                    <div class="space-y-2 text-xs text-slate-300 leading-relaxed font-sans">
                        <div><b>1. Filtro de Asimetría de Liquidez:</b> Al exigir un <b>Z-Score ≥ +1.0σ</b> en la emisión neta de stablecoins, el sistema evita entradas en consolidaciones sin volumen y sólo participa cuando se detecta inyección fresca de capital institucional hacia el ecosistema.</div>
                        <div><b>2. Protección de Capital en Bear Markets:</b> Al restringir las compras a la ventana de aceleración post-Halving (<b>H+150d a H+550d</b>) y ejecutar salidas automáticas cuando el flujo de stablecoins se contrae (<b>Z-Score ≤ -0.5σ</b>), la estrategia permanece 100% en liquidez durante las caídas de más del -70% del ciclo bajista.</div>
                        <div><b>3. Adaptabilidad:</b> Puedes ajustar los umbrales de Stop Loss y Take Profit o dejar que las salidas sean 100% dinámicas según la pérdida de la EMA rápida y el agotamiento del flujo de stablecoins.</div>
                    </div>
                    ''').classes('w-full')

                # Handler de ejecución interactiva
                async def on_execute_backtest():
                    bt_spinner.set_visibility(True)
                    bt_run_btn.disable()
                    ui.notify('Ejecutando simulación de backtest...', type='info', position='top-right')

                    # Actualizar estado desde los inputs
                    bt_state["initial_capital"] = float(bt_cap_in.value or 10000.0)
                    bt_state["ema_fast"] = int(bt_fast_in.value or 20)
                    bt_state["ema_slow"] = int(bt_slow_in.value or 50)
                    bt_state["trend_mode"] = str(bt_mode_in.value or 'price_above')
                    bt_state["flow_window"] = int(bt_flow_in.value or 14)
                    bt_state["z_window"] = int(bt_zwin_in.value or 60)
                    bt_state["z_entry_threshold"] = float(bt_zin_in.value or 1.0)
                    bt_state["z_exit_threshold"] = float(bt_zout_in.value or -0.5)
                    bt_state["halving_filter_enabled"] = bool(bt_hfilter_in.value)
                    bt_state["min_post_halving_days"] = int(bt_hmin_in.value or 150)
                    bt_state["max_post_halving_days"] = int(bt_hmax_in.value or 550)
                    bt_state["stop_loss_pct"] = float(bt_sl_in.value or 0.0)
                    bt_state["take_profit_pct"] = float(bt_tp_in.value or 0.0)
                    bt_state["trailing_stop"] = bool(bt_trail_in.value)

                    try:
                        loop = asyncio.get_event_loop()
                        bt_state["last_results"] = await loop.run_in_executor(
                            None,
                            lambda: bt_engine.run_backtest(
                                initial_capital=bt_state["initial_capital"],
                                commission_pct=bt_state["commission_pct"],
                                slippage_pct=bt_state["slippage_pct"],
                                ema_fast=bt_state["ema_fast"],
                                ema_slow=bt_state["ema_slow"],
                                ema_trend=bt_state["ema_trend"],
                                trend_mode=bt_state["trend_mode"],
                                flow_window=bt_state["flow_window"],
                                z_window=bt_state["z_window"],
                                z_entry_threshold=bt_state["z_entry_threshold"],
                                z_exit_threshold=bt_state["z_exit_threshold"],
                                halving_filter_enabled=bt_state["halving_filter_enabled"],
                                min_post_halving_days=bt_state["min_post_halving_days"],
                                max_post_halving_days=bt_state["max_post_halving_days"],
                                stop_loss_pct=bt_state["stop_loss_pct"],
                                take_profit_pct=bt_state["take_profit_pct"],
                                trailing_stop=bt_state["trailing_stop"]
                            )
                        )
                        ui.notify('¡Simulación de backtest completada!', type='positive', position='top-right')
                        render_backtest_tab()
                    except Exception as ex:
                        ui.notify(f'Error en backtest: {ex}', type='negative', position='top-right')
                    finally:
                        bt_spinner.set_visibility(False)
                        bt_run_btn.enable()

                bt_run_btn.on_click(on_execute_backtest)

        def render_horizons_tab():
            horizons_container.clear()
            metrics = analyzer.calculate_cycle_metrics()

            with horizons_container:
                with ui.card().classes('w-full bg-[#111827] border border-[#1e293b] p-5 rounded-xl mt-3'):
                    ui.label('RENDIMIENTOS POR HORIZONTE TEMPORAL POST-HALVING').classes('text-xs font-bold text-slate-400 font-mono tracking-wider mb-2')
                    ui.label('Comparación del retorno porcentual acumulado de cada ciclo en días clave: 30, 60, 90, 180, 365, 500, 730 días y al Pico de Ciclo (ATH).').classes('text-xs text-slate-400 mb-4')

                    horizons_keys = ["H+30d", "H+60d", "H+90d", "H+180d", "H+365d", "H+500d", "H+730d", "Pico ATH"]
                    
                    fig = go.Figure()

                    for m in metrics:
                        h_name = m['name']
                        h_color = m['color']
                        h_returns = m['horizon_returns']
                        
                        y_vals = []
                        for hk in horizons_keys:
                            if hk == "Pico ATH":
                                y_vals.append(m['peak_return_pct'])
                            elif hk in h_returns and h_returns[hk] is not None:
                                y_vals.append(h_returns[hk]['return_pct'])
                            else:
                                y_vals.append(None)

                        fig.add_trace(go.Bar(
                            x=horizons_keys,
                            y=y_vals,
                            name=h_name,
                            marker_color=h_color,
                            hovertemplate=(
                                f"<b>{h_name}</b><br>"
                                + "Horizonte: %{x}<br>"
                                + "Retorno: <b>+%{y:,.1f}%</b><extra></extra>"
                            )
                        ))

                    fig.update_layout(
                        paper_bgcolor='#111827',
                        plot_bgcolor='#0a0e17',
                        font=dict(family='Plus Jakarta Sans, sans-serif', color='#94a3b8', size=11),
                        height=440,
                        barmode='group',
                        margin=dict(l=50, r=20, t=25, b=75),
                        legend=dict(
                            orientation='h',
                            yanchor='top',
                            y=-0.16,
                            xanchor='center',
                            x=0.5,
                            bgcolor='rgba(15, 23, 42, 0.95)',
                            bordercolor='#1e293b',
                            borderwidth=1,
                            font=dict(size=11, color='#e2e8f0', family='JetBrains Mono')
                        ),
                        xaxis=dict(gridcolor='#1e293b', tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10)),
                        yaxis=dict(
                            title='Retorno Porcentual (%) — Escala Log',
                            type='log',
                            gridcolor='#1e293b',
                            ticksuffix='%',
                            tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10)
                        )
                    )
                    ui.plotly(fig).classes('w-full h-full')

        def render_correlation_tab():
            corr_container.clear()
            corrs = analyzer.calculate_correlation_matrix()

            with corr_container:
                with ui.row().classes('w-full grid grid-cols-1 lg:grid-cols-2 gap-4 mt-3'):
                    
                    # Panel Izquierdo: Heatmap de Pearson
                    with ui.card().classes('bg-[#111827] border border-[#1e293b] p-5 rounded-xl shadow-md'):
                        ui.label('MATRIZ DE CORRELACIÓN DE PEARSON').classes('text-xs font-bold text-slate-400 font-mono tracking-wider mb-1')
                        ui.label('Correlación lineal de las trayectorias normalizadas de precio entre todos los ciclos evaluados en la misma ventana de días post-Halving.').classes('text-xs text-slate-400 mb-3')

                        labels = [f"Halving {l[1]}" for l in corrs['labels']]
                        z_vals = corrs['pearson_matrix']

                        fig_heat = go.Figure(data=go.Heatmap(
                            z=z_vals,
                            x=labels,
                            y=labels,
                            colorscale='Viridis',
                            text=[[f"<b>{val:.3f}</b>" for val in row] for row in z_vals],
                            texttemplate="%{text}",
                            textfont=dict(family='JetBrains Mono', size=12, color='#ffffff'),
                            colorbar=dict(title=dict(text='Coef. Pearson (r)', font=dict(color='#cbd5e1', size=10)), tickfont=dict(family='JetBrains Mono', color='#cbd5e1'))
                        ))

                        fig_heat.update_layout(
                            paper_bgcolor='#111827',
                            plot_bgcolor='#0a0e17',
                            font=dict(family='Plus Jakarta Sans, sans-serif', color='#94a3b8'),
                            height=360,
                            margin=dict(l=50, r=20, t=20, b=40),
                            xaxis=dict(tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=11)),
                            yaxis=dict(tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=11))
                        )
                        ui.plotly(fig_heat).classes('w-full h-full')

                    # Panel Derecho: Similitud del Ciclo 4 con Ciclos Previos
                    with ui.card().classes('bg-[#111827] border border-[#1e293b] p-5 rounded-xl shadow-md'):
                        ui.label('SIMILITUD DEL CICLO ACTUAL (H4) CON CICLOS PREVIOS').classes('text-xs font-bold text-slate-400 font-mono tracking-wider mb-1')
                        ui.label('Nivel de acoplamiento de la trayectoria actual frente a los ciclos anteriores en el mismo punto relativo de maduración.').classes('text-xs text-slate-400 mb-4')

                        sims = corrs.get('similarity_scores', {})
                        for h_prev, data in sims.items():
                            h_num = h_prev[1]
                            corr_val = data['correlation']
                            sim_pct = data['similarity_pct']
                            overlap = data['overlap_days']
                            
                            with ui.column().classes('w-full bg-[#0a0e17] p-3 rounded-lg border border-[#1e293b] mb-2.5'):
                                with ui.row().classes('w-full justify-between items-center'):
                                    with ui.row().classes('items-center gap-2'):
                                        ui.badge(f'H4 vs Halving {h_num}', color='amber' if h_num == '2' else 'indigo').classes('font-mono font-bold text-xs')
                                        ui.label(f'{overlap} días evaluados').classes('text-[11px] text-slate-500 font-mono')
                                    ui.label(f'r = {corr_val:.3f} ({sim_pct:.1f}%)').classes('text-xs font-mono font-bold text-emerald-400')
                                
                                # Barra de progreso visual
                                ui.linear_progress(value=max(0.0, min(1.0, corr_val)), show_value=False).props('color=amber rounded stripe size=8px').classes('mt-2')

        def render_decay_tab():
            decay_container.clear()
            decay_data = analyzer.calculate_diminishing_returns_model()
            metrics = analyzer.calculate_cycle_metrics()

            with decay_container:
                with ui.row().classes('w-full grid grid-cols-1 lg:grid-cols-2 gap-4 mt-3'):
                    
                    # Panel Izquierdo: Gráfico de Decaimiento de Múltiplos
                    with ui.card().classes('bg-[#111827] border border-[#1e293b] p-5 rounded-xl shadow-md'):
                        ui.label('CURVA DE RENDIMIENTOS DECRECIENTES').classes('text-xs font-bold text-slate-400 font-mono tracking-wider mb-1')
                        ui.label('Múltiplo máximo alcanzado en cada ciclo de Halving vs modelo exponencial de maduración de mercado.').classes('text-xs text-slate-400 mb-3')

                        completed_m = [m for m in metrics if m["is_completed"]]
                        cycles = [1, 2, 3]
                        mults = [m["peak_multiplier"] for m in completed_m]

                        fig_decay = go.Figure()
                        
                        # Puntos reales
                        fig_decay.add_trace(go.Scatter(
                            x=cycles,
                            y=mults,
                            mode='markers+text',
                            name='Múltiplo Real Alcanzado',
                            text=[f"<b>{m:.1f}x</b>" for m in mults],
                            textposition='top right',
                            textfont=dict(family='JetBrains Mono', color='#ffffff', size=12),
                            marker=dict(size=12, color=['#38bdf8', '#a855f7', '#10b981'], symbol='circle')
                        ))

                        # Curva ajustada y proyección a Ciclo 4
                        extended_cycles = np.linspace(1, 4.2, 50)
                        log_mults = np.log(mults)
                        coeffs = np.polyfit(cycles, log_mults, 1)
                        fitted_y = np.exp(coeffs[0] * extended_cycles + coeffs[1])

                        fig_decay.add_trace(go.Scatter(
                            x=extended_cycles,
                            y=fitted_y,
                            mode='lines',
                            name='Ajuste Exponencial (Trend)',
                            line=dict(color='#f59e0b', width=2, dash='dash')
                        ))

                        # Punto Proyectado Ciclo 4
                        h4_proj_mult = np.exp(coeffs[0] * 4 + coeffs[1])
                        fig_decay.add_trace(go.Scatter(
                            x=[4],
                            y=[h4_proj_mult],
                            mode='markers+text',
                            name='Proyección Halving 4',
                            text=[f"<b>Est: {h4_proj_mult:.1f}x</b>"],
                            textposition='top right',
                            textfont=dict(family='JetBrains Mono', color='#fbbf24', size=12),
                            marker=dict(size=14, color='#f59e0b', symbol='star')
                        ))

                        fig_decay.update_layout(
                            paper_bgcolor='#111827',
                            plot_bgcolor='#0a0e17',
                            font=dict(family='Plus Jakarta Sans, sans-serif', color='#94a3b8'),
                            height=360,
                            margin=dict(l=50, r=20, t=20, b=40),
                            xaxis=dict(
                                title='Ciclo de Halving',
                                tickvals=[1, 2, 3, 4],
                                ticktext=['Halving 1 (2012)', 'Halving 2 (2016)', 'Halving 3 (2020)', 'Halving 4 (2024)'],
                                gridcolor='#1e293b',
                                tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10)
                            ),
                            yaxis=dict(
                                title='Múltiplo de Crecimiento (Log)',
                                type='log',
                                gridcolor='#1e293b',
                                ticksuffix='x',
                                tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10)
                            )
                        )
                        ui.plotly(fig_decay).classes('w-full h-full')

                    # Panel Derecho: Escenarios de Proyección para Halving 4
                    with ui.card().classes('bg-[#111827] border border-[#1e293b] p-5 rounded-xl shadow-md'):
                        ui.label('ESCENARIOS DE PRECIO PARA HALVING 4').classes('text-xs font-bold text-slate-400 font-mono tracking-wider mb-1')
                        ui.label('Proyecciones de objetivo basadas en el precio inicial del Halving 4 (~$63,843 USD).').classes('text-xs text-slate-400 mb-4')

                        scenarios = decay_data.get('scenarios', {})
                        for key, sc in scenarios.items():
                            badge_color = 'teal' if key == 'base_model' else ('cyan' if key == 'bullish' else 'amber')
                            with ui.column().classes('w-full bg-[#0a0e17] p-3.5 rounded-lg border border-[#1e293b] mb-3'):
                                with ui.row().classes('w-full justify-between items-center mb-1'):
                                    ui.label(sc['name']).classes('text-xs font-bold text-white font-mono')
                                    ui.badge(f"{sc['multiplier']}x ({sc['estimated_roi_pct']:+.0f}%)", color=badge_color).classes('font-mono font-bold text-xs')
                                
                                ui.label(f"Target: ${sc['target_price']:,.2f} USD").classes('text-base font-black text-amber-400 font-mono my-0.5')
                                ui.label(sc['desc']).classes('text-[11px] text-slate-400 leading-tight')

        def render_table_tab():
            table_container.clear()
            metrics = analyzer.calculate_cycle_metrics()

            with table_container:
                with ui.card().classes('w-full bg-[#111827] border border-[#1e293b] p-5 rounded-xl mt-3 overflow-x-auto'):
                    ui.label('DESGLOSE CUANTITATIVO COMPARATIVO DE HALVINGS').classes('text-xs font-bold text-slate-400 font-mono tracking-wider mb-3')

                    html_table = '''
                    <table class="w-full text-left text-xs font-mono border-collapse">
                        <thead>
                            <tr class="border-b border-[#1e293b] text-slate-400 text-[11px]">
                                <th class="py-2.5 px-3">Ciclo</th>
                                <th class="py-2.5 px-3">Fecha Halving</th>
                                <th class="py-2.5 px-3">Recompensa</th>
                                <th class="py-2.5 px-3 text-right">Precio Halving</th>
                                <th class="py-2.5 px-3 text-right">Rally Pre-Halving</th>
                                <th class="py-2.5 px-3 text-right">Pico de Ciclo (ATH)</th>
                                <th class="py-2.5 px-3 text-right">Múltiplo Máx</th>
                                <th class="py-2.5 px-3 text-right">Días al Pico</th>
                                <th class="py-2.5 px-3 text-right">H+180d</th>
                                <th class="py-2.5 px-3 text-right">H+365d</th>
                                <th class="py-2.5 px-3 text-right">Max Drawdown</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-[#1e293b]/60">
                    '''

                    for m in metrics:
                        h_ret = m['horizon_returns']
                        h180 = f"+{h_ret.get('H+180d', {}).get('return_pct', 0):.1f}%" if h_ret.get('H+180d') else "-"
                        h365 = f"+{h_ret.get('H+365d', {}).get('return_pct', 0):.1f}%" if h_ret.get('H+365d') else "-"
                        peak_str = f"${m['peak_price']:,.2f}" if m['peak_price'] else "-"
                        peak_mult_str = f"{m['peak_multiplier']:.2f}x"
                        badge_status = "" if m['is_completed'] else " <span class='text-amber-400 font-bold'>(En curso)</span>"

                        html_table += f'''
                            <tr class="hover:bg-slate-800/40 transition-colors">
                                <td class="py-3 px-3 font-bold text-white"><span style="color: {m['color']}">■</span> {m['name']}{badge_status}</td>
                                <td class="py-3 px-3 text-slate-300">{m['halving_date']}</td>
                                <td class="py-3 px-3 text-slate-400">{m['reward_reduction']}</td>
                                <td class="py-3 px-3 text-right font-bold text-slate-200">${m['halving_price']:,.2f}</td>
                                <td class="py-3 px-3 text-right text-emerald-400 font-bold">+{m['pre_halving_rally_pct']:,.1f}%</td>
                                <td class="py-3 px-3 text-right font-bold text-white">{peak_str}</td>
                                <td class="py-3 px-3 text-right font-extrabold text-amber-400">{peak_mult_str}</td>
                                <td class="py-3 px-3 text-right text-slate-300">{m['days_to_peak']} d</td>
                                <td class="py-3 px-3 text-right text-slate-300">{h180}</td>
                                <td class="py-3 px-3 text-right text-slate-300">{h365}</td>
                                <td class="py-3 px-3 text-right text-rose-400 font-bold">{m['max_drawdown_post_cycle']:.1f}%</td>
                            </tr>
                        '''

                    html_table += '</tbody></table>'
                    ui.html(html_table).classes('w-full')

        # -------------------------------------------------------------
        # EVENT LISTENERS
        # -------------------------------------------------------------

        def on_scale_change(e):
            state['scale_mode'] = e.value
            render_main_chart()

        def on_y_axis_change(e):
            state['y_axis_type'] = e.value
            render_main_chart()

        def on_window_change(e):
            state['time_window'] = e.value
            render_main_chart()

        def update_selected_cycles():
            sel = []
            if chk_h1.value: sel.append("H1")
            if chk_h2.value: sel.append("H2")
            if chk_h3.value: sel.append("H3")
            if chk_h4.value: sel.append("H4")
            if chk_bench.value: sel.append("bench")
            state['selected_cycles'] = sel
            render_main_chart()

        async def on_refresh():
            loading_spinner.set_visibility(True)
            refresh_btn.disable()
            ui.notify('Sincronizando datos históricos de Halvings y Stablecoins...', type='info', position='top-right')
            
            try:
                # Ejecutar descarga en segundo plano para BTC y Stablecoins
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: (
                        analyzer.fetch_historical_btc_data(force_refresh=True),
                        analyzer.fetch_historical_stablecoin_data(force_refresh=True)
                    )
                )
                
                # Re-renderizar todos los componentes
                render_kpi_cards()
                render_main_chart()
                render_growth_tab()
                render_stables_tab()
                render_backtest_tab()
                render_horizons_tab()
                render_correlation_tab()
                render_decay_tab()
                render_table_tab()
                ui.notify('¡Datos de Halvings y Stablecoins actualizados con éxito!', type='positive', position='top-right')
            except Exception as ex:
                ui.notify(f'Error actualizando datos: {ex}', type='negative', position='top-right')
            finally:
                loading_spinner.set_visibility(False)
                refresh_btn.enable()

        scale_select.on_value_change(on_scale_change)
        y_axis_select.on_value_change(on_y_axis_change)
        window_select.on_value_change(on_window_change)
        chk_h1.on_value_change(lambda _: update_selected_cycles())
        chk_h2.on_value_change(lambda _: update_selected_cycles())
        chk_h3.on_value_change(lambda _: update_selected_cycles())
        chk_h4.on_value_change(lambda _: update_selected_cycles())
        chk_bench.on_value_change(lambda _: update_selected_cycles())
        refresh_btn.on_click(on_refresh)

        # Render inicial de todas las vistas
        render_kpi_cards()
        render_main_chart()
        render_growth_tab()
        render_stables_tab()
        render_backtest_tab()
        render_horizons_tab()
        render_correlation_tab()
        render_decay_tab()
        render_table_tab()

    return {
        "analyzer": analyzer,
        "refresh": on_refresh
    }
