"""Estadísticas de trades compartidas por PaperTrader (daemon) y BotProxy (interfaz web)."""
from typing import Any, Dict, List


def compute_detailed_stats(trade_history: List[Dict[str, Any]], initial_balance: float) -> Dict[str, Any]:
    """Estadísticas cuantitativas avanzadas a partir del historial de trades de un bot."""
    total_trades = len(trade_history)
    if total_trades == 0:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
            "profit_factor": 0.0,
            "avg_trade_pnl": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
        }

    pnls = [t.get("pnl", 0.0) for t in trade_history]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_pnl = sum(pnls)
    total_pnl_pct = (total_pnl / initial_balance * 100.0) if initial_balance > 0 else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    # Profit factor = ganancia bruta / pérdida bruta. Sin pérdidas el ratio es matemáticamente infinito
    # (no el monto de ganancia bruta, que tiene otras unidades).
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    return {
        "total_trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / total_trades * 100.0),
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "profit_factor": profit_factor,
        "avg_trade_pnl": total_pnl / total_trades,
        "best_trade": max(pnls) if pnls else 0.0,
        "worst_trade": min(pnls) if pnls else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
    }
