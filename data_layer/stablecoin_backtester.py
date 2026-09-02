import os
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from strategy_engine.stablecoin_momentum_strategy import StablecoinEmissionEMAStrategy, HALVING_DATES

logger = logging.getLogger(__name__)

class StablecoinBacktester:
    """
    Motor cuantitativo de simulación y evaluación para estrategias
    basadas en Flujos de Stablecoins, Tendencia EMA y Filtros de Halving.
    """
    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        self.df_merged: Optional[pd.DataFrame] = None
        self._load_datasets()

    def _load_datasets(self) -> None:
        """Carga y une los datasets históricos diarios de BTC y Stablecoins."""
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir, exist_ok=True)

        btc_parquet = os.path.join(self.data_dir, "btc_daily_historical.parquet")
        btc_csv = os.path.join(self.data_dir, "btc_daily_historical.csv")
        stables_csv = os.path.join(self.data_dir, "stablecoins_daily_historical.csv")
        stables_parquet = os.path.join(self.data_dir, "stablecoins_daily_historical.parquet")

        df_btc: Optional[pd.DataFrame] = None
        if os.path.exists(btc_parquet):
            try:
                df_btc = pd.read_parquet(btc_parquet)
            except Exception as e:
                logger.warning(f"Error leyendo btc_daily_historical.parquet: {e}")

        if df_btc is None and os.path.exists(btc_csv):
            try:
                df_btc = pd.read_csv(btc_csv)
            except Exception as e:
                logger.warning(f"Error leyendo btc_daily_historical.csv: {e}")

        if df_btc is None:
            try:
                from data_layer.halving_analyzer import BTCHalvingAnalyzer
                analyzer = BTCHalvingAnalyzer()
                df_btc = analyzer.fetch_historical_btc_data()
            except Exception as e:
                logger.error(f"Error al descargar df_btc: {e}")
                return

        df_stables: Optional[pd.DataFrame] = None
        if os.path.exists(stables_csv):
            try:
                df_stables = pd.read_csv(stables_csv)
            except Exception as e:
                logger.warning(f"Error leyendo stablecoins_daily_historical.csv: {e}")

        if df_stables is None and os.path.exists(stables_parquet):
            try:
                df_stables = pd.read_parquet(stables_parquet)
            except Exception as e:
                logger.warning(f"Error leyendo stablecoins_daily_historical.parquet: {e}")

        if df_stables is None:
            try:
                from data_layer.halving_analyzer import BTCHalvingAnalyzer
                analyzer = BTCHalvingAnalyzer()
                df_stables = analyzer.fetch_historical_stablecoin_data()
            except Exception as e:
                logger.error(f"Error al descargar df_stables: {e}")
                return

        try:
            df_btc['timestamp'] = pd.to_datetime(df_btc['timestamp'], utc=True)
            df_stables['timestamp'] = pd.to_datetime(df_stables['timestamp'], utc=True)

            if 'market_cap' in df_stables.columns:
                df_stables = df_stables.rename(columns={'market_cap': 'stables_mcap'})

            # Merge por fecha diaria
            df_btc['date_only'] = df_btc['timestamp'].dt.floor('D')
            df_stables['date_only'] = df_stables['timestamp'].dt.floor('D')

            merged = pd.merge(
                df_btc[['date_only', 'timestamp', 'open', 'high', 'low', 'close', 'volume']],
                df_stables[['date_only', 'stables_mcap']],
                on='date_only',
                how='inner'
            ).sort_values('timestamp').reset_index(drop=True)

            merged.drop(columns=['date_only'], inplace=True, errors='ignore')
            merged['stables_mcap'] = merged['stables_mcap'].ffill().bfill()
            self.df_merged = merged
            logger.info(f"StablecoinBacktester: {len(self.df_merged)} registros diarios cargados con éxito.")
        except Exception as e:
            logger.error(f"Error uniendo datasets BTC y Stablecoins: {e}")

    def run_backtest(
        self,
        initial_capital: float = 10000.0,
        commission_pct: float = 0.1,
        slippage_pct: float = 0.05,
        ema_fast: int = 20,
        ema_slow: int = 50,
        ema_trend: int = 100,
        trend_mode: str = "price_above",
        flow_window: int = 14,
        z_window: int = 60,
        z_entry_threshold: float = 1.0,
        z_exit_threshold: float = -0.5,
        halving_filter_enabled: bool = True,
        min_post_halving_days: int = 150,
        max_post_halving_days: int = 550,
        stop_loss_pct: float = 8.0,
        take_profit_pct: float = 45.0,
        trailing_stop: bool = True,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Ejecuta la simulación cuantitativa trade-a-trade sobre el histórico.
        """
        if self.df_merged is None or self.df_merged.empty:
            self._load_datasets()

        if self.df_merged is None or self.df_merged.empty:
            raise ValueError("No hay datos históricos disponibles para el backtest.")

        df = self.df_merged.copy()

        # Filtrar rango de fechas si se especifica
        if start_date:
            df = df[df['timestamp'] >= pd.to_datetime(start_date, utc=True)]
        if end_date:
            df = df[df['timestamp'] <= pd.to_datetime(end_date, utc=True)]

        df = df.sort_values('timestamp').reset_index(drop=True)
        if df.empty:
            raise ValueError("El rango de fechas especificado no contiene registros.")

        # Configuración para la estrategia
        strategy_config = {
            "strategy_name": "Stablecoin Emission & EMA Momentum",
            "class_name": "StablecoinEmissionEMAStrategy",
            "symbol": "BTC/USDT",
            "timeframe": "1d",
            "trade_direction": "Long",
            "parameters": {
                "ema_fast": ema_fast,
                "ema_slow": ema_slow,
                "ema_trend": ema_trend,
                "trend_mode": trend_mode,
                "flow_window": flow_window,
                "z_window": z_window,
                "z_entry_threshold": z_entry_threshold,
                "z_exit_threshold": z_exit_threshold,
                "halving_filter_enabled": halving_filter_enabled,
                "min_post_halving_days": min_post_halving_days,
                "max_post_halving_days": max_post_halving_days,
                "SL": stop_loss_pct,
                "TP": take_profit_pct
            }
        }

        strat = StablecoinEmissionEMAStrategy(strategy_config)
        df_signals = strat.generate_signals(df)

        # -------------------------------------------------------------
        # SIMULACIÓN ITERATIVA DE TRADES (SIN LOOK-AHEAD BIAS)
        # -------------------------------------------------------------
        capital = float(initial_capital)
        comm_factor = commission_pct / 100.0
        slip_factor = slippage_pct / 100.0

        position = 0.0 # Cantidad de BTC
        entry_price = 0.0
        entry_idx = 0
        entry_date = None
        entry_halving_day = 0
        highest_price_in_trade = 0.0

        trades_list = []
        equity_records = []

        initial_btc_price = float(df_signals.iloc[0]['close'])
        bnh_btc_amount = (initial_capital * (1.0 - comm_factor)) / (initial_btc_price * (1.0 + slip_factor))

        for i in range(len(df_signals)):
            row = df_signals.iloc[i]
            curr_date = row['timestamp']
            curr_open = float(row.get('open', row['close']))
            curr_high = float(row.get('high', row['close']))
            curr_low = float(row.get('low', row['close']))
            curr_close = float(row['close'])
            rel_h_day = int(row.get('rel_halving_days', 0))

            is_entry_signal = bool(row.get('entry_long', False))
            is_exit_signal = bool(row.get('exit_long', False))

            # 1. Si estamos en posición, evaluar SL, TP, Trailing Stop o Señal de Salida
            if position > 0.0:
                highest_price_in_trade = max(highest_price_in_trade, curr_high)
                trade_pnl_pct_high = ((curr_high - entry_price) / entry_price) * 100.0
                trade_pnl_pct_low = ((curr_low - entry_price) / entry_price) * 100.0

                exit_triggered = False
                exit_price = curr_close
                exit_reason = "Signal Exit"

                # A. Stop Loss Fijo
                if stop_loss_pct > 0 and trade_pnl_pct_low <= -stop_loss_pct:
                    exit_price = entry_price * (1.0 - (stop_loss_pct / 100.0))
                    exit_reason = f"Stop Loss ({stop_loss_pct}%)"
                    exit_triggered = True

                # B. Take Profit Fijo
                elif take_profit_pct > 0 and trade_pnl_pct_high >= take_profit_pct:
                    exit_price = entry_price * (1.0 + (take_profit_pct / 100.0))
                    exit_reason = f"Take Profit ({take_profit_pct}%)"
                    exit_triggered = True

                # C. Trailing Stop
                elif trailing_stop and stop_loss_pct > 0:
                    drawdown_from_peak = ((curr_low - highest_price_in_trade) / highest_price_in_trade) * 100.0
                    if drawdown_from_peak <= -stop_loss_pct and highest_price_in_trade > entry_price * 1.05:
                        exit_price = highest_price_in_trade * (1.0 - (stop_loss_pct / 100.0))
                        exit_reason = f"Trailing Stop ({stop_loss_pct}%)"
                        exit_triggered = True

                # D. Salida por Señal (Indicadores / Régimen)
                elif is_exit_signal and (i > entry_idx):
                    exit_price = curr_close
                    exit_reason = "Signal / Liquidity Exhaustion"
                    exit_triggered = True

                # Ejecutar salida
                if exit_triggered:
                    exec_exit_price = exit_price * (1.0 - slip_factor)
                    gross_value = position * exec_exit_price
                    fee = gross_value * comm_factor
                    capital = gross_value - fee

                    pnl_usd = capital - (position * entry_price)
                    pnl_pct = ((exec_exit_price - entry_price) / entry_price) * 100.0
                    holding_days = (curr_date - entry_date).days if entry_date else 0

                    trades_list.append({
                        "trade_id": len(trades_list) + 1,
                        "entry_date": entry_date.strftime('%Y-%m-%d'),
                        "exit_date": curr_date.strftime('%Y-%m-%d'),
                        "entry_price": round(entry_price, 2),
                        "exit_price": round(exec_exit_price, 2),
                        "pnl_usd": round(pnl_usd, 2),
                        "pnl_pct": round(pnl_pct, 2),
                        "exit_reason": exit_reason,
                        "holding_days": max(1, holding_days),
                        "entry_halving_day": entry_halving_day,
                        "exit_halving_day": rel_h_day
                    })

                    position = 0.0
                    entry_price = 0.0
                    highest_price_in_trade = 0.0

            # 2. Si no estamos en posición, evaluar entrada
            elif is_entry_signal and capital > 10.0:
                exec_entry_price = curr_close * (1.0 + slip_factor)
                fee = capital * comm_factor
                capital_after_fee = capital - fee
                position = capital_after_fee / exec_entry_price
                entry_price = exec_entry_price
                entry_idx = i
                entry_date = curr_date
                entry_halving_day = rel_h_day
                highest_price_in_trade = curr_high

            # 3. Registrar Equity diario
            current_portfolio_value = (position * curr_close) if position > 0 else capital
            bnh_value = bnh_btc_amount * curr_close

            equity_records.append({
                "timestamp": curr_date,
                "strategy_equity": round(current_portfolio_value, 2),
                "benchmark_equity": round(bnh_value, 2),
                "in_position": (position > 0),
                "btc_price": round(curr_close, 2),
                "stables_mcap": round(row.get('stables_mcap', 0), 2),
                "z_stables_flow": round(row.get('z_stables_flow', 0), 3),
                "ema_fast": round(row.get('ema_fast', 0), 2),
                "ema_slow": round(row.get('ema_slow', 0), 2)
            })

        # Cerrar posición al final si quedó abierta
        if position > 0:
            last_row = df_signals.iloc[-1]
            last_close = float(last_row['close'])
            exec_exit_price = last_close * (1.0 - slip_factor)
            gross_value = position * exec_exit_price
            fee = gross_value * comm_factor
            capital = gross_value - fee

            pnl_usd = capital - (position * entry_price)
            pnl_pct = ((exec_exit_price - entry_price) / entry_price) * 100.0
            holding_days = (last_row['timestamp'] - entry_date).days if entry_date else 0

            trades_list.append({
                "trade_id": len(trades_list) + 1,
                "entry_date": entry_date.strftime('%Y-%m-%d'),
                "exit_date": last_row['timestamp'].strftime('%Y-%m-%d'),
                "entry_price": round(entry_price, 2),
                "exit_price": round(exec_exit_price, 2),
                "pnl_usd": round(pnl_usd, 2),
                "pnl_pct": round(pnl_pct, 2),
                "exit_reason": "End of Data",
                "holding_days": max(1, holding_days),
                "entry_halving_day": entry_halving_day,
                "exit_halving_day": int(last_row.get('rel_halving_days', 0))
            })

        df_equity = pd.DataFrame(equity_records)
        df_trades = pd.DataFrame(trades_list)

        # -------------------------------------------------------------
        # CÁLCULO DE MÉTRICAS CUANTITATIVAS AVANZADAS
        # -------------------------------------------------------------
        final_equity = float(df_equity['strategy_equity'].iloc[-1]) if not df_equity.empty else initial_capital
        total_profit_usd = final_equity - initial_capital
        total_return_pct = ((final_equity / initial_capital) - 1.0) * 100.0

        final_bnh = float(df_equity['benchmark_equity'].iloc[-1]) if not df_equity.empty else initial_capital
        bnh_return_pct = ((final_bnh / initial_capital) - 1.0) * 100.0

        # Drawdown de la estrategia
        df_equity['peak_equity'] = df_equity['strategy_equity'].cummax()
        df_equity['drawdown_pct'] = ((df_equity['strategy_equity'] - df_equity['peak_equity']) / df_equity['peak_equity']) * 100.0
        max_drawdown_pct = float(df_equity['drawdown_pct'].min())

        # CAGR
        total_days = max(1, (df_equity['timestamp'].iloc[-1] - df_equity['timestamp'].iloc[0]).days)
        years = total_days / 365.25
        cagr_pct = ((final_equity / initial_capital) ** (1.0 / years) - 1.0) * 100.0 if years > 0 and final_equity > 0 else 0.0

        # Sharpe & Sortino Ratios (Anualizados sobre retornos diarios)
        daily_returns = df_equity['strategy_equity'].pct_change().dropna()
        if len(daily_returns) > 1 and daily_returns.std() > 0:
            mean_ret = daily_returns.mean()
            std_ret = daily_returns.std()
            sharpe_ratio = round((mean_ret / std_ret) * np.sqrt(365), 2)

            downside_returns = daily_returns[daily_returns < 0]
            downside_std = downside_returns.std() if not downside_returns.empty else std_ret
            sortino_ratio = round((mean_ret / (downside_std + 1e-9)) * np.sqrt(365), 2)
        else:
            sharpe_ratio = 0.0
            sortino_ratio = 0.0

        calmar_ratio = round(cagr_pct / abs(max_drawdown_pct), 2) if max_drawdown_pct < 0 else 0.0

        # Estadísticas de Trades
        total_trades = len(df_trades)
        if total_trades > 0:
            wins = df_trades[df_trades['pnl_usd'] > 0]
            losses = df_trades[df_trades['pnl_usd'] <= 0]
            win_rate = (len(wins) / total_trades) * 100.0
            gross_win = float(wins['pnl_usd'].sum()) if not wins.empty else 0.0
            gross_loss = abs(float(losses['pnl_usd'].sum())) if not losses.empty else 0.0
            profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)
            avg_trade_pnl = float(df_trades['pnl_usd'].mean())
            avg_trade_pct = float(df_trades['pnl_pct'].mean())
            avg_holding_days = float(df_trades['holding_days'].mean())
            max_win_pct = float(df_trades['pnl_pct'].max())
            max_loss_pct = float(df_trades['pnl_pct'].min())
        else:
            win_rate = 0.0
            profit_factor = 0.0
            avg_trade_pnl = 0.0
            avg_trade_pct = 0.0
            avg_holding_days = 0.0
            max_win_pct = 0.0
            max_loss_pct = 0.0

        metrics = {
            "initial_capital": initial_capital,
            "final_capital": round(final_equity, 2),
            "net_profit_usd": round(total_profit_usd, 2),
            "total_return_pct": round(total_return_pct, 2),
            "bnh_return_pct": round(bnh_return_pct, 2),
            "cagr_pct": round(cagr_pct, 2),
            "sharpe_ratio": sharpe_ratio,
            "sortino_ratio": sortino_ratio,
            "calmar_ratio": calmar_ratio,
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "total_trades": total_trades,
            "winning_trades": len(df_trades[df_trades['pnl_usd'] > 0]) if total_trades > 0 else 0,
            "losing_trades": len(df_trades[df_trades['pnl_usd'] <= 0]) if total_trades > 0 else 0,
            "win_rate_pct": round(win_rate, 1),
            "profit_factor": profit_factor,
            "avg_trade_pnl_usd": round(avg_trade_pnl, 2),
            "avg_trade_pct": round(avg_trade_pct, 2),
            "avg_holding_days": round(avg_holding_days, 1),
            "max_win_pct": round(max_win_pct, 2),
            "max_loss_pct": round(max_loss_pct, 2),
            "total_days": total_days
        }

        return {
            "metrics": metrics,
            "equity_curve": df_equity,
            "trades": df_trades,
            "signals": df_signals,
            "parameters": strategy_config["parameters"]
        }

    @staticmethod
    def build_backtest_figure(results: Dict[str, Any]) -> go.Figure:
        """
        Construye la visualización Plotly interactiva sincronizada en 3 paneles:
        Panel 1: Precio BTC + EMAs + Señales de Entrada/Salida.
        Panel 2: Z-Score de Emisión de Stablecoins con líneas de umbral.
        Panel 3: Curva de Capital (Estrategia vs Buy & Hold) y Drawdown.
        """
        df_equity = results.get("equity_curve", pd.DataFrame())
        df_trades = results.get("trades", pd.DataFrame())
        params = results.get("parameters", {})

        if df_equity.empty:
            fig = go.Figure()
            fig.update_layout(paper_bgcolor='#111827', plot_bgcolor='#0a0e17')
            return fig

        fig = make_subplots(
            rows=3,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.06,
            row_heights=[0.45, 0.25, 0.30],
            subplot_titles=(
                "Precio de Bitcoin (BTC/USD), EMAs y Puntos de Entrada / Salida",
                f"Impulso de Emisión On-Chain (Z-Score Stablecoins Flow {params.get('flow_window', 14)}d)",
                "Curva de Crecimiento de Capital (Estrategia SHM vs. Buy & Hold)"
            )
        )

        dates = df_equity['timestamp']

        # --- PANEL 1: PRECIO Y SEÑALES ---
        fig.add_trace(go.Scatter(
            x=dates,
            y=df_equity['btc_price'],
            name='Precio BTC',
            line=dict(color='#cbd5e1', width=1.5),
            hovertemplate='<b>BTC/USD</b>: $%{y:,.2f}<extra></extra>'
        ), row=1, col=1)

        fig.add_trace(go.Scatter(
            x=dates,
            y=df_equity['ema_fast'],
            name=f"EMA Rápida ({params.get('ema_fast', 20)})",
            line=dict(color='#38bdf8', width=1.2, dash='dot'),
            opacity=0.8,
            hovertemplate='EMA Fast: $%{y:,.2f}<extra></extra>'
        ), row=1, col=1)

        fig.add_trace(go.Scatter(
            x=dates,
            y=df_equity['ema_slow'],
            name=f"EMA Lenta ({params.get('ema_slow', 50)})",
            line=dict(color='#fbbf24', width=1.2),
            opacity=0.8,
            hovertemplate='EMA Slow: $%{y:,.2f}<extra></extra>'
        ), row=1, col=1)

        # Marcas de Trades (Entradas y Salidas)
        if not df_trades.empty:
            entry_dates = pd.to_datetime(df_trades['entry_date'], utc=True)
            entry_prices = df_trades['entry_price']
            fig.add_trace(go.Scatter(
                x=entry_dates,
                y=entry_prices,
                mode='markers',
                name='Compra (Long)',
                marker=dict(symbol='triangle-up', size=11, color='#10b981', line=dict(width=1, color='#ffffff')),
                customdata=df_trades[['trade_id', 'entry_halving_day']],
                hovertemplate='<b>COMPRA LONG #%{customdata[0]}</b><br>Precio: $%{y:,.2f}<br>Día Halving: H+%{customdata[1]}d<extra></extra>'
            ), row=1, col=1)

            exit_dates = pd.to_datetime(df_trades['exit_date'], utc=True)
            exit_prices = df_trades['exit_price']
            fig.add_trace(go.Scatter(
                x=exit_dates,
                y=exit_prices,
                mode='markers',
                name='Venta / Cierre',
                marker=dict(symbol='triangle-down', size=11, color='#ef4444', line=dict(width=1, color='#ffffff')),
                customdata=df_trades[['trade_id', 'pnl_pct', 'pnl_usd', 'exit_reason']],
                hovertemplate='<b>CIERRE TRADE #%{customdata[0]}</b><br>Precio: $%{y:,.2f}<br>PnL: %{customdata[1]:+.2f}% ($%{customdata[2]:+,.2f})<br>Motivo: %{customdata[3]}<extra></extra>'
            ), row=1, col=1)

        # --- PANEL 2: Z-SCORE DE STABLECOINS ---
        z_scores = df_equity['z_stables_flow']
        z_entry = params.get('z_entry_threshold', 1.0)
        z_exit = params.get('z_exit_threshold', -0.5)

        # Colores de barras de Z-Score
        bar_colors = ['#10b981' if z >= z_entry else ('#ef4444' if z <= z_exit else '#64748b') for z in z_scores]

        fig.add_trace(go.Bar(
            x=dates,
            y=z_scores,
            name='Z-Score Emisión',
            marker_color=bar_colors,
            opacity=0.75,
            hovertemplate='<b>Z-Score Flujo</b>: %{y:+.2f}σ<extra></extra>'
        ), row=2, col=1)

        # Líneas de umbral
        fig.add_hline(y=z_entry, line_dash='dash', line_color='#10b981', line_width=1.2, row=2, col=1, annotation_text=f"Entrada ({z_entry:+.1f}σ)", annotation_position="top left", annotation_font=dict(color='#10b981', size=9))
        fig.add_hline(y=z_exit, line_dash='dash', line_color='#ef4444', line_width=1.2, row=2, col=1, annotation_text=f"Salida ({z_exit:+.1f}σ)", annotation_position="bottom left", annotation_font=dict(color='#ef4444', size=9))
        fig.add_hline(y=0.0, line_color='#334155', line_width=1, row=2, col=1)

        # --- PANEL 3: CURVAS DE EQUITY ---
        fig.add_trace(go.Scatter(
            x=dates,
            y=df_equity['strategy_equity'],
            name='Estrategia SHM ($)',
            line=dict(color='#10b981', width=2.2),
            hovertemplate='<b>Estrategia</b>: $%{y:,.2f}<extra></extra>'
        ), row=3, col=1)

        fig.add_trace(go.Scatter(
            x=dates,
            y=df_equity['benchmark_equity'],
            name='Buy & Hold BTC ($)',
            line=dict(color='#94a3b8', width=1.5, dash='dash'),
            hovertemplate='<b>Buy & Hold BTC</b>: $%{y:,.2f}<extra></extra>'
        ), row=3, col=1)

        # Layout styling
        fig.update_layout(
            paper_bgcolor='#111827',
            plot_bgcolor='#0a0e17',
            font=dict(family='Plus Jakarta Sans, sans-serif', color='#94a3b8', size=11),
            height=850,
            margin=dict(l=60, r=25, t=50, b=50),
            legend=dict(
                orientation='h',
                yanchor='top',
                y=-0.05,
                xanchor='center',
                x=0.5,
                bgcolor='rgba(15, 23, 42, 0.95)',
                bordercolor='#1e293b',
                borderwidth=1,
                font=dict(size=10, color='#e2e8f0', family='JetBrains Mono')
            ),
            hovermode='x unified'
        )

        fig.update_xaxes(gridcolor='#1e293b', tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10))
        fig.update_yaxes(gridcolor='#1e293b', tickfont=dict(family='JetBrains Mono', color='#cbd5e1', size=10))
        fig.update_yaxes(title_text="Precio BTC (USD)", type="log", row=1, col=1)
        fig.update_yaxes(title_text="Z-Score (σ)", row=2, col=1)
        fig.update_yaxes(title_text="Capital Total (USD)", type="log", row=3, col=1)

        return fig
