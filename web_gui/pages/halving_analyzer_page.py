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
            tab_horizons = ui.tab('horizons', label='Rendimientos por Horizonte', icon='bar_chart')
            tab_corr = ui.tab('correlation', label='Matriz de Correlación y Similitud', icon='grid_view')
            tab_decay = ui.tab('decay', label='Decaimiento y Proyecciones', icon='auto_graph')
            tab_table = ui.tab('table', label='Tabla Cuantitativa Detallada', icon='table_chart')

        with ui.tab_panels(tabs, value='growth').classes('w-full bg-transparent p-0'):
            with ui.tab_panel('growth'):
                growth_container = ui.column().classes('w-full')

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
        growth_state = {
            "timeframe": "month",         # 'day', 'week', 'month', 'quarter', 'semester', 'year'
            "step_size": 1,               # 1, 2, 3, 4, 6, 12...
            "metric": "periodic_delta",   # 'periodic_delta', 'cumulative_pct', 'cumulative_mult', 'dual_view', 'heatmap'
            "max_days": 1000,
            "selected_cycles": ["H1", "H2", "H3", "H4", "bench"],
            "y_scale": "linear"
        }

        cycle_meta_map = {
            "H1": {"name": "Halving 1 (2012)", "color": "#38bdf8"},
            "H2": {"name": "Halving 2 (2016)", "color": "#a855f7"},
            "H3": {"name": "Halving 3 (2020)", "color": "#10b981"},
            "H4": {"name": "Halving 4 (2024 Actual)", "color": "#f59e0b"},
            "bench": {"name": "Promedio Histórico (H1-H3)", "color": "#94a3b8"}
        }

        def build_growth_figure(growth_data: dict, g_state: dict) -> go.Figure:
            periods = growth_data.get("periods", [])
            cycles = growth_data.get("cycles", {})
            benchmark = growth_data.get("benchmark", [])
            metric = g_state.get("metric", "periodic_delta")
            sel_cycles = g_state.get("selected_cycles", ["H1", "H2", "H3", "H4", "bench"])
            y_scale = g_state.get("y_scale", "linear")

            x_labels = [p["label_short"] for p in periods]
            x_full_labels = [p["label_full"] for p in periods]

            if metric == "dual_view":
                fig = make_subplots(
                    rows=2, cols=1,
                    shared_xaxes=True,
                    vertical_spacing=0.10,
                    row_heights=[0.55, 0.45],
                    subplot_titles=[
                        'Variación Periódica Δ% (Crecimiento / Decrecimiento por Intervalo)',
                        'Crecimiento Acumulado (% sobre precio inicial del Halving)'
                    ]
                )

                for cid in ["H1", "H2", "H3", "H4"]:
                    if cid in sel_cycles and cid in cycles:
                        c_list = cycles[cid]
                        c_info = cycle_meta_map[cid]
                        y_delta = [p["periodic_return_pct"] for p in c_list]
                        y_cum = [p["cumulative_return_pct"] for p in c_list]
                        
                        customdata = [
                            [p.get("start_price", 0), p.get("end_price", 0), p.get("cumulative_return_pct", 0), p.get("cumulative_multiplier", 1.0), x_full_labels[i] if i < len(x_full_labels) else ""]
                            for i, p in enumerate(c_list)
                        ]

                        fig.add_trace(
                            go.Bar(
                                x=x_labels,
                                y=y_delta,
                                name=f"{c_info['name']} (Δ%)",
                                marker_color=c_info["color"],
                                customdata=customdata,
                                hovertemplate=(
                                    f"<b>{c_info['name']}</b><br>"
                                    "Período: %{customdata[4]}<br>"
                                    "Inicio: $%{customdata[0]:,.2f} | Fin: $%{customdata[1]:,.2f}<br>"
                                    "Variación Período: <b>%{y:+.2f}%</b><extra></extra>"
                                ),
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
                                    "Período: %{customdata[4]}<br>"
                                    "Retorno Acumulado: <b>+%{y:,.1f}%</b> (%{customdata[3]:.2f}x)<extra></extra>"
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
                            [p.get("end_price", 0), p.get("cumulative_multiplier", 1.0), x_full_labels[i] if i < len(x_full_labels) else ""]
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
                                "Período: %{customdata[2]}<br>"
                                "Precio: $%{customdata[0]:,.2f}<br>"
                                "Crecimiento Acumulado: <b>+%{y:,.1f}%</b> (%{customdata[1]:.2f}x)<extra></extra>"
                            )
                        ))

                if "bench" in sel_cycles and benchmark:
                    y_b_cum = [b["mean_cumulative_pct"] for b in benchmark]
                    fig.add_trace(go.Scatter(
                        x=x_labels,
                        y=y_b_cum,
                        mode='lines+markers',
                        name="Promedio Histórico (H1-H3)",
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
                        title='Retorno Acumulado (%)',
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
                            [p.get("end_price", 0), p.get("cumulative_return_pct", 0), x_full_labels[i] if i < len(x_full_labels) else ""]
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
                                "Período: %{customdata[2]}<br>"
                                "Precio: $%{customdata[0]:,.2f}<br>"
                                "Múltiplo: <b>%{y:.2f}x</b> (+%{customdata[1]:,.1f}%)<extra></extra>"
                            )
                        ))

                if "bench" in sel_cycles and benchmark:
                    y_b_mult = [b["mean_cumulative_multiplier"] for b in benchmark]
                    fig.add_trace(go.Scatter(
                        x=x_labels,
                        y=y_b_mult,
                        mode='lines+markers',
                        name="Promedio Histórico (H1-H3)",
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
                        title='Múltiplo Normalizado (P_t / P_H0)',
                        type='log' if y_scale == 'log' else 'linear',
                        gridcolor='#1e293b',
                        ticksuffix='x',
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
                            [p.get("start_price", 0), p.get("end_price", 0), p.get("cumulative_return_pct", 0), p.get("cumulative_multiplier", 1.0), p.get("is_in_progress", False), x_full_labels[i] if i < len(x_full_labels) else ""]
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
                                "Período: %{customdata[5]}<br>"
                                "Inicio: $%{customdata[0]:,.2f} | Cierre: $%{customdata[1]:,.2f}<br>"
                                "Variación Periódica: <b>%{y:+.2f}%</b><br>"
                                "Crecimiento Acumulado: +%{customdata[2]:,.1f}% (%{customdata[3]:.2f}x)<extra></extra>"
                            )
                        ))

                if "bench" in sel_cycles and benchmark:
                    y_b_delta = [b["mean_periodic_pct"] for b in benchmark]
                    fig.add_trace(go.Bar(
                        x=x_labels,
                        y=y_b_delta,
                        name="Promedio Histórico (H1-H3)",
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
                        title='Variación Periódica (Δ% Crecimiento / Decrecimiento)',
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
                    if cid not in cycles or idx >= len(cycles[cid]):
                        return "<span class='text-slate-600'>-</span>"
                    c_period = cycles[cid][idx]
                    if not c_period.get("has_data") or c_period.get("periodic_return_pct") is None:
                        return "<span class='text-slate-600'>-</span>"
                    val = c_period["periodic_return_pct"]
                    in_prog = " <span class='text-amber-400 text-[9px]'>*</span>" if c_period.get("is_in_progress") else ""
                    if val > 0:
                        return f"<span class='text-emerald-400 font-bold'>+{val:.1f}%{in_prog}</span>"
                    elif val < 0:
                        return f"<span class='text-rose-400 font-bold'>{val:.1f}%{in_prog}</span>"
                    else:
                        return f"<span class='text-slate-400'>0.0%{in_prog}</span>"

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

        def build_growth_insights_html(growth_data: dict, g_state: dict) -> str:
            summary = growth_data.get("summary", {})
            tf_name = summary.get("timeframe_label", "Mes")
            step_name = summary.get("step_label", "1 Mes")
            win_r = summary.get("global_positive_ratio", 0)
            avg_r = summary.get("avg_period_return", 0)
            max_r = summary.get("max_period_return", 0)
            min_r = summary.get("min_period_return", 0)

            # Extraer rendimiento más reciente de H4
            h4_periods = growth_data.get("cycles", {}).get("H4", [])
            h4_active = [p for p in h4_periods if p.get("has_data") and p.get("periodic_return_pct") is not None]
            h4_last_ret = h4_active[-1]["periodic_return_pct"] if h4_active else 0
            h4_last_label = growth_data.get("periods", [])[len(h4_active)-1]["label_short"] if h4_active else "-"

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
                max_days=growth_state["max_days"]
            )

            periods = growth_data.get("periods", [])
            summary = growth_data.get("summary", {})

            with growth_container:
                # 1. Panel de Controles
                with ui.card().classes('w-full bg-[#111827] border border-[#1e293b] p-4 rounded-xl shadow-md mt-3'):
                    with ui.row().classes('w-full justify-between items-center flex-wrap gap-2 border-b border-[#1e293b] pb-3 mb-3'):
                        with ui.column().classes('gap-0.5'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('query_stats', size='1.3rem').classes('text-amber-400')
                                ui.label('CRECIMIENTO / DECRECIMIENTO POR TEMPORALIDAD').classes('text-xs font-extrabold text-white font-mono tracking-wider')
                            ui.label('Selecciona la temporalidad (Día, Semana, Mes, Trimestre, Semestre, Año) y cantidad de períodos para comparar la variación periódica (Δ%) o acumulada entre ciclos.').classes('text-[11px] text-slate-400')

                    with ui.row().classes('w-full items-center justify-between flex-wrap gap-3'):
                        # Selectores de Temporalidad y Cantidad
                        with ui.row().classes('items-center flex-wrap gap-2.5'):
                            ui.label('TEMPORALIDAD:').classes('text-[11px] font-bold text-slate-400 font-mono self-center')
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
                            ).props('dense outlined dark options-dense').classes('w-56 text-xs')

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
                            ).props('dense outlined dark options-dense').classes('w-44 text-xs')

                            ui.label('MÉTRICA:').classes('text-[11px] font-bold text-slate-400 font-mono self-center ml-1')
                            g_metric_select = ui.select(
                                options={
                                    'periodic_delta': 'Variación Periódica Δ% (Crecimiento / Decrecimiento)',
                                    'cumulative_pct': 'Crecimiento Acumulado (+% desde H0)',
                                    'cumulative_mult': 'Múltiplo Acumulado (Nx)',
                                    'dual_view': 'Vista Dual (Barras Δ% + Curva Acumulada)',
                                    'heatmap': 'Mapa de Calor (Matriz de Retornos)'
                                },
                                value=growth_state['metric']
                            ).props('dense outlined dark options-dense').classes('w-72 text-xs')

                        # Ventana y Escala
                        with ui.row().classes('items-center flex-wrap gap-2.5'):
                            ui.label('VENTANA:').classes('text-[11px] font-bold text-slate-400 font-mono self-center')
                            g_win_select = ui.select(
                                options={
                                    365: 'Primer Año (0 a +365d)',
                                    730: '2 Años (0 a +730d)',
                                    1000: 'Ciclo Estándar (0 a +1000d)',
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
                        ui.label(f"{summary.get('step_label', '1 Mes')} ({summary.get('interval_days', 30)}d)").classes('text-lg font-black text-amber-400 font-mono')
                        ui.label(f"{len(periods)} períodos analizados post-Halving").classes('text-[10px] text-slate-400 font-mono')

                    with ui.card().classes('bg-[#0f172a] border border-[#1e293b] p-3.5 rounded-xl shadow-sm'):
                        ui.label('TASA GLOBAL DE CRECIMIENTO').classes('text-[10px] font-bold text-slate-400 font-mono tracking-wider')
                        win_r = summary.get('global_positive_ratio', 0)
                        ui.label(f"{win_r:.1f}%").classes('text-lg font-black text-emerald-400 font-mono')
                        ui.label('Períodos con variación Δ% positiva').classes('text-[10px] text-slate-400 font-mono')

                    with ui.card().classes('bg-[#0f172a] border border-[#1e293b] p-3.5 rounded-xl shadow-sm'):
                        ui.label('RETORNO PERIÓDICO PROMEDIO').classes('text-[10px] font-bold text-slate-400 font-mono tracking-wider')
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
                        ui.label('Rally máximo vs retroceso máximo').classes('text-[10px] text-slate-400 font-mono')

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

                g_tf_select.on_value_change(on_growth_tf_change)
                g_step_select.on_value_change(on_growth_step_change)
                g_metric_select.on_value_change(on_growth_metric_change)
                g_win_select.on_value_change(on_growth_win_change)
                g_chk_h1.on_value_change(lambda _: update_growth_cycles())
                g_chk_h2.on_value_change(lambda _: update_growth_cycles())
                g_chk_h3.on_value_change(lambda _: update_growth_cycles())
                g_chk_h4.on_value_change(lambda _: update_growth_cycles())
                g_chk_bench.on_value_change(lambda _: update_growth_cycles())

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
            ui.notify('Sincronizando datos históricos de Halvings...', type='info', position='top-right')
            
            try:
                # Ejecutar descarga en segundo plano
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, lambda: analyzer.fetch_historical_btc_data(force_refresh=True))
                
                # Re-renderizar todos los componentes
                render_kpi_cards()
                render_main_chart()
                render_growth_tab()
                render_horizons_tab()
                render_correlation_tab()
                render_decay_tab()
                render_table_tab()
                ui.notify('¡Datos de Halvings actualizados con éxito!', type='positive', position='top-right')
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
        render_horizons_tab()
        render_correlation_tab()
        render_decay_tab()
        render_table_tab()

    return {
        "analyzer": analyzer,
        "refresh": on_refresh
    }
