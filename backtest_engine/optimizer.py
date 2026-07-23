"""
optimizer.py — Motor de búsqueda en cuadrícula (Grid Search) para estrategias.

Para cada combinación de parámetros generada por el grid, ejecuta un backtest
completo y recopila métricas clave para comparación.
"""
from __future__ import annotations

import copy
import itertools
import math
from typing import Any, Callable, Dict, Generator, List, Optional

import pandas as pd

from backtest_engine.backtester import Backtester
from backtest_engine.metrics import calculate_equity_curve_metrics, calculate_metrics
from strategy_engine.base_strategy import BaseStrategy


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _build_range(min_val: float, max_val: float, step: float) -> List[float]:
    """
    Genera una lista de valores desde min_val hasta max_val (inclusive) con
    incrementos de `step`.  Siempre incluye min_val y max_val.
    """
    if step <= 0:
        return [min_val]
    values: List[float] = []
    v = min_val
    while v <= max_val + 1e-9:
        values.append(round(v, 10))
        v += step
    # Aseguramos que max_val esté siempre presente
    if not values or abs(values[-1] - max_val) > 1e-9:
        values.append(max_val)
    return values


def count_combinations(param_ranges: Dict[str, Dict[str, float]]) -> int:
    """Devuelve el número total de combinaciones sin ejecutar el grid."""
    total = 1
    for cfg in param_ranges.values():
        vals = _build_range(cfg.get('min', 0), cfg.get('max', 0), cfg.get('step', 1))
        total *= len(vals)
    return total


def generate_param_grid(
    param_ranges: Dict[str, Dict[str, float]]
) -> Generator[Dict[str, Any], None, None]:
    """
    Dado un diccionario de la forma:
        { 'RAPIDA': {'min': 5, 'max': 20, 'step': 5},
          'LENTA':  {'min': 20, 'max': 50, 'step': 10} }

    Genera todos los dicts de parámetros que forman el grid.
    """
    keys = list(param_ranges.keys())
    ranges = [
        _build_range(
            param_ranges[k].get('min', 0),
            param_ranges[k].get('max', 0),
            param_ranges[k].get('step', 1),
        )
        for k in keys
    ]
    for combo in itertools.product(*ranges):
        yield dict(zip(keys, combo))


# ──────────────────────────────────────────────
# Optimizer
# ──────────────────────────────────────────────

def run_grid_search(
    strategy_path: str,
    df: pd.DataFrame,
    initial_capital: float,
    param_ranges: Dict[str, Dict[str, float]],
    optimize_metric: str = 'sharpe_ratio',   # 'cagr', 'max_drawdown_pct', 'net_pnl'
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> List[Dict[str, Any]]:
    """
    Ejecuta Grid Search sincrónico (diseñado para usarse en run_in_executor).

    Retorna una lista de resultados ordenados de mejor a peor según
    `optimize_metric`.  Cada elemento contiene:
        - params           : dict de parámetros usados en esta iteración
        - sharpe_ratio     : float
        - cagr             : float  (porcentaje)
        - max_drawdown_pct : float  (porcentaje, negativo)
        - total_trades     : int
        - final_equity     : float
        - net_pnl          : float
    """
    import yaml

    results: List[Dict[str, Any]] = []
    total = count_combinations(param_ranges)
    done = 0

    # Cargamos el config YAML una sola vez para clonar rápido
    with open(strategy_path, 'r', encoding='utf-8') as fh:
        base_config = yaml.safe_load(fh)

    for params in generate_param_grid(param_ranges):
        try:
            # Construimos la estrategia con los parámetros del combo actual
            config_copy = copy.deepcopy(base_config)
            strategy = BaseStrategy(config_copy, custom_parameters=params)

            bt = Backtester(strategy, initial_capital=initial_capital)
            bt.use_vectorbt = False
            run_result = bt.run(df)

            trades_df: Optional[pd.DataFrame] = run_result.get('trades')
            equity_curve: Optional[pd.DataFrame] = run_result.get('equity_curve')

            # Normalizar índice de la equity curve
            if equity_curve is not None and not equity_curve.empty:
                if 'timestamp' in equity_curve.columns:
                    equity_curve = equity_curve.set_index('timestamp')
                equity_curve.index = pd.to_datetime(equity_curve.index)

            if equity_curve is not None and not equity_curve.empty:
                eq_metrics = calculate_equity_curve_metrics(equity_curve['equity'])
                trade_metrics = (
                    calculate_metrics(trades_df, initial_capital)
                    if trades_df is not None and not trades_df.empty
                    else {'total_trades': 0}
                )
                final_equity = float(equity_curve['equity'].iloc[-1])
            else:
                eq_metrics = {'sharpe_ratio': -999.0, 'cagr': -999.0, 'max_drawdown_pct': 0.0}
                trade_metrics = {'total_trades': 0}
                final_equity = initial_capital

            results.append({
                'params': {k: (int(v) if float(v) == int(v) else float(v)) for k, v in params.items()},
                'sharpe_ratio': round(float(eq_metrics.get('sharpe_ratio', -999)), 4),
                'cagr': round(float(eq_metrics.get('cagr', -999)), 4),
                'max_drawdown_pct': round(float(eq_metrics.get('max_drawdown_pct', 0)), 4),
                'total_trades': int(trade_metrics.get('total_trades', 0)),
                'final_equity': round(final_equity, 6),
                'net_pnl': round(final_equity - initial_capital, 6),
            })

        except Exception as ex:
            results.append({
                'params': {k: (int(v) if float(v) == int(v) else float(v)) for k, v in params.items()},
                'sharpe_ratio': -999.0,
                'cagr': -999.0,
                'max_drawdown_pct': 0.0,
                'total_trades': 0,
                'final_equity': initial_capital,
                'net_pnl': 0.0,
                'error': str(ex),
            })

        done += 1
        if progress_callback:
            try:
                progress_callback(done, total)
            except Exception:
                pass

    # Ordenar: sin errores primero, luego por métrica descendente
    def _sort_key(r):
        if 'error' in r:
            return -999_999.0
        val = float(r.get(optimize_metric, -999))
        # Para drawdown: menos negativo = mejor → invertimos
        if optimize_metric == 'max_drawdown_pct':
            val = -abs(val)
        return val

    results.sort(key=_sort_key, reverse=True)
    return results
