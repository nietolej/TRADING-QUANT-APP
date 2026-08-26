"""
optimizer.py — Motor de búsqueda en cuadrícula (Grid Search) para estrategias.

Para cada combinación de parámetros generada por el grid, ejecuta un backtest
completo y recopila métricas clave para comparación. Diseñado para máxima estabilidad en Windows.
"""
from __future__ import annotations

import copy
import itertools
import math
import os
import concurrent.futures
from typing import Any, Callable, Dict, Generator, List, Optional

import pandas as pd
import numpy as np
import yaml

from backtest_engine.backtester import Backtester
from backtest_engine.metrics import calculate_equity_curve_metrics, calculate_metrics
from strategy_engine.base_strategy import BaseStrategy


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _build_range(min_val: float, max_val: float, step: float) -> List[float]:
    """
    Genera una lista de valores desde min_val hasta max_val (inclusive) con
    incrementos de `step`. Siempre incluye min_val y max_val cuando corresponde.
    """
    try:
        min_v = float(min_val)
        max_v = float(max_val)
        st = float(step)
    except (ValueError, TypeError):
        return [min_val]

    if st <= 0:
        return [min_v]
    if min_v > max_v:
        return [min_v]

    values: List[float] = []
    v = min_v
    while v <= max_v + 1e-9:
        val = int(v) if abs(v - round(v)) < 1e-6 else round(v, 4)
        if val not in values:
            values.append(val)
        v += st
        if len(values) > 500:
            break

    # Asegurar que max_v esté presente
    last_val = int(max_v) if abs(max_v - round(max_v)) < 1e-6 else round(max_v, 4)
    if not values or (last_val not in values and values[-1] < max_v):
        values.append(last_val)

    return values


def count_combinations(param_ranges: Dict[str, Dict[str, float]]) -> int:
    """Devuelve el número total de combinaciones sin ejecutar el grid."""
    total = 1
    for cfg in param_ranges.values():
        vals = _build_range(cfg.get('min', 0), cfg.get('max', 0), cfg.get('step', 1))
        total *= max(1, len(vals))
    return total


