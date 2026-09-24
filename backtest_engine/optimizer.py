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


# Tope duro de combinaciones para un solo Grid Search. Cada combinación ejecuta un
# backtest completo sobre el histórico real; sin este límite, una malla con varios
# parámetros de rango amplio puede escalar a cientos de miles de combinaciones y
# colgar el servidor (CPU/memoria) durante minutos u horas sin forma de saberlo antes.
MAX_GRID_COMBINATIONS = 20_000


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
    sizing_config: Optional[Dict[str, Any]] = None,
    trade_start: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Ejecuta un backtest individual para una combinación específica de parámetros.
    `trade_start`: las velas previas solo calientan indicadores (ver Backtester).
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
                slippage_pct=slippage_pct,
                trade_start=trade_start
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
            eq_metrics = calculate_equity_curve_metrics(equity_curve['equity'], timeframe=strategy.timeframe)
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
            'profit_factor_reliable': bool(trade_metrics.get('profit_factor_reliable', False)),
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
            'profit_factor_reliable': False,
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

def rank_key(r: Dict[str, Any], optimize_metric: str) -> tuple:
    """Clave de orden del Grid Search (mayor = mejor): errores al final, luego sin operaciones."""
    if r.get('error'):
        return (-2, 0.0)
    # Una combinación sin operaciones no es un resultado: con la métrica Max Drawdown
    # (DD 0%) ganaba el ranking justamente por no operar nunca.
    if int(r.get('total_trades', 0) or 0) == 0:
        return (-1, 0.0)
    val = float(r.get(optimize_metric, -999))
    # Para drawdown: menor (menos negativo/menor % caída) es mejor
    if optimize_metric == 'max_drawdown_pct':
        val = -abs(val)
    # profit_factor con muestra insuficiente (ver metrics.MIN_TRADES_FOR_RELIABLE_PF)
    # no debe poder ganar el ranking solo por ser matemáticamente "inf" (0 perdedoras):
    # se penaliza por debajo de cualquier resultado confiable, sin tratarlo como error duro.
    if optimize_metric == 'profit_factor' and not r.get('profit_factor_reliable', False):
        val = -1.0
    return (0, val)


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
    cancel_event: Optional[Any] = None,
    trade_start: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """
    Ejecuta Grid Search de forma segura y multihilo.
    Retorna una lista de resultados ordenados de mejor a peor según `optimize_metric`.

    Soporta cancelación mediante `cancel_event`: al activarse, se cancelan todas las
    combinaciones aún no iniciadas, pero las que ya están corriendo en el ThreadPoolExecutor
    (hasta `max_workers` a la vez) terminan su backtest antes de detenerse por completo —
    no es una interrupción instantánea de esos hilos en ejecución.

    Lanza ValueError si el número de combinaciones excede MAX_GRID_COMBINATIONS, para
    evitar colgar el servidor con una malla de parámetros demasiado extensa.
    """
    results: List[Dict[str, Any]] = []
    total = count_combinations(param_ranges)
    if total > MAX_GRID_COMBINATIONS:
        raise ValueError(
            f"La combinatoria solicitada ({total:,} combinaciones) excede el máximo permitido "
            f"({MAX_GRID_COMBINATIONS:,}). Reduce el rango o aumenta el paso de los parámetros."
        )
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
                sizing_config,
                trade_start
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
                    'profit_factor_reliable': False,
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

    results.sort(key=lambda r: rank_key(r, optimize_metric), reverse=True)
    return results


# ──────────────────────────────────────────────────────────────
# Walk-Forward Validation
# ──────────────────────────────────────────────────────────────

# Velas previas que se usan solo para calentar indicadores en cada ventana del walk-forward.
WF_WARMUP_BARS = 300
# Tope de la eficiencia por fold: un ratio OOS/IS con un IS casi nulo explota (ej. 50x) y
# dominaba el promedio.
WF_EFFICIENCY_CLIP = 2.0


