import pandas as pd
import numpy as np
import itertools
from typing import Dict, List, Tuple, Optional
from datetime import datetime
from strategy_engine.mle_thermometer import MLEThermometer

class MLEOptimizer:
    """
    Motor de Optimización Cuantitativa para el Filtro de Liquidez Estructural (MLE).
    Realiza una búsqueda de hiperparámetros sobre ventanas, pesos y umbrales de decisión
    para encontrar la configuración con mayor alfa y poder predictivo Long/Short en BTC.
    """
    def __init__(self, thermometer: Optional[MLEThermometer] = None):
        self.thermometer = thermometer or MLEThermometer(z_score_window=365)

    def generate_weight_combinations(self, step: int = 10) -> List[Tuple[int, int, int]]:
        """Genera todas las combinaciones de pesos enteros (SSR, INF, LEV) que sumen 100."""
        combos = []
        for ssr in range(0, 101, step):
            for inf in range(0, 101 - ssr, step):
                lev = 100 - ssr - inf
                if lev >= 0:
                    combos.append((ssr, inf, lev))
        return combos

    def _compute_component_series(self, stables_mc: pd.Series, btc_price: pd.Series, oi: pd.Series, window: int) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """Calcula las 3 series de componentes normalizadas (0-100) para una ventana dada."""
        # 1. SSR Component
        df_ssr = pd.concat([stables_mc, btc_price], axis=1, join='inner').dropna()
        df_ssr.columns = ['stable_mc', 'btc_price']
        df_ssr['btc_mc'] = df_ssr['btc_price'] * 19_700_000
        df_ssr['ssr'] = df_ssr['btc_mc'] / df_ssr['stable_mc']
        
        w = min(window, len(df_ssr))
        rolling_mean = df_ssr['ssr'].rolling(window=w).mean()
        rolling_std = df_ssr['ssr'].rolling(window=w).std().replace(0, np.nan)
        z_score = ((df_ssr['ssr'] - rolling_mean) / rolling_std).fillna(0)
        n_ssr = 100 - ((z_score.clip(-2, 2) + 2) / 4) * 100

        # 2. Inflows Velocity Component
        delta = stables_mc.diff()
        ema_7 = delta.ewm(span=7, adjust=False).mean()
        roll_min = ema_7.rolling(window=w).min()
        roll_max = ema_7.rolling(window=w).max()
        denom_inf = (roll_max - roll_min).replace(0, np.nan)
        n_inf = (((ema_7 - roll_min) / denom_inf) * 100).fillna(50)

        # 3. Leverage Ratio Component
        df_lev = pd.concat([oi, stables_mc], axis=1, join='inner').dropna()
        df_lev.columns = ['oi', 'stable_mc']
        df_lev['lev_ratio'] = df_lev['oi'] / df_lev['stable_mc']
        roll_min_lev = df_lev['lev_ratio'].rolling(window=w).min()
        roll_max_lev = df_lev['lev_ratio'].rolling(window=w).max()
        denom_lev = (roll_max_lev - roll_min_lev).replace(0, np.nan)
        n_lev = (100 - (((df_lev['lev_ratio'] - roll_min_lev) / denom_lev) * 100)).fillna(50)

        return n_ssr, n_inf, n_lev

    def run_optimization(
        self,
        windows: List[int] = [60, 90, 180, 365, 730],
        weight_step: int = 10,
        objective: str = 'sharpe',  # 'sharpe', 'return', 'win_rate', 'correlation'
        mode: str = 'long_short',   # 'long_short', 'long_only', 'short_only'
        eval_days: int = 730
    ) -> Dict:
        """
        Ejecuta el backtesting exhaustivo del espacio de parámetros.
        Retorna ranking, mejor configuración y análisis de sensibilidad.
        """
        # 1. Descargar datos base
        stables_mc = self.thermometer._fetch_defillama_stablecoins()
        btc_price = self.thermometer._fetch_binance_btc_prices()
        oi = self.thermometer._fetch_binance_open_interest()

        if stables_mc.empty or btc_price.empty:
            return {"status": "error", "message": "No se pudieron obtener datos históricos de APIs."}

        stables_mc.index = stables_mc.index.normalize()
        btc_price.index = btc_price.index.normalize()
        oi.index = oi.index.normalize() if not oi.empty else stables_mc.index

        # Si OI está vacío, crear serie aproximada
        if oi.empty:
            oi = btc_price * 1000

        # Filtrar al rango de días deseado
        if eval_days and eval_days > 0:
            cutoff = datetime.now() - pd.Timedelta(days=eval_days)
            stables_mc = stables_mc[stables_mc.index >= cutoff]
            btc_price = btc_price[btc_price.index >= cutoff]
            oi = oi[oi.index >= cutoff]

        btc_returns = btc_price.pct_change().fillna(0)
        btc_fwd_7d = (btc_price.shift(-7) / btc_price - 1).fillna(0)

        weight_combos = self.generate_weight_combinations(step=weight_step)
        threshold_combos = [
            (65, 35),
            (70, 30),
            (60, 40),
            (75, 25),
            (55, 45)
        ]

        results = []
        
        # Para sensibilidad individual
        indicator_correlations = {'ssr': [], 'inf': [], 'lev': []}

        for w in windows:
            if len(btc_price) < w // 2:
                continue
            
            n_ssr, n_inf, n_lev = self._compute_component_series(stables_mc, btc_price, oi, w)
            
            # Alinear todas las series con retornos de BTC
            df_aligned = pd.concat([
                n_ssr.rename('ssr'),
                n_inf.rename('inf'),
                n_lev.rename('lev'),
                btc_price.rename('price'),
                btc_returns.rename('ret'),
                btc_fwd_7d.rename('fwd_7d')
            ], axis=1).dropna()

            if len(df_aligned) < 30:
                continue

            ssr_corr = df_aligned['ssr'].corr(df_aligned['fwd_7d'])
            inf_corr = df_aligned['inf'].corr(df_aligned['fwd_7d'])
            lev_corr = df_aligned['lev'].corr(df_aligned['fwd_7d'])
            if not np.isnan(ssr_corr): indicator_correlations['ssr'].append(ssr_corr)
            if not np.isnan(inf_corr): indicator_correlations['inf'].append(inf_corr)
            if not np.isnan(lev_corr): indicator_correlations['lev'].append(lev_corr)

            # Vectorizar cálculos para las combinaciones de pesos
            ssr_arr = df_aligned['ssr'].values
            inf_arr = df_aligned['inf'].values
            lev_arr = df_aligned['lev'].values
            ret_arr = df_aligned['ret'].values
            fwd_arr = df_aligned['fwd_7d'].values

            for ssr_w, inf_w, lev_w in weight_combos:
                theta_arr = (ssr_w * ssr_arr + inf_w * inf_arr + lev_w * lev_arr) / 100.0
                
                # Calcular correlación de forma segura
                corr_fwd = 0.0
                if len(theta_arr) > 10 and np.std(theta_arr[:-7]) > 1e-6 and np.std(fwd_arr[:-7]) > 1e-6:
                    c = np.corrcoef(theta_arr[:-7], fwd_arr[:-7])[0, 1]
                    if not np.isnan(c):
                        corr_fwd = float(c)

                for th_long, th_short in threshold_combos:
                    # Posiciones de la estrategia: +1 (Long), -1 (Short), 0 (Cash)
                    positions = np.zeros(len(theta_arr))
                    
                    if mode == 'long_short':
                        positions[theta_arr > th_long] = 1.0
                        positions[theta_arr < th_short] = -1.0
                    elif mode == 'long_only':
                        positions[theta_arr > th_long] = 1.0
                    elif mode == 'short_only':
                        positions[theta_arr < th_short] = -1.0

                    # Shift positions by 1 day (operar con la señal de ayer)
                    strat_positions = np.roll(positions, 1)
                    strat_positions[0] = 0.0

                    strat_returns = strat_positions * ret_arr
                    
                    # Métricas Cuantitativas
                    n_days = len(strat_returns)
                    mean_ret = np.mean(strat_returns)
                    std_ret = np.std(strat_returns)
                    sharpe = (mean_ret / std_ret * np.sqrt(365)) if std_ret > 1e-8 else 0.0
                    
                    cum_ret = np.prod(1.0 + strat_returns) - 1.0
                    
                    # Drawdown
                    cum_curve = np.cumprod(1.0 + strat_returns)
                    peak = np.maximum.accumulate(cum_curve)
                    dd = (cum_curve - peak) / peak
                    max_dd = float(np.min(dd)) * 100.0

                    # Win Rate en Long y en Short
                    long_mask = (strat_positions == 1.0)
                    short_mask = (strat_positions == -1.0)
                    
                    win_rate_long = float(np.mean(ret_arr[long_mask] > 0) * 100.0) if np.sum(long_mask) > 0 else 0.0
                    win_rate_short = float(np.mean(ret_arr[short_mask] < 0) * 100.0) if np.sum(short_mask) > 0 else 0.0
                    
                    avg_long_ret = float(np.mean(ret_arr[long_mask]) * 100.0) if np.sum(long_mask) > 0 else 0.0
                    avg_short_ret = float(np.mean(-ret_arr[short_mask]) * 100.0) if np.sum(short_mask) > 0 else 0.0

                    score = sharpe
                    if objective == 'return':
                        score = cum_ret
                    elif objective == 'win_rate':
                        score = (win_rate_long + win_rate_short) / (2.0 if mode == 'long_short' else 1.0)
                    elif objective == 'correlation':
                        score = corr_fwd

                    results.append({
                        "window": int(w),
                        "w_ssr": int(ssr_w),
                        "w_inf": int(inf_w),
                        "w_lev": int(lev_w),
                        "th_long": int(th_long),
                        "th_short": int(th_short),
                        "sharpe": round(float(sharpe), 2),
                        "total_return_pct": round(float(cum_ret * 100.0), 1),
                        "max_drawdown_pct": round(abs(max_dd), 1),
                        "win_rate_long": round(win_rate_long, 1),
                        "win_rate_short": round(win_rate_short, 1),
                        "avg_long_ret": round(avg_long_ret, 2),
                        "avg_short_ret": round(avg_short_ret, 2),
                        "correlation_fwd": round(corr_fwd, 3),
                        "score": round(float(score), 4),
                        "long_days": int(np.sum(long_mask)),
                        "short_days": int(np.sum(short_mask))
                    })

        # Ordenar por el score objetivo descendente
        results.sort(key=lambda x: x["score"], reverse=True)
        top_results = results[:25]
        best_config = top_results[0] if top_results else None

        # Sensibilidad promedio de cada indicador
        avg_corrs = {
            "ssr_power": round(float(np.mean(indicator_correlations['ssr'])), 3) if indicator_correlations['ssr'] else 0.0,
            "inf_velocity": round(float(np.mean(indicator_correlations['inf'])), 3) if indicator_correlations['inf'] else 0.0,
            "lev_risk": round(float(np.mean(indicator_correlations['lev'])), 3) if indicator_correlations['lev'] else 0.0
        }

        return {
            "status": "success",
            "total_evaluated": len(results),
            "best_config": best_config,
            "top_results": top_results,
            "indicator_impact": avg_corrs,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
