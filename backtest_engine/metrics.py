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
    Calcula métricas a partir de la curva de equity (ej. Max Drawdown, CAGR).
    """
    if len(equity_curve) == 0:
        return {}
        
    # Drawdown
    roll_max = equity_curve.cummax()
    drawdown = (equity_curve - roll_max) / roll_max
    max_drawdown_pct = drawdown.min() * 100
    
    # CAGR (Asume índice datetime)
    if isinstance(equity_curve.index, pd.DatetimeIndex) and len(equity_curve) > 1:
        days = (equity_curve.index[-1] - equity_curve.index[0]).days
        if days > 0:
            cagr = ((equity_curve.iloc[-1] / equity_curve.iloc[0]) ** (365.25 / days) - 1) * 100
        else:
            cagr = 0
    else:
        cagr = 0
        
    return {
        "max_drawdown_pct": max_drawdown_pct,
        "cagr": cagr
    }
