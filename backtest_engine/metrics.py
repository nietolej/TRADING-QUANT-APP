import pandas as pd
import numpy as np

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
    avg_trade_net_profit = net_profit / total_trades if total_trades > 0 else 0
    
    return {
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "net_profit": net_profit,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "percent_profitable": percent_profitable,
        "profit_factor": profit_factor,
        "average_trade_net_profit": avg_trade_net_profit
    }

def calculate_equity_curve_metrics(equity_curve: pd.Series) -> dict:
    """
    Calcula métricas a partir de la curva de equity:
    Max Drawdown, CAGR y Sharpe Ratio anualizados.
    """
    if len(equity_curve) == 0:
        return {}

    equity_curve = equity_curve.dropna()
    if len(equity_curve) < 2:
        return {'max_drawdown_pct': 0, 'cagr': 0, 'sharpe_ratio': 0}

    # ── Drawdown ────────────────────────────────────────────────────────────
    roll_max = equity_curve.cummax()
    drawdown = (equity_curve - roll_max) / roll_max
    max_drawdown_pct = drawdown.min() * 100

    # ── CAGR ────────────────────────────────────────────────────────────────
    if isinstance(equity_curve.index, pd.DatetimeIndex):
        days = (equity_curve.index[-1] - equity_curve.index[0]).days
        years = days / 365.25 if days > 0 else 0
    else:
        # Fallback: assume each bar is a 4-hour candle (252 * 6 bars/year)
        n_bars = len(equity_curve)
        years = n_bars / (252 * 6)

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
            periods_per_year = (365.25 * 24 * 3600) / median_seconds if median_seconds and median_seconds > 0 else 252
        else:
            periods_per_year = 252 * 6  # 4h bars default
        sharpe_ratio = (returns.mean() / returns.std()) * np.sqrt(periods_per_year)
    else:
        sharpe_ratio = 0

    return {
        "max_drawdown_pct": max_drawdown_pct,
        "cagr": cagr,
        "sharpe_ratio": sharpe_ratio,
    }
