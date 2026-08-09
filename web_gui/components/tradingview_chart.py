import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from typing import Dict, Any, Optional

def calculate_indicators(df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    Calcula los indicadores técnicos solicitados sobre una copia del DataFrame de mercado.
    """
    df_calc = df.copy()
    
    if df_calc.empty or 'close' not in df_calc.columns:
        return df_calc

    close = df_calc['close']
    high = df_calc['high']
    low = df_calc['low']

    # 1. Medias Móviles Simples (SMA)
    for key, val in config.items():
        if key.startswith('sma_') and val:
            try:
                p = int(key.split('_')[1])
                df_calc[f'SMA_{p}'] = close.rolling(window=p).mean()
            except ValueError:
                pass

    # 2. Medias Móviles Exponenciales (EMA)
    for key, val in config.items():
        if key.startswith('ema_') and val:
            try:
                p = int(key.split('_')[1])
                df_calc[f'EMA_{p}'] = close.ewm(span=p, adjust=False).mean()
            except ValueError:
                pass

    # 3. Bandas de Bollinger (20, 2 std)
    if config.get('bollinger', False):
        period = int(config.get('bollinger_period', 20))
        std_dev = float(config.get('bollinger_std', 2.0))
        bb_middle = close.rolling(window=period).mean()
        bb_std = close.rolling(window=period).std()
        df_calc['BB_upper'] = bb_middle + (bb_std * std_dev)
        df_calc['BB_middle'] = bb_middle
        df_calc['BB_lower'] = bb_middle - (bb_std * std_dev)

    # 4. VWAP (Volume Weighted Average Price)
    if config.get('vwap', False) and 'volume' in df_calc.columns:
        typical_price = (high + low + close) / 3.0
        tpv = typical_price * df_calc['volume']
        cum_tpv = tpv.cumsum()
        cum_vol = df_calc['volume'].cumsum()
        df_calc['VWAP'] = np.where(cum_vol > 0, cum_tpv / cum_vol, np.nan)

    # 5. ATR (Average True Range)
    if config.get('atr', False) or config.get('supertrend', False):
        period = int(config.get('atr_period', 14))
        high_low = high - low
        high_close = (high - close.shift()).abs()
        low_close = (low - close.shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df_calc['ATR'] = tr.rolling(window=period).mean()

    # 6. SuperTrend
    if config.get('supertrend', False):
        period = int(config.get('supertrend_period', 10))
        multiplier = float(config.get('supertrend_multiplier', 3.0))
        
        if 'ATR' not in df_calc.columns:
            high_low = high - low
            high_close = (high - close.shift()).abs()
            low_close = (low - close.shift()).abs()
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            atr_s = tr.rolling(window=period).mean()
        else:
            atr_s = df_calc['ATR']
            
        hl2 = (high + low) / 2.0
        basic_upper = hl2 + (multiplier * atr_s)
        basic_lower = hl2 - (multiplier * atr_s)
        
        st_upper = np.zeros(len(df_calc))
        st_lower = np.zeros(len(df_calc))
        st_dir = np.ones(len(df_calc))
        st_line = np.zeros(len(df_calc))
        
        close_arr = close.values
        b_up = basic_upper.values
        b_low = basic_lower.values
        
        for i in range(1, len(df_calc)):
            if np.isnan(b_up[i]) or np.isnan(b_low[i]):
                continue
                
            # Trailing Upper Band
            if b_up[i] < st_upper[i-1] or close_arr[i-1] > st_upper[i-1]:
                st_upper[i] = b_up[i]
            else:
                st_upper[i] = st_upper[i-1]
                
            # Trailing Lower Band
            if b_low[i] > st_lower[i-1] or close_arr[i-1] < st_lower[i-1]:
                st_lower[i] = b_low[i]
            else:
                st_lower[i] = st_lower[i-1]
                
            # Direction
            if st_dir[i-1] == 1:
                if close_arr[i] < st_lower[i]:
                    st_dir[i] = -1
                    st_line[i] = st_upper[i]
                else:
                    st_dir[i] = 1
                    st_line[i] = st_lower[i]
            else:
                if close_arr[i] > st_upper[i]:
                    st_dir[i] = 1
                    st_line[i] = st_lower[i]
                else:
                    st_dir[i] = -1
                    st_line[i] = st_upper[i]
                    
        df_calc['SuperTrend'] = np.where(st_line == 0, np.nan, st_line)
        df_calc['SuperTrend_Dir'] = st_dir

    # 7. RSI (Relative Strength Index)
    if config.get('rsi', False):
        period = int(config.get('rsi_period', 14))
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()
        rs = np.where(loss != 0, gain / loss, np.nan)
        df_calc['RSI'] = 100.0 - (100.0 / (1.0 + rs))

    # 8. MACD
    if config.get('macd', False):
        fast = int(config.get('macd_fast', 12))
        slow = int(config.get('macd_slow', 26))
        signal = int(config.get('macd_signal', 9))
        
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        df_calc['MACD_line'] = ema_fast - ema_slow
        df_calc['MACD_signal'] = df_calc['MACD_line'].ewm(span=signal, adjust=False).mean()
        df_calc['MACD_hist'] = df_calc['MACD_line'] - df_calc['MACD_signal']

    return df_calc


def build_tradingview_plotly_figure(
    df: pd.DataFrame,
    trades_df: Optional[pd.DataFrame] = None,
    config: Optional[Dict[str, Any]] = None,
    symbol: str = "BTC/USDT",
    timeframe: str = "1d",
    uirevision: Any = True
) -> go.Figure:
    """
    Construye una figura Plotly profesional estilo TradingView / NinjaTrader 8 con tema oscuro,
    indicadores configurables y señales de entrada/salida de trades.
    """
    if config is None:
        config = {
            'sma_20': True,
            'ema_9': True,
            'rsi': True,
            'show_trades': True,
            'show_trade_lines': True,
            'show_trade_labels': True,
            'trade_filter': 'ALL'
        }

    if df.empty:
        fig = go.Figure()
        fig.add_annotation(text="Sin datos disponibles para graficar", showarrow=False, font=dict(size=18, color="#94a3b8"))
        fig.update_layout(template="plotly_dark", paper_bgcolor="#0e131f", plot_bgcolor="#0e131f")
        return fig

    # Calcular indicadores sobre el DataFrame
    df_calc = calculate_indicators(df, config)
    
    # Conservar el rango completo de velas solicitado para el backtest (hasta 8000 velas)
    if len(df_calc) > 8000:
        df_calc = df_calc.tail(8000)

    # Formatear fechas para eje X
    if not isinstance(df_calc.index, pd.DatetimeIndex):
        try:
            df_calc.index = pd.to_datetime(df_calc.index)
        except Exception:
            pass

    x_dates = [str(x)[:19] for x in df_calc.index]

    # Determinación de filas del subgráfico (Panel Principal, Oscilador Secundario, Volumen)
    has_oscillator = any([config.get('rsi'), config.get('macd'), config.get('atr')])
    has_volume = 'volume' in df_calc.columns and df_calc['volume'].sum() > 0

    if has_oscillator and has_volume:
        rows = 3
        row_heights = [0.60, 0.22, 0.18]
        osc_row = 2
        vol_row = 3
    elif has_oscillator and not has_volume:
        rows = 2
        row_heights = [0.72, 0.28]
        osc_row = 2
        vol_row = None
    elif not has_oscillator and has_volume:
        rows = 2
        row_heights = [0.80, 0.20]
        osc_row = None
        vol_row = 2
    else:
        rows = 1
        row_heights = [1.0]
        osc_row = None
        vol_row = None

    fig = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=row_heights
    )

    # ── 1. Velas Japonesas (Candlesticks) ──
    fig.add_trace(
        go.Candlestick(
            x=x_dates,
            open=df_calc['open'],
            high=df_calc['high'],
            low=df_calc['low'],
            close=df_calc['close'],
            name=f"{symbol} ({timeframe})",
            increasing_line_color='#089981',
            increasing_fillcolor='#089981',
            decreasing_line_color='#f23645',
            decreasing_fillcolor='#f23645',
            hoverlabel=dict(bgcolor="#1e293b", font_size=12)
        ),
        row=1, col=1
    )

    # ── 2. Overlays de Indicadores en el gráfico principal ──
    preset_colors = {
        'SMA_20': '#3b82f6', 'SMA_50': '#8b5cf6', 'SMA_200': '#ec4899',
        'EMA_9': '#f59e0b', 'EMA_21': '#10b981', 'EMA_50': '#06b6d4', 'EMA_200': '#6366f1',
        'EMA_1': '#f43f5e', 'EMA_5': '#fb7185', 'EMA_10': '#38bdf8', 'EMA_12': '#f59e0b',
        'EMA_26': '#a855f7', 'EMA_30': '#10b981'
    }
    color_palette = [
        '#3b82f6', '#f59e0b', '#10b981', '#a855f7', '#06b6d4',
        '#ec4899', '#f97316', '#eab308', '#f43f5e', '#84cc16', '#6366f1'
    ]

    palette_idx = 0
    for col_name in df_calc.columns:
        col_upper = col_name.upper()
        if col_upper.startswith(('SMA_', 'EMA_')):
            cfg_key = col_name.lower()
            if config.get(cfg_key, True):
                color = preset_colors.get(col_upper, color_palette[palette_idx % len(color_palette)])
                palette_idx += 1
                fig.add_trace(
                    go.Scatter(
                        x=x_dates,
                        y=df_calc[col_name],
                        mode='lines',
                        name=col_upper,
                        line=dict(color=color, width=1.5)
                    ),
                    row=1, col=1
                )

    # Bandas de Bollinger
    if 'BB_upper' in df_calc.columns and 'BB_lower' in df_calc.columns:
        fig.add_trace(
            go.Scatter(x=x_dates, y=df_calc['BB_upper'], mode='lines', name='Bollinger Sup', line=dict(color='rgba(148, 163, 184, 0.5)', width=1, dash='dash')),
            row=1, col=1
        )
        fig.add_trace(
            go.Scatter(
                x=x_dates, y=df_calc['BB_lower'], mode='lines', name='Bollinger Inf',
                line=dict(color='rgba(148, 163, 184, 0.5)', width=1, dash='dash'),
                fill='tonexty', fillcolor='rgba(148, 163, 184, 0.08)'
            ),
            row=1, col=1
        )

    # VWAP
    if 'VWAP' in df_calc.columns:
        fig.add_trace(
            go.Scatter(x=x_dates, y=df_calc['VWAP'], mode='lines', name='VWAP', line=dict(color='#eab308', width=2, dash='dot')),
            row=1, col=1
        )

    # SuperTrend
    if 'SuperTrend' in df_calc.columns:
        fig.add_trace(
            go.Scatter(
                x=x_dates, y=df_calc['SuperTrend'], mode='lines', name='SuperTrend',
                line=dict(color='#10b981', width=2)
            ),
            row=1, col=1
        )

    # ── 3. Overlays de Trades (Entradas / Salidas / Conexiones) ──
    if config.get('show_trades', True) and trades_df is not None and not trades_df.empty:
        # Filtrar trades para que solo se grafiquen los que están dentro de la ventana visible de velas (últimas 3000)
        try:
            min_date = df_calc.index.min()
            max_date = df_calc.index.max()
            trades_df['entry_time'] = pd.to_datetime(trades_df['entry_time'])
            visible_trades = trades_df[(trades_df['entry_time'] >= min_date) & (trades_df['entry_time'] <= max_date)]
        except Exception:
            visible_trades = trades_df

        t_filter = config.get('trade_filter', 'ALL')
        show_lines = config.get('show_trade_lines', True)
        show_labels = config.get('show_trade_labels', True)

        long_e_x, long_e_y, long_e_text, long_e_hover = [], [], [], []
        short_e_x, short_e_y, short_e_text, short_e_hover = [], [], [], []
        win_x_x, win_x_y, win_x_text, win_x_hover = [], [], [], []
        loss_x_x, loss_x_y, loss_x_text, loss_x_hover = [], [], [], []
        
        line_win_x, line_win_y = [], []
        line_loss_x, line_loss_y = [], []

        for _, t in visible_trades.iterrows():
            pnl = float(t.get('pnl', 0) or 0)
            side = str(t.get('side', 'LONG')).upper()
            is_win = pnl > 0

            # Aplicar filtro de trades
            if t_filter == 'WINS' and not is_win: continue
            if t_filter == 'LOSSES' and is_win: continue
            if t_filter == 'LONG' and side != 'LONG': continue
            if t_filter == 'SHORT' and side != 'SHORT': continue

            try:
                e_time = str(pd.to_datetime(t.get('entry_time')))[:19]
                x_time = str(pd.to_datetime(t.get('exit_time')))[:19]
                e_price = float(t.get('entry_price', 0))
                x_price = float(t.get('exit_price', 0))
                exit_reason = str(t.get('exit_reason', 'Señal'))
            except Exception:
                continue

            entry_text = f"Entrada {side}<br>Precio: {e_price:,.2f}"
            pnl_pct = 0.0
            if e_price > 0:
                pnl_pct = (x_price - e_price) / e_price * 100.0 if side == 'LONG' else (e_price - x_price) / e_price * 100.0
            
            label_str = f"{'+' if pnl_pct > 0 else ''}{pnl_pct:.2f}%"
            hover_str = f"Salida {side} ({exit_reason})<br>Precio: {x_price:,.2f}<br>PnL: {pnl:+,.2f} ({pnl_pct:+.2f}%)"

            if side == 'LONG':
                long_e_x.append(e_time); long_e_y.append(e_price)
                long_e_text.append("Buy"); long_e_hover.append(entry_text)
            else:
                short_e_x.append(e_time); short_e_y.append(e_price)
                short_e_text.append("Sell"); short_e_hover.append(entry_text)

            if is_win:
                win_x_x.append(x_time); win_x_y.append(x_price)
                win_x_text.append(label_str); win_x_hover.append(hover_str)
                line_win_x.extend([e_time, x_time, None])
                line_win_y.extend([e_price, x_price, None])
            else:
                loss_x_x.append(x_time); loss_x_y.append(x_price)
                loss_x_text.append(label_str); loss_x_hover.append(hover_str)
                line_loss_x.extend([e_time, x_time, None])
                line_loss_y.extend([e_price, x_price, None])

        # Agregar Traces (Batching)
        if long_e_x:
            fig.add_trace(go.Scatter(x=long_e_x, y=long_e_y, mode='markers+text' if show_labels else 'markers', name="Entradas Long",
                marker=dict(symbol='triangle-up', size=13, color='#22c55e', line=dict(color='#ffffff', width=1)),
                text=long_e_text, textposition="bottom center", textfont=dict(color='#22c55e', size=10),
                hoverinfo="text", hovertext=long_e_hover, showlegend=False), row=1, col=1)
        if short_e_x:
            fig.add_trace(go.Scatter(x=short_e_x, y=short_e_y, mode='markers+text' if show_labels else 'markers', name="Entradas Short",
                marker=dict(symbol='triangle-down', size=13, color='#ef4444', line=dict(color='#ffffff', width=1)),
                text=short_e_text, textposition="top center", textfont=dict(color='#ef4444', size=10),
                hoverinfo="text", hovertext=short_e_hover, showlegend=False), row=1, col=1)
        if win_x_x:
            fig.add_trace(go.Scatter(x=win_x_x, y=win_x_y, mode='markers+text' if show_labels else 'markers', name="Salidas Win",
                marker=dict(symbol='circle', size=11, color='#06b6d4', line=dict(color='#ffffff', width=1)),
                text=win_x_text, textposition="top center", textfont=dict(color='#06b6d4', size=9),
                hoverinfo="text", hovertext=win_x_hover, showlegend=False), row=1, col=1)
        if loss_x_x:
            fig.add_trace(go.Scatter(x=loss_x_x, y=loss_x_y, mode='markers+text' if show_labels else 'markers', name="Salidas Loss",
                marker=dict(symbol='x', size=11, color='#f43f5e', line=dict(color='#ffffff', width=1)),
                text=loss_x_text, textposition="bottom center", textfont=dict(color='#f43f5e', size=9),
                hoverinfo="text", hovertext=loss_x_hover, showlegend=False), row=1, col=1)
        if show_lines and line_win_x:
            fig.add_trace(go.Scatter(x=line_win_x, y=line_win_y, mode='lines', line=dict(color='#22c55e', width=1.5, dash='dot'), hoverinfo='none', showlegend=False), row=1, col=1)
        if show_lines and line_loss_x:
            fig.add_trace(go.Scatter(x=line_loss_x, y=line_loss_y, mode='lines', line=dict(color='#ef4444', width=1.5, dash='dot'), hoverinfo='none', showlegend=False), row=1, col=1)

    # ── 4. Subgráfico de Osciladores (RSI / MACD / ATR) ──
    if osc_row is not None:
        if config.get('rsi') and 'RSI' in df_calc.columns:
            fig.add_trace(
                go.Scatter(x=x_dates, y=df_calc['RSI'], mode='lines', name='RSI (14)', line=dict(color='#a855f7', width=1.5)),
                row=osc_row, col=1
            )
            # Sobrecompra / Sobrevenda
            fig.add_hline(y=70, line_dash="dash", line_color="rgba(239, 68, 68, 0.6)", row=osc_row, col=1)
            fig.add_hline(y=30, line_dash="dash", line_color="rgba(34, 197, 94, 0.6)", row=osc_row, col=1)

        elif config.get('macd') and 'MACD_line' in df_calc.columns:
            fig.add_trace(
                go.Scatter(x=x_dates, y=df_calc['MACD_line'], mode='lines', name='MACD', line=dict(color='#3b82f6', width=1.5)),
                row=osc_row, col=1
            )
            fig.add_trace(
                go.Scatter(x=x_dates, y=df_calc['MACD_signal'], mode='lines', name='Señal', line=dict(color='#f97316', width=1.5)),
                row=osc_row, col=1
            )
            colors_hist = ['#22c55e' if v >= 0 else '#ef4444' for v in df_calc['MACD_hist']]
            fig.add_trace(
                go.Bar(x=x_dates, y=df_calc['MACD_hist'], name='Hist', marker_color=colors_hist, opacity=0.7),
                row=osc_row, col=1
            )

        elif config.get('atr') and 'ATR' in df_calc.columns:
            fig.add_trace(
                go.Scatter(x=x_dates, y=df_calc['ATR'], mode='lines', name='ATR', line=dict(color='#eab308', width=1.5)),
                row=osc_row, col=1
            )

    # ── 5. Subgráfico de Volumen ──
    if vol_row is not None:
        vol_colors = ['rgba(8, 153, 129, 0.6)' if c >= o else 'rgba(242, 54, 69, 0.6)' 
                      for o, c in zip(df_calc['open'], df_calc['close'])]
        fig.add_trace(
            go.Bar(
                x=x_dates, y=df_calc['volume'], name='Volumen',
                marker_color=vol_colors, showlegend=False
            ),
            row=vol_row, col=1
        )

    # ── Estilizado Oscuro TradingView / NinjaTrader 8 ──
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0e131f",
        plot_bgcolor="#0e131f",
        margin=dict(l=35, r=35, t=30, b=30),
        height=800,
        hovermode="x unified",
        dragmode="pan",
        uirevision=uirevision,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=11, color="#cbd5e1"),
            bgcolor="rgba(15, 23, 42, 0.7)"
        )
    )

    show_rangeslider = bool(config.get('show_rangeslider', False)) if config else False

    fig.update_xaxes(
        showgrid=True,
        gridcolor="#1e293b",
        zerolinecolor="#1e293b",
        fixedrange=False,
        side="top",
        rangeslider=dict(visible=show_rangeslider, thickness=0.05),
        rangeselector=dict(
            buttons=list([
                dict(count=1, label="1M", step="month", stepmode="backward"),
                dict(count=3, label="3M", step="month", stepmode="backward"),
                dict(count=6, label="6M", step="month", stepmode="backward"),
                dict(count=1, label="YTD", step="year", stepmode="todate"),
                dict(count=1, label="1Y", step="year", stepmode="backward"),
                dict(step="all", label="Todo")
            ]),
            font=dict(color="#ffffff", size=11),
            bgcolor="#1e293b",
            activecolor="#3b82f6",
            x=0, y=1.12
        )
    )

    # Actualizar ejes Y de los subgráficos (permitir zoom en eje Y si se desea)
    fig.update_yaxes(showgrid=True, gridcolor="#1e293b", zerolinecolor="#1e293b", fixedrange=False, side="right")

    return fig
