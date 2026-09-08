import pandas as pd
import numpy as np

# Antes, un profit_factor = inf (sin operaciones perdedoras) se ordenaba por encima de
# CUALQUIER estrategia real con muchas operaciones, aunque viniera de 1-2 trades ganadores
# casuales — estadísticamente irrelevante pero "ganador" en el ranking del optimizador.
MIN_TRADES_FOR_RELIABLE_PF = 10

def calculate_metrics(trades_df: pd.DataFrame, initial_capital: float) -> dict:
    """
    Calcula métricas de rendimiento estilo NinjaTrader 8.
    """
    if trades_df.empty:
        return {
            "total_trades": 0,
            "net_profit": 0,
            "percent_profitable": 0,
            "profit_factor": 0,
            "profit_factor_reliable": False,
            "max_drawdown_pct": 0,
            "cagr": 0,
            "average_trade_net_profit": 0
        }

    gross_profit = trades_df[trades_df['pnl'] > 0]['pnl'].sum()
    gross_loss = abs(trades_df[trades_df['pnl'] < 0]['pnl'].sum())

    net_profit = gross_profit - gross_loss
    total_trades = len(trades_df)
    winning_trades = len(trades_df[trades_df['pnl'] > 0])
    losing_trades = len(trades_df[trades_df['pnl'] < 0])

    percent_profitable = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
    # Con muestra insuficiente (o sin ninguna operación perdedora) el profit_factor no es
    # estadísticamente significativo: se marca como "no confiable" para que la UI/el
    # optimizador puedan penalizarlo o mostrarlo con advertencia en vez de tratarlo como
    # el mejor resultado posible.
    profit_factor_reliable = bool(total_trades >= MIN_TRADES_FOR_RELIABLE_PF and losing_trades > 0)
    avg_trade_net_profit = net_profit / total_trades if total_trades > 0 else 0

    # Calcular perdedoras consecutivas
    is_loser = trades_df['pnl'] < 0
    losers_consec = is_loser.groupby((~is_loser).cumsum()).sum()
    max_consecutive_losers = int(losers_consec.max()) if not losers_consec.empty else 0

    return {
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "net_profit": net_profit,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "percent_profitable": percent_profitable,
        "profit_factor": profit_factor,
        "profit_factor_reliable": profit_factor_reliable,
        "average_trade_net_profit": avg_trade_net_profit,
        "max_consecutive_losers": max_consecutive_losers
    }

def _timeframe_to_bars_per_year(timeframe: str) -> float:
    """Convierte un timeframe tipo '1h'/'4h'/'15m'/'1d' a barras/año (365.25 días)."""
    try:
        tf = str(timeframe).strip().lower()
        unit = tf[-1]
        qty = float(tf[:-1]) if tf[:-1] else 1.0
        seconds_per_unit = {"m": 60, "h": 3600, "d": 86400, "w": 604800}.get(unit)
        if not seconds_per_unit or qty <= 0:
            return 252 * 6  # último recurso: velas de 4h
        bar_seconds = qty * seconds_per_unit
        return (365.25 * 24 * 3600) / bar_seconds
    except Exception:
        return 252 * 6


def calculate_equity_curve_metrics(equity_curve: pd.Series, timeframe: str = None) -> dict:
    """
    Calcula métricas a partir de la curva de equity:
    Max Drawdown, CAGR y Sharpe Ratio anualizados.

    `timeframe` (ej. "1h", "4h", "1d") es el timeframe REAL de la estrategia/backtest.
    Antes, si el índice de equity_curve no era un DatetimeIndex, el fallback asumía
    ciegamente velas de 4h (252*6 barras/año) sin importar el timeframe configurado —
    para una estrategia diaria eso infla el Sharpe anualizado en ~sqrt(6) (~2.45x). Pasar
    el timeframe real permite usar el fallback correcto en vez de uno fijo incorrecto.
    """
    if len(equity_curve) == 0:
        return {}

    equity_curve = equity_curve.dropna()
    if len(equity_curve) < 2:
        return {'max_drawdown_pct': 0, 'cagr': 0, 'sharpe_ratio': 0}

    fallback_bars_per_year = _timeframe_to_bars_per_year(timeframe) if timeframe else (252 * 6)

    # ── Drawdown ────────────────────────────────────────────────────────────
    roll_max = equity_curve.cummax()
    drawdown = (equity_curve - roll_max) / roll_max
    max_drawdown_pct = drawdown.min() * 100

    # ── CAGR ────────────────────────────────────────────────────────────────
    if isinstance(equity_curve.index, pd.DatetimeIndex):
        days = (equity_curve.index[-1] - equity_curve.index[0]).days
        years = days / 365.25 if days > 0 else 0
    else:
        # Fallback: usar el timeframe real de la estrategia (o 4h si no se proveyó)
        n_bars = len(equity_curve)
        years = n_bars / fallback_bars_per_year

    if years > 0 and equity_curve.iloc[0] > 0:
        cagr = ((equity_curve.iloc[-1] / equity_curve.iloc[0]) ** (1 / years) - 1) * 100
    else:
        cagr = 0

    # ── Sharpe Ratio (annualised) ────────────────────────────────────────────
    returns = equity_curve.pct_change().dropna()
    if len(returns) > 1 and returns.std() > 0:
        if isinstance(equity_curve.index, pd.DatetimeIndex) and len(equity_curve) > 2:
            # Infer periods per year from the median bar interval
            median_seconds = equity_curve.index.to_series().diff().dt.total_seconds().median()
            periods_per_year = (365.25 * 24 * 3600) / median_seconds if median_seconds and median_seconds > 0 else fallback_bars_per_year
        else:
            periods_per_year = fallback_bars_per_year
        sharpe_ratio = (returns.mean() / returns.std()) * np.sqrt(periods_per_year)
    else:
        sharpe_ratio = 0

    return {
        "max_drawdown_pct": max_drawdown_pct,
        "cagr": cagr,
        "sharpe_ratio": sharpe_ratio,
    }
