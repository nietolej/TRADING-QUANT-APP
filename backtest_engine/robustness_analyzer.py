"""
robustness_analyzer.py — Motor Cuantitativo de Análisis de Robustez y Sensibilidad de Parámetros.

Calcula:
1. Configuración Más Robusta (Meseta / Plateau Analysis de vecinos en el espacio de parámetros).
2. Parámetro Más Influyente (% de importancia y sensibilidad mediante descomposición de varianza).
3. Media, Desviación Estándar, Coeficiente de Variación y Dispersión de cada parámetro (PnL, DD, Sharpe).
4. Índice Global de Robustez (Robustness Score 0-100) y detección de Sobreajuste (Overfitting Risk).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple
import pandas as pd
import numpy as np


def analyze_robustness(
    results: List[Dict[str, Any]],
    param_ranges: Dict[str, Dict[str, float]],
    target_metric: str = 'sharpe_ratio'
) -> Dict[str, Any]:
    """
    Analiza la robustez global del espacio de parámetros explorado en el Grid Search.
    """
    if not results or len(results) < 2:
        return {'status': 'insufficient_data'}

    # 1. Convertir resultados a DataFrame
    records = []
    for r in results:
        if r.get('error'):
            continue
        row = dict(r['params'])
        row['sharpe_ratio'] = float(r.get('sharpe_ratio', -999))
        row['cagr'] = float(r.get('cagr', -999))
        row['max_drawdown_pct'] = float(r.get('max_drawdown_pct', 0))
        row['net_pnl'] = float(r.get('net_pnl', 0))
        row['profit_factor'] = float(r.get('profit_factor', 0))
        row['percent_profitable'] = float(r.get('percent_profitable', 0))
        row['total_trades'] = int(r.get('total_trades', 0))
        row['_raw_result'] = r
        records.append(row)

    if not records:
        return {'status': 'no_valid_records'}

    df = pd.DataFrame(records)
    param_keys = list(param_ranges.keys())
    
    # ─────────────────────────────────────────────────────────────
    # A. CONFIGURACIÓN MÁS ROBUSTA (MESETA / NEIGHBORHOOD PLATEAU)
    # ─────────────────────────────────────────────────────────────
    # Para cada configuración, encontramos sus vecinos a +-1 paso en el grid
    # y calculamos la media y estabilidad del vecindario.
    
    best_peak_idx = df[target_metric].idxmax() if target_metric != 'max_drawdown_pct' else df['max_drawdown_pct'].abs().idxmin()
    best_peak_row = df.loc[best_peak_idx]

    # Calcular pasos para cada parámetro
    param_steps = {}
    for k in param_keys:
        st = float(param_ranges[k].get('step', 1.0))
        param_steps[k] = st if st > 0 else 1.0

    plateau_scores = []
    for idx, row in df.iterrows():
        # Filtro de vecindad: distancias <= 1.05 * step en cada parámetro
        mask = pd.Series(True, index=df.index)
        for k in param_keys:
            dist = (df[k] - row[k]).abs()
            mask &= (dist <= param_steps[k] * 1.05)
            
        neighbors = df[mask]
        n_count = len(neighbors)
        
        neigh_metric_mean = float(neighbors[target_metric].mean())
        neigh_metric_std = float(neighbors[target_metric].std()) if n_count > 1 else 0.0
        neigh_pnl_mean = float(neighbors['net_pnl'].mean())
        neigh_pnl_std = float(neighbors['net_pnl'].std()) if n_count > 1 else 0.0
        neigh_dd_mean = float(neighbors['max_drawdown_pct'].abs().mean())
        
        # Robustness Score de la meseta:
        # Recompensa alta media de vecinos y penaliza alta dispersión/varianza
        # Score = mean - 0.5 * std (para Sharpe)
        if target_metric == 'max_drawdown_pct':
            robust_score = -(neigh_dd_mean + 0.5 * neigh_metric_std)
        else:
            robust_score = neigh_metric_mean - 0.5 * neigh_metric_std

        # Penalización si el vecindario tiene pocas simulaciones (ej. esquinas extremas del grid)
        coverage_factor = min(1.0, n_count / (2 ** len(param_keys)))
        adjusted_robust_score = robust_score * (0.7 + 0.3 * coverage_factor)

        plateau_scores.append({
            'index': idx,
            'robust_score': adjusted_robust_score,
            'neigh_metric_mean': neigh_metric_mean,
            'neigh_metric_std': neigh_metric_std,
            'neigh_pnl_mean': neigh_pnl_mean,
            'neigh_pnl_std': neigh_pnl_std,
            'neigh_dd_mean': neigh_dd_mean,
            'neighbors_count': n_count,
            'params': {k: row[k] for k in param_keys},
            'self_sharpe': float(row['sharpe_ratio']),
            'self_pnl': float(row['net_pnl']),
            'self_dd': float(row['max_drawdown_pct']),
            'self_cagr': float(row['cagr']),
            'raw_result': row['_raw_result']
        })

    # Seleccionar la mejor meseta
    plateau_scores.sort(key=lambda x: x['robust_score'], reverse=True)
    best_robust = plateau_scores[0]

    # ─────────────────────────────────────────────────────────────
    # B. SENSIBILIDAD E IMPORTANCIA DE CADA PARÁMETRO (% ANOVA)
    # ─────────────────────────────────────────────────────────────
    # Cuánta de la varianza total de la métrica es explicada por cada parámetro
    total_var = float(df[target_metric].var()) if len(df) > 1 else 1e-6
    param_importance = {}
    
    if total_var > 1e-9:
        for k in param_keys:
            # Varianza entre las medias de los grupos del parámetro k
            group_means = df.groupby(k)[target_metric].mean()
            between_group_var = float(group_means.var()) if len(group_means) > 1 else 0.0
            param_importance[k] = max(0.0, between_group_var)
        
        sum_imp = sum(param_importance.values())
        if sum_imp > 0:
            param_importance_pct = {k: round((v / sum_imp) * 100, 1) for k, v in param_importance.items()}
        else:
            param_importance_pct = {k: round(100.0 / len(param_keys), 1) for k in param_keys}
    else:
        param_importance_pct = {k: round(100.0 / len(param_keys), 1) for k in param_keys}

    # Ordenar por importancia descendente
    sorted_importance = sorted(param_importance_pct.items(), key=lambda x: x[1], reverse=True)
    most_influential_param = sorted_importance[0][0]
    least_influential_param = sorted_importance[-1][0]

    # ─────────────────────────────────────────────────────────────
    # C. ESTADÍSTICAS DE MEDIA Y DISPERSIÓN POR PARÁMETRO
    # ─────────────────────────────────────────────────────────────
    param_stats = {}
    for k in param_keys:
        unique_vals = sorted(df[k].unique())
        val_records = []
        for val in unique_vals:
            subset = df[df[k] == val]
            pnl_mean = float(subset['net_pnl'].mean())
            pnl_std = float(subset['net_pnl'].std()) if len(subset) > 1 else 0.0
            sharpe_mean = float(subset['sharpe_ratio'].mean())
            sharpe_std = float(subset['sharpe_ratio'].std()) if len(subset) > 1 else 0.0
            dd_mean = float(subset['max_drawdown_pct'].abs().mean())
            dd_worst = float(subset['max_drawdown_pct'].abs().max())
            win_rate_mean = float(subset['percent_profitable'].mean())
            profitable_pct = float((subset['net_pnl'] > 0).mean() * 100)
            
            # Coeficiente de variación: std / |mean|
            cv = (pnl_std / abs(pnl_mean)) if abs(pnl_mean) > 1e-4 else 0.0

            val_records.append({
                'val': int(val) if float(val) == int(val) else val,
                'count': len(subset),
                'pnl_mean': round(pnl_mean, 2),
                'pnl_std': round(pnl_std, 2),
                'pnl_cv': round(cv, 2),
                'sharpe_mean': round(sharpe_mean, 3),
                'sharpe_std': round(sharpe_std, 3),
                'dd_mean': round(dd_mean, 2),
                'dd_worst': round(dd_worst, 2),
                'win_rate_mean': round(win_rate_mean, 2),
                'profitable_pct': round(profitable_pct, 1),
                'raw_pnl_list': subset['net_pnl'].tolist(),
                'raw_sharpe_list': subset['sharpe_ratio'].tolist()
            })
        param_stats[k] = val_records

    # ─────────────────────────────────────────────────────────────
    # D. ÍNDICE GLOBAL DE ROBUSTEZ (ROBUSTNESS SCORE 0 - 100)
    # ─────────────────────────────────────────────────────────────
    # 1. Tasa de rentabilidad general (% de combinaciones con PnL > 0)
    profit_rate = float((df['net_pnl'] > 0).mean()) * 100
    
    # 2. Coeficiente de variación medio del PnL entre parámetros
    avg_cv = float(np.mean([np.mean([v['pnl_cv'] for v in vals]) for vals in param_stats.values()]))
    stability_factor = max(0.0, min(100.0, 100.0 - avg_cv * 20))
    
    # 3. Consistencia de Sharpe (> 0)
    sharpe_positive_rate = float((df['sharpe_ratio'] > 0).mean()) * 100

    # 4. Drawdown control (penaliza si el max drawdown supera el 40%)
    mean_dd = float(df['max_drawdown_pct'].abs().mean())
    dd_score = max(0.0, min(100.0, (100.0 - mean_dd * 1.5)))

    # Puntuación compuesta ponderada
    global_score = round(
        0.35 * profit_rate +
        0.25 * sharpe_positive_rate +
        0.20 * stability_factor +
        0.20 * dd_score
    , 1)
    global_score = max(0.0, min(100.0, global_score))

    if global_score >= 80:
        health_status = 'ALTA ROBUSTEZ (Excelente Tolerancia)'
        health_color = '#10b981' # Emerald
        health_desc = 'El espacio de parámetros forma mesetas amplias y estables. El riesgo de sobreajuste es muy bajo y la estrategia tolera cambios en las condiciones de mercado.'
    elif global_score >= 60:
        health_status = 'ROBUSTEZ MODERADA (Aceptable)'
        health_color = '#f59e0b' # Amber
        health_desc = 'La mayoría de configuraciones son rentables, pero existen zonas de caída. Se recomienda operar exclusivamente dentro de la meseta más robusta.'
    elif global_score >= 40:
        health_status = 'SENSIBILIDAD MEDIA-ALTA'
        health_color = '#f97316' # Orange
        health_desc = 'La estrategia depende fuertemente de valores precisos de sus parámetros. Requiere monitoreo continuo y filtros de curva de capital.'
    else:
        health_status = 'FRÁGIL / ALTO RIESGO DE OVERFITTING'
        health_color = '#ef4444' # Red
        health_desc = 'Los mejores resultados corresponden a picos aislados (islas de suerte). Gran parte del espacio de parámetros es perdedor.'

    # Comparación Pico vs Meseta
    peak_params = {k: (int(best_peak_row[k]) if float(best_peak_row[k]) == int(best_peak_row[k]) else float(best_peak_row[k])) for k in param_keys}
    robust_params = {k: (int(best_robust['params'][k]) if float(best_robust['params'][k]) == int(best_robust['params'][k]) else float(best_robust['params'][k])) for k in param_keys}
    is_same = (peak_params == robust_params)

    return {
        'status': 'success',
        'global_score': global_score,
        'health_status': health_status,
        'health_color': health_color,
        'health_desc': health_desc,
        'profit_rate': round(profit_rate, 1),
        'sharpe_positive_rate': round(sharpe_positive_rate, 1),
        'mean_dd': round(mean_dd, 2),
        'most_influential_param': most_influential_param,
        'most_influential_pct': param_importance_pct.get(most_influential_param, 0),
        'least_influential_param': least_influential_param,
        'least_influential_pct': param_importance_pct.get(least_influential_param, 0),
        'param_importance_pct': sorted_importance,
        'param_stats': param_stats,
        'best_robust': best_robust,
        'best_peak': {
            'params': peak_params,
            'sharpe': float(best_peak_row['sharpe_ratio']),
            'pnl': float(best_peak_row['net_pnl']),
            'dd': float(best_peak_row['max_drawdown_pct']),
            'cagr': float(best_peak_row['cagr']),
            'trades': int(best_peak_row['total_trades']),
            'win_rate': float(best_peak_row['percent_profitable']),
            'raw_result': best_peak_row['_raw_result']
        },
        'is_same_as_peak': is_same,
        'total_combinations': len(df)
    }
