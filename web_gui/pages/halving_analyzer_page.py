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
            tab_horizons = ui.tab('horizons', label='Rendimientos por Horizonte', icon='bar_chart')
            tab_corr = ui.tab('correlation', label='Matriz de Correlación y Similitud', icon='grid_view')
            tab_decay = ui.tab('decay', label='Decaimiento y Proyecciones', icon='auto_graph')
            tab_table = ui.tab('table', label='Tabla Cuantitativa Detallada', icon='table_chart')

        with ui.tab_panels(tabs, value='horizons').classes('w-full bg-transparent p-0'):
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

                    # Estructura de tabla HTML personalizada con Bloomberg Obsidian Styling
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
        render_horizons_tab()
        render_correlation_tab()
        render_decay_tab()
        render_table_tab()

    return {
        "analyzer": analyzer,
        "refresh": on_refresh
    }
