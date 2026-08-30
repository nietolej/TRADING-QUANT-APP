"""
robustness_analyzer.py — Motor Cuantitativo Vectorizado de Análisis de Robustez y Sensibilidad de Parámetros.

Calcula a ultra alta velocidad (Vectorizado con NumPy):
1. Configuración Más Robusta (Meseta / Plateau Analysis de vecinos en el espacio de parámetros).
2. Parámetro Más Influyente (% de importancia y sensibilidad mediante descomposición de varianza ANOVA).
3. Media, Desviación Estándar, Coeficiente de Variación y Dispersión de cada parámetro (PnL, DD, Sharpe).
4. Índice Global de Robustez (Robustness Score 0-100) y detección de Sobreajuste (Overfitting Risk).
5. Generación de Matrices Pivote para Mapas de Calor 2D y Superficies 3D de Parámetros.
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
    Implementación vectorizada de alto rendimiento para miles de combinaciones.
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
    N = len(df)
    
    # ─────────────────────────────────────────────────────────────
    # A. CONFIGURACIÓN MÁS ROBUSTA (MESETA / VECTORIZED PLATEAU)
    # ─────────────────────────────────────────────────────────────
    best_peak_idx = df[target_metric].idxmax() if target_metric != 'max_drawdown_pct' else df['max_drawdown_pct'].abs().idxmin()
    best_peak_row = df.loc[best_peak_idx]

    param_steps = {}
    for k in param_keys:
        st = float(param_ranges[k].get('step', 1.0))
        param_steps[k] = st if st > 0 else 1.0

    param_vals = df[param_keys].to_numpy(dtype=np.float64)
    steps = np.array([param_steps[k] for k in param_keys], dtype=np.float64)
    norm_coords = param_vals / steps

    metric_arr = df[target_metric].to_numpy(dtype=np.float64)
    pnl_arr = df['net_pnl'].to_numpy(dtype=np.float64)
    dd_arr = df['max_drawdown_pct'].abs().to_numpy(dtype=np.float64)

    # Matriz booleana de vecindad vectorizada (alta velocidad)
    if N <= 3000:
        diff = np.abs(norm_coords[:, None, :] - norm_coords[None, :, :])
        is_neigh = np.all(diff <= 1.05, axis=-1)
    else:
        is_neigh = np.zeros((N, N), dtype=bool)
        chunk_size = 500
        for i in range(0, N, chunk_size):
            chunk = norm_coords[i:i+chunk_size]
            diff_chunk = np.abs(chunk[:, None, :] - norm_coords[None, :, :])
            is_neigh[i:i+chunk_size] = np.all(diff_chunk <= 1.05, axis=-1)

    neigh_counts = is_neigh.sum(axis=1)
    safe_counts = np.maximum(1, neigh_counts)
    
    neigh_metric_mean = (is_neigh @ metric_arr) / safe_counts
    neigh_pnl_mean = (is_neigh @ pnl_arr) / safe_counts
    neigh_dd_mean = (is_neigh @ dd_arr) / safe_counts

    neigh_metric_sq_mean = (is_neigh @ (metric_arr ** 2)) / safe_counts
    neigh_metric_std = np.sqrt(np.maximum(0.0, neigh_metric_sq_mean - (neigh_metric_mean ** 2)))
    
    neigh_pnl_sq_mean = (is_neigh @ (pnl_arr ** 2)) / safe_counts
    neigh_pnl_std = np.sqrt(np.maximum(0.0, neigh_pnl_sq_mean - (neigh_pnl_mean ** 2)))

    if target_metric == 'max_drawdown_pct':
        robust_scores = -(neigh_dd_mean + 0.5 * neigh_metric_std)
    else:
        robust_scores = neigh_metric_mean - 0.5 * neigh_metric_std

    coverage_factor = np.minimum(1.0, neigh_counts / (2 ** len(param_keys)))
    adjusted_robust_scores = robust_scores * (0.7 + 0.3 * coverage_factor)

    plateau_scores = []
    for idx in range(N):
        row = df.iloc[idx]
        plateau_scores.append({
            'index': idx,
            'robust_score': float(adjusted_robust_scores[idx]),
            'neigh_metric_mean': float(neigh_metric_mean[idx]),
            'neigh_metric_std': float(neigh_metric_std[idx]),
            'neigh_pnl_mean': float(neigh_pnl_mean[idx]),
            'neigh_pnl_std': float(neigh_pnl_std[idx]),
            'neigh_dd_mean': float(neigh_dd_mean[idx]),
            'neighbors_count': int(neigh_counts[idx]),
            'params': {k: row[k] for k in param_keys},
            'self_sharpe': float(row['sharpe_ratio']),
            'self_pnl': float(row['net_pnl']),
            'self_dd': float(row['max_drawdown_pct']),
            'self_cagr': float(row['cagr']),
            'raw_result': row['_raw_result']
        })

    plateau_scores.sort(key=lambda x: x['robust_score'], reverse=True)
    best_robust = plateau_scores[0]

    # ─────────────────────────────────────────────────────────────
    # B. SENSIBILIDAD E IMPORTANCIA DE CADA PARÁMETRO (% ANOVA)
    # ─────────────────────────────────────────────────────────────
    total_var = float(df[target_metric].var()) if len(df) > 1 else 1e-6
    param_importance = {}
    
    if total_var > 1e-9:
        for k in param_keys:
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
    profit_rate = float((df['net_pnl'] > 0).mean()) * 100
    avg_cv = float(np.mean([np.mean([v['pnl_cv'] for v in vals]) for vals in param_stats.values()]))
    stability_factor = max(0.0, min(100.0, 100.0 - avg_cv * 20))
    sharpe_positive_rate = float((df['sharpe_ratio'] > 0).mean()) * 100
    mean_dd = float(df['max_drawdown_pct'].abs().mean())
    dd_score = max(0.0, min(100.0, (100.0 - mean_dd * 1.5)))

    global_score = round(
        0.35 * profit_rate +
        0.25 * sharpe_positive_rate +
        0.20 * stability_factor +
        0.20 * dd_score
    , 1)
    global_score = max(0.0, min(100.0, global_score))

    if global_score >= 80:
        health_status = 'ALTA ROBUSTEZ (Excelente Tolerancia)'
        health_color = '#10b981'
        health_desc = 'El espacio de parámetros forma mesetas amplias y estables. El riesgo de sobreajuste es muy bajo y la estrategia tolera cambios en el mercado.'
    elif global_score >= 60:
        health_status = 'ROBUSTEZ MODERADA (Aceptable)'
        health_color = '#f59e0b'
        health_desc = 'La mayoría de configuraciones son rentables, pero existen zonas de caída. Se recomienda operar exclusivamente dentro de la meseta más robusta.'
    elif global_score >= 40:
        health_status = 'SENSIBILIDAD MEDIA-ALTA'
        health_color = '#f97316'
        health_desc = 'La estrategia depende fuertemente de valores precisos de sus parámetros. Requiere monitoreo continuo y filtros de curva de capital.'
    else:
        health_status = 'FRÁGIL / ALTO RIESGO DE OVERFITTING'
        health_color = '#ef4444'
        health_desc = 'Los mejores resultados corresponden a picos aislados (islas de suerte). Gran parte del espacio de parámetros es perdedor.'

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
        'total_combinations': len(df),
        'param_keys': param_keys
    }


def generate_heatmap_matrix(
    results: List[Dict[str, Any]],
    param_x: str,
    param_y: str,
    metric: str = 'sharpe_ratio'
) -> Dict[str, Any]:
    """
    Genera la matriz pivote (Z, X, Y) para visualización en Mapa de Calor o Superficie 3D.
    """
    records = []
    for r in results:
        if r.get('error'):
            continue
        row = dict(r['params'])
        row[metric] = float(r.get(metric, 0))
        records.append(row)
        
    if not records:
        return {'x': [], 'y': [], 'z': []}
        
    df = pd.DataFrame(records)
    if param_x not in df.columns or param_y not in df.columns:
        return {'x': [], 'y': [], 'z': []}
        
    pivot = df.pivot_table(index=param_y, columns=param_x, values=metric, aggfunc='mean')
    x_vals = [int(v) if float(v) == int(v) else float(v) for v in pivot.columns]
    y_vals = [int(v) if float(v) == int(v) else float(v) for v in pivot.index]
    z_vals = pivot.fillna(0).values.tolist()
    
    return {
        'x': x_vals,
        'y': y_vals,
        'z': z_vals,
        'param_x': param_x,
        'param_y': param_y,
        'metric': metric
    }