def _fold_efficiency(metric: str, is_metric: float, oos_metric: float) -> float:
    """
    Cuánto del resultado in-sample se conserva fuera de muestra (1.0 = todo).
    Antes, si IS y OOS eran ambos negativos la eficiencia valía 1.0 ("degradación
    proporcional"): una estrategia que pierde en los dos tramos salía como robusta. Y con
    max_drawdown_pct (siempre <= 0) todos los folds caían en ese caso.
    """
    if is_metric == -999 or oos_metric == -999:
        return 0.0
    if metric == 'max_drawdown_pct':
        is_dd, oos_dd = abs(is_metric), abs(oos_metric)
        eff = 1.0 if oos_dd <= 1e-9 else is_dd / oos_dd  # OOS con el doble de caída -> 0.5
    elif is_metric <= 0:
        # Sin ventaja in-sample no hay nada que "conservar".
        eff = 0.0
    else:
        eff = oos_metric / is_metric
    return float(max(-WF_EFFICIENCY_CLIP, min(WF_EFFICIENCY_CLIP, eff)))


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
    Cada tramo se simula con hasta WF_WARMUP_BARS velas previas que solo calientan los
    indicadores (no se opera en ellas ni cuentan en las métricas).

    Métricas clave del resultado:
      - wf_efficiency: media de la eficiencia por fold (ver _fold_efficiency). >0.7 = robusto, <0.5 = sobreajuste.
      - overfitting_detected: eficiencia < 0.5 o menos de la mitad de los folds ganan dinero OOS.
      - consensus_params: la combinación ganadora más repetida entre folds.
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

    def _d(i):
        ts = df.index[i]
        return str(ts)[:10] if hasattr(ts, 'strftime') else str(i)

    for fold_idx in range(n_splits):
        if cancel_event and getattr(cancel_event, 'is_set', lambda: False)():
            break

        fold_start = fold_idx * window_size
        fold_end = fold_start + window_size if fold_idx < n_splits - 1 else n
        if fold_end - fold_start < 20:
            continue

        split_abs = fold_start + int((fold_end - fold_start) * in_sample_pct)
        if split_abs - fold_start < 10 or fold_end - split_abs < 5:
            continue

        # Tramos con calentamiento: se opera en IS desde fold_start y en OOS desde split_abs.
        df_is = df.iloc[max(0, fold_start - WF_WARMUP_BARS):split_abs].copy()
        df_oos = df.iloc[max(0, split_abs - WF_WARMUP_BARS):fold_end].copy()

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
            trade_start=df.index[fold_start],
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
            trade_start=df.index[split_abs],
        )
        oos_metric = float(oos_result.get(optimize_metric, -999))
        fold_efficiency = _fold_efficiency(optimize_metric, is_metric, oos_metric)

        folds.append({
            'fold': fold_idx + 1,
            'is_start': _d(fold_start),
            'is_end': _d(split_abs - 1),
            'oos_start': _d(split_abs),
            'oos_end': _d(fold_end - 1),
            'best_params': best_params,
            'is_metric': round(is_metric, 4),
            'oos_metric': round(oos_metric, 4),
            'is_trades': best_is.get('total_trades', 0),
            'oos_trades': oos_result.get('total_trades', 0),
            'is_cagr': round(float(best_is.get('cagr', 0)), 2),
            'oos_cagr': round(float(oos_result.get('cagr', 0)), 2),
            'is_dd': round(float(best_is.get('max_drawdown_pct', 0)), 2),
            'oos_dd': round(float(oos_result.get('max_drawdown_pct', 0)), 2),
            'oos_net_pnl': round(float(oos_result.get('net_pnl', 0)), 6),
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
    # Fold "positivo" = ganó dinero fuera de muestra (antes: metric > 0, que con
    # max_drawdown_pct nunca se cumplía y marcaba siempre sobreajuste).
    oos_positive_folds = sum(1 for f in folds if f['oos_net_pnl'] > 0)
    overfitting_detected = wf_efficiency < 0.5 or oos_positive_folds * 2 < len(folds)

    # Consenso = combinación COMPLETA más repetida (desempate: mejor métrica OOS media).
    # Antes se tomaba la moda de cada parámetro por separado, lo que podía dar una
    # combinación que nunca ganó ningún fold.
    combos: Dict[tuple, List[float]] = {}
    for f in folds:
        combos.setdefault(tuple(sorted(f['best_params'].items())), []).append(f['oos_metric'])
    best_combo = max(combos.items(), key=lambda kv: (len(kv[1]), float(np.mean(kv[1]))))[0]

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
        'consensus_params': dict(best_combo),
        'consensus_folds': len(combos[best_combo]),
        'optimize_metric': optimize_metric,
        'in_sample_pct': in_sample_pct,
        'n_splits': n_splits,
        'warmup_bars': WF_WARMUP_BARS,
    }
