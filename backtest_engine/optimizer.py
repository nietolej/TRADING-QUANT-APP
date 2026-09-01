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
from collections import Counter
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

def _extract_range_tuple(cfg: Any) -> tuple[float, float, float]:
    """Extrae (min, max, step) de un dict, tupla o lista."""
    if isinstance(cfg, (list, tuple)):
        min_v = cfg[0] if len(cfg) > 0 else 0
        max_v = cfg[1] if len(cfg) > 1 else min_v
        step_v = cfg[2] if len(cfg) > 2 else 1
        return float(min_v), float(max_v), float(step_v)
    elif isinstance(cfg, dict):
        min_v = cfg.get('min', cfg.get('start', 0))
        max_v = cfg.get('max', cfg.get('end', min_v))
        step_v = cfg.get('step', cfg.get('increment', 1))
        return float(min_v), float(max_v), float(step_v)
    else:
        v = float(cfg or 0)
        return v, v, 1.0


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


def count_combinations(param_ranges: Dict[str, Any]) -> int:
    """Devuelve el número total de combinaciones sin ejecutar el grid."""
    total = 1
    for cfg in param_ranges.values():
        min_v, max_v, st = _extract_range_tuple(cfg)
        vals = _build_range(min_v, max_v, st)
        total *= max(1, len(vals))
    return total


def generate_param_grid(
    param_ranges: Dict[str, Any]
) -> Generator[Dict[str, Any], None, None]:
    """
    Genera todas las combinaciones de parámetros del grid.
    """
    keys = list(param_ranges.keys())
    ranges = []
    for k in keys:
        min_v, max_v, st = _extract_range_tuple(param_ranges[k])
        ranges.append(_build_range(min_v, max_v, st))

    for combo in itertools.product(*ranges):
        yield dict(zip(keys, combo))