def generate_param_grid(
    param_ranges: Dict[str, Dict[str, float]]
) -> Generator[Dict[str, Any], None, None]:
    """
    Genera todas las combinaciones de parámetros del grid.
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
# Optimizer Worker
# ──────────────────────────────────────────────

def _optimizer_worker(
    params: Dict[str, Any],
    base_config: Dict[str, Any],
    df: pd.DataFrame,
    initial_capital: float,
    commission_pct: float = 0.1,
    slippage_pct: float = 0.05,
    ec_config: Optional[Dict[str, Any]] = None,
    sizing_config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Ejecuta un backtest individual para una combinación específica de parámetros.
    """
    try:
        config_copy = copy.deepcopy(base_config)
        
        # Inyectar sizing si fue provisto
        if sizing_config:
            if 'risk_management' not in config_copy:
                config_copy['risk_management'] = {}
            config_copy['risk_management']['position_sizing'] = sizing_config

        # Inyectar equity curve si fue provisto
        if ec_config:
            config_copy['equity_curve_management'] = ec_config

        strategy = BaseStrategy(config_copy, custom_parameters=params)

        if ec_config and ec_config.get('enabled', False):
            from backtest_engine.equity_curve_backtester import EquityCurveBacktester
            bt = EquityCurveBacktester(
                strategy,
                initial_capital=initial_capital,
                commission_pct=commission_pct,
                slippage_pct=slippage_pct
            )
        else:
            bt = Backtester(
                strategy,
                initial_capital=initial_capital,
                commission_pct=commission_pct,
                slippage_pct=slippage_pct
            )
            
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
            # Submuestrear para transferencias ligeras
            if len(equity_curve) <= 400:
                eq_list = equity_curve['equity'].tolist()
            else:
                step_s = max(1, len(equity_curve) // 400)
                eq_list = equity_curve['equity'].iloc[::step_s].tolist()
        else:
            eq_metrics = {'sharpe_ratio': -999.0, 'cagr': -999.0, 'max_drawdown_pct': 0.0}
            trade_metrics = {'total_trades': 0}
            final_equity = initial_capital
            eq_list = []

        return {
            'params': {k: (int(v) if float(v) == int(v) else float(v)) for k, v in params.items()},
            'sharpe_ratio': round(float(eq_metrics.get('sharpe_ratio', -999)), 4),
            'cagr': round(float(eq_metrics.get('cagr', -999)), 4),
            'max_drawdown_pct': round(float(eq_metrics.get('max_drawdown_pct', 0)), 4),
            'total_trades': int(trade_metrics.get('total_trades', 0)),
            'winning_trades': int(trade_metrics.get('winning_trades', 0)),
            'losing_trades': int(trade_metrics.get('losing_trades', 0)),
            'percent_profitable': round(float(trade_metrics.get('percent_profitable', 0)), 2),
            'profit_factor': round(float(trade_metrics.get('profit_factor', 0)), 2),
            'max_consecutive_losers': int(trade_metrics.get('max_consecutive_losers', 0)),
            'initial_capital': initial_capital,
            'final_equity': round(final_equity, 6),
            'net_pnl': round(final_equity - initial_capital, 6),
            'equity_curve': eq_list,
        }

    except Exception as ex:
        return {
            'params': {k: (int(v) if float(v) == int(v) else float(v)) for k, v in params.items()},
            'sharpe_ratio': -999.0,
            'cagr': -999.0,
            'max_drawdown_pct': 0.0,
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'percent_profitable': 0.0,
            'profit_factor': 0.0,
            'max_consecutive_losers': 0,
            'initial_capital': initial_capital,
            'final_equity': initial_capital,
            'net_pnl': 0.0,
            'error': str(ex),
            'equity_curve': [],
        }


# ──────────────────────────────────────────────
# Optimizer Entry Point
# ──────────────────────────────────────────────

def run_grid_search(
    strategy_path: str,
    df: pd.DataFrame,
    initial_capital: float,
    param_ranges: Dict[str, Dict[str, float]],
    optimize_metric: str = 'sharpe_ratio',
    progress_callback: Optional[Callable[[int, int], None]] = None,
    commission_pct: float = 0.1,
    slippage_pct: float = 0.05,
    ec_config: Optional[Dict[str, Any]] = None,
    sizing_config: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Ejecuta Grid Search de forma segura y multihilo.
    Retorna una lista de resultados ordenados de mejor a peor según `optimize_metric`.
    """
    results: List[Dict[str, Any]] = []
    total = count_combinations(param_ranges)
    done = 0

    # Cargamos el config YAML una sola vez
    with open(strategy_path, 'r', encoding='utf-8') as fh:
        base_config = yaml.safe_load(fh)
        
    param_grids = list(generate_param_grid(param_ranges))
    max_workers = min(16, max(1, (os.cpu_count() or 2) * 2))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _optimizer_worker,
                params,
                base_config,
                df,
                initial_capital,
                commission_pct,
                slippage_pct,
                ec_config,
                sizing_config
            ): params
            for params in param_grids
        }
        
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result()
                results.append(res)
            except Exception as e:
                params = futures[future]
                results.append({
                    'params': {k: (int(v) if float(v) == int(v) else float(v)) for k, v in params.items()},
                    'sharpe_ratio': -999.0,
                    'cagr': -999.0,
                    'max_drawdown_pct': 0.0,
                    'total_trades': 0,
                    'winning_trades': 0,
                    'losing_trades': 0,
                    'percent_profitable': 0.0,
                    'profit_factor': 0.0,
                    'max_consecutive_losers': 0,
                    'initial_capital': initial_capital,
                    'final_equity': initial_capital,
                    'net_pnl': 0.0,
                    'error': f"Error: {str(e)}",
                    'equity_curve': [],
                })
            
            done += 1
            if progress_callback:
                try:
                    progress_callback(done, total)
                except Exception:
                    pass

    # Ordenar: sin errores primero, luego por métrica descendente
    def _sort_key(r):
        if r.get('error'):
            return -999_999.0
        val = float(r.get(optimize_metric, -999))
        # Para drawdown: menor (menos negativo/menor % caída) es mejor
        if optimize_metric == 'max_drawdown_pct':
            val = -abs(val)
        return val

    results.sort(key=_sort_key, reverse=True)
    return results