def _create_strategy_instance(config: Dict[str, Any], params: Dict[str, Any]) -> BaseStrategy:
    """Instancia la clase de estrategia adecuada según 'class_name'."""
    class_name = config.get("class_name")
    if class_name == "OnChainFlowStrategy":
        from strategy_engine.onchain_flow_strategy import OnChainFlowStrategy
        return OnChainFlowStrategy(config, custom_parameters=params)
    elif class_name == "StablecoinEmissionEMAStrategy":
        from strategy_engine.stablecoin_momentum_strategy import StablecoinEmissionEMAStrategy
        return StablecoinEmissionEMAStrategy(config, custom_parameters=params)
    return BaseStrategy(config, custom_parameters=params)


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

        strategy = _create_strategy_instance(config_copy, params)

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
            # Submuestrear para transferencias ultra ligeras (100 puntos máx)
            if len(equity_curve) <= 100:
                eq_list = equity_curve['equity'].tolist()
            else:
                step_s = max(1, len(equity_curve) // 100)
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
    sizing_config: Optional[Dict[str, Any]] = None,
    cancel_event: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """
    Ejecuta Grid Search de forma segura y multihilo.
    Retorna una lista de resultados ordenados de mejor a peor según `optimize_metric`.
    Soporta cancelación inmediata mediante `cancel_event`.
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
            if cancel_event and getattr(cancel_event, 'is_set', lambda: False)():
                for f in futures:
                    f.cancel()
                break

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


# ──────────────────────────────────────────────────────────────
# Walk-Forward Validation
# ──────────────────────────────────────────────────────────────

def run_walk_forward(
    strategy_path: str,
    df: pd.DataFrame,
    initial_capital: float,
    param_ranges: Dict[str, Dict[str, float]],
    n_splits: int = 5,
    in_sample_pct: float = 0.7,
    optimize_metric: str = 'sharpe_ratio',
    commission_pct: float = 0.1,
    slippage_pct: float = 0.05,
    sizing_config: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    cancel_event: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Validación Walk-Forward para detectar overfitting en Grid Search.

    Divide el dataset en `n_splits` ventanas. Para cada ventana:
      1. In-Sample (IS, 70%): ejecuta Grid Search y elige los mejores parámetros.
      2. Out-of-Sample (OOS, 30%): aplica esos parámetros en datos no vistos.

    Métricas clave del resultado:
      - wf_efficiency: OOS_metric / IS_metric. >0.7 = robusto, <0.5 = sobreajuste.
      - overfitting_detected: True si la eficiencia es baja.
      - consensus_params: los parámetros ganadores más frecuentes entre todos los folds.
    """
    if df.empty or len(df) < 40:
        return {'error': 'DataFrame insuficiente para Walk-Forward (min 40 velas).'}

    with open(strategy_path, 'r', encoding='utf-8') as fh:
        base_config = yaml.safe_load(fh)

    n = len(df)
    window_size = n // n_splits
    folds: List[Dict[str, Any]] = []
    total_steps = n_splits
    done = 0

    for fold_idx in range(n_splits):
        if cancel_event and getattr(cancel_event, 'is_set', lambda: False)():
            break

        fold_start = fold_idx * window_size
        fold_end = fold_start + window_size if fold_idx < n_splits - 1 else n
        fold_df = df.iloc[fold_start:fold_end].copy()

        if len(fold_df) < 20:
            continue

        split_point = int(len(fold_df) * in_sample_pct)
        df_is = fold_df.iloc[:split_point].copy()
        df_oos = fold_df.iloc[split_point:].copy()

        if len(df_is) < 10 or len(df_oos) < 5:
            continue

        # ─── Fase 1: Grid Search sobre IS ───
        is_results = run_grid_search(
            strategy_path=strategy_path,
            df=df_is,
            initial_capital=initial_capital,
            param_ranges=param_ranges,
            optimize_metric=optimize_metric,
            commission_pct=commission_pct,
            slippage_pct=slippage_pct,
            sizing_config=sizing_config,
            cancel_event=cancel_event,
        )

        if not is_results or is_results[0].get('error'):
            continue

        best_is = is_results[0]
        best_params = best_is['params']
        is_metric = float(best_is.get(optimize_metric, -999))

        # ─── Fase 2: Aplicar mejores parámetros IS en OOS ───
        oos_result = _optimizer_worker(
            params=best_params,
            base_config=base_config,
            df=df_oos,
            initial_capital=initial_capital,
            commission_pct=commission_pct,
            slippage_pct=slippage_pct,
            sizing_config=sizing_config,
        )
        oos_metric = float(oos_result.get(optimize_metric, -999))

        # Eficiencia del fold
        if is_metric > 0 and is_metric != -999:
            fold_efficiency = oos_metric / is_metric
        elif is_metric <= 0 and oos_metric <= 0:
            fold_efficiency = 1.0  # Ambos negativos: degradación proporcional
        else:
            fold_efficiency = 0.0  # IS positivo, OOS negativo = sobreajuste total

        folds.append({
            'fold': fold_idx + 1,
            'is_start': str(df_is.index[0])[:10] if hasattr(df_is.index[0], 'strftime') else str(fold_start),
            'is_end': str(df_is.index[-1])[:10] if hasattr(df_is.index[-1], 'strftime') else str(split_point),
            'oos_start': str(df_oos.index[0])[:10] if hasattr(df_oos.index[0], 'strftime') else str(split_point),
            'oos_end': str(df_oos.index[-1])[:10] if hasattr(df_oos.index[-1], 'strftime') else str(fold_end),
            'best_params': best_params,
            'is_metric': round(is_metric, 4),
            'oos_metric': round(oos_metric, 4),
            'is_trades': best_is.get('total_trades', 0),
            'oos_trades': oos_result.get('total_trades', 0),
            'is_cagr': round(float(best_is.get('cagr', 0)), 2),
            'oos_cagr': round(float(oos_result.get('cagr', 0)), 2),
            'is_dd': round(float(best_is.get('max_drawdown_pct', 0)), 2),
            'oos_dd': round(float(oos_result.get('max_drawdown_pct', 0)), 2),
            'fold_efficiency': round(fold_efficiency, 3),
        })

        done += 1
        if progress_callback:
            try:
                progress_callback(done, total_steps)
            except Exception:
                pass

    if not folds:
        return {'error': 'Ningún fold generó resultados válidos.'}

    # ─── Métricas Globales Walk-Forward ───
    efficiencies = [f['fold_efficiency'] for f in folds]
    is_metrics = [f['is_metric'] for f in folds if f['is_metric'] != -999]
    oos_metrics = [f['oos_metric'] for f in folds if f['oos_metric'] != -999]
    oos_cagrs = [f['oos_cagr'] for f in folds]

    wf_efficiency = float(np.mean(efficiencies)) if efficiencies else 0.0
    is_mean = float(np.mean(is_metrics)) if is_metrics else 0.0
    oos_mean = float(np.mean(oos_metrics)) if oos_metrics else 0.0
    oos_cagr_mean = float(np.mean(oos_cagrs)) if oos_cagrs else 0.0
    oos_positive_folds = sum(1 for m in oos_metrics if m > 0)
    overfitting_detected = wf_efficiency < 0.5 or oos_positive_folds < len(folds) // 2

    # Parámetros más frecuentes en IS (consenso robusto entre folds)
    all_param_keys = list(folds[0]['best_params'].keys()) if folds else []
    consensus_params: Dict[str, Any] = {}
    for key in all_param_keys:
        values = [str(f['best_params'].get(key, '')) for f in folds]
        most_common = Counter(values).most_common(1)
        consensus_params[key] = most_common[0][0] if most_common else ''

    return {
        'status': 'success',
        'n_folds': len(folds),
        'folds': folds,
        'wf_efficiency': round(wf_efficiency, 3),
        'is_mean': round(is_mean, 4),
        'oos_mean': round(oos_mean, 4),
        'oos_cagr_mean': round(oos_cagr_mean, 2),
        'oos_positive_folds': oos_positive_folds,
        'overfitting_detected': overfitting_detected,
        'consensus_params': consensus_params,
        'optimize_metric': optimize_metric,
        'in_sample_pct': in_sample_pct,
        'n_splits': n_splits,
    }
