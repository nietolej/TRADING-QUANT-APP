"""
Módulo de Análisis Cuantitativo de Halvings de Bitcoin (BTC Halving Analyzer)
Gestiona la descarga y caché de datos históricos de Bitcoin desde 2011,
la indexación relativa por ciclos de Halving (H=0, H+1, H+2, etc.),
el cálculo de métricas de impacto, matrices de correlación y modelos de proyección.
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Definición canónica de los eventos de Halving de Bitcoin
HALVING_EVENTS = [
    {
        "id": "H1",
        "name": "Halving 1 (2012)",
        "date": "2012-11-28",
        "block": 210000,
        "reward_before": 50.0,
        "reward_after": 25.0,
        "color": "#38bdf8",  # Sky Blue
        "is_completed": True,
        "cycle_bottom_date": "2011-11-18",
        "cycle_peak_date": "2013-11-29"
    },
    {
        "id": "H2",
        "name": "Halving 2 (2016)",
        "date": "2016-07-09",
        "block": 420000,
        "reward_before": 25.0,
        "reward_after": 12.5,
        "color": "#a855f7",  # Purple
        "is_completed": True,
        "cycle_bottom_date": "2015-01-14",
        "cycle_peak_date": "2017-12-16"
    },
    {
        "id": "H3",
        "name": "Halving 3 (2020)",
        "date": "2020-05-11",
        "block": 630000,
        "reward_before": 12.5,
        "reward_after": 6.25,
        "color": "#10b981",  # Emerald Green
        "is_completed": True,
        "cycle_bottom_date": "2018-12-15",
        "cycle_peak_date": "2021-11-10"
    },
    {
        "id": "H4",
        "name": "Halving 4 (2024 - Actual)",
        "date": "2024-04-19",
        "block": 840000,
        "reward_before": 6.25,
        "reward_after": 3.125,
        "color": "#f59e0b",  # Amber / Gold
        "is_completed": False,
        "cycle_bottom_date": "2022-11-21",
        "cycle_peak_date": None
    },
    {
        "id": "H5",
        "name": "Halving 5 (2028 - Estimado)",
        "date": "2028-04-17",
        "block": 1050000,
        "reward_before": 3.125,
        "reward_after": 1.5625,
        "color": "#ec4899",  # Pink
        "is_completed": False,
        "cycle_bottom_date": None,
        "cycle_peak_date": None
    }
]

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE_FILE = os.path.join(DATA_DIR, "btc_daily_historical.parquet")


class BTCHalvingAnalyzer:
    """
    Analizador cuantitativo de los ciclos de Halving de Bitcoin.
    """

    def __init__(self, data_cache_file: str = CACHE_FILE):
        self.cache_file = data_cache_file
        self.df_btc: Optional[pd.DataFrame] = None
        self.halvings = [h for h in HALVING_EVENTS if h["id"] != "H5"]  # Ciclos pasados y actual
        self.ensure_data_loaded()

    def fetch_historical_btc_data(self, force_refresh: bool = False) -> pd.DataFrame:
        """
        Descarga y compila la serie histórica de precios diarios de Bitcoin desde 2011 hasta la fecha actual.
        Combina Bitstamp (vía CCXT para 2011-2014) y Yahoo Finance (2014-actualidad) / Binance.
        """
        csv_fallback = self.cache_file.replace('.parquet', '.csv')
        if not force_refresh:
            if os.path.exists(self.cache_file):
                try:
                    df = pd.read_parquet(self.cache_file)
                    if not df.empty and "close" in df.columns:
                        last_date = pd.to_datetime(df['timestamp'].max())
                        if last_date.tzinfo is None:
                            last_date = last_date.tz_localize('UTC')
                        now_utc = datetime.now(timezone.utc)
                        if (now_utc - last_date).days <= 2:
                            logger.info(f"Cargados {len(df)} registros históricos de BTC desde caché parquet.")
                            return df
                except Exception as e:
                    logger.debug(f"Error al leer caché parquet: {e}")

            if os.path.exists(csv_fallback):
                try:
                    df = pd.read_csv(csv_fallback)
                    if not df.empty and "close" in df.columns:
                        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
                        last_date = pd.to_datetime(df['timestamp'].max())
                        now_utc = datetime.now(timezone.utc)
                        if (now_utc - last_date).days <= 2:
                            logger.info(f"Cargados {len(df)} registros históricos de BTC desde caché CSV.")
                            return df
                except Exception as e:
                    logger.debug(f"Error al leer caché CSV: {e}")

        logger.info("Descargando serie histórica completa de Bitcoin (2011 - Presente)...")
        all_dfs = []

        # 1. Bitstamp via CCXT (Cubre desde Agosto de 2011 hasta Septiembre de 2014)
        try:
            import ccxt
            bitstamp = ccxt.bitstamp({'enableRateLimit': True})
            since_ms = bitstamp.parse8601('2011-08-01T00:00:00Z')
            ohlcv_early = []
            
            current_since = since_ms
            end_cutoff = bitstamp.parse8601('2014-10-01T00:00:00Z')
            while current_since < end_cutoff:
                chunk = bitstamp.fetch_ohlcv('BTC/USD', '1d', since=current_since, limit=1000)
                if not chunk:
                    break
                ohlcv_early.extend(chunk)
                last_ts = chunk[-1][0]
                if last_ts <= current_since:
                    break
                current_since = last_ts + 86400000
                if len(chunk) < 500:
                    break

            if ohlcv_early:
                df_early = pd.DataFrame(ohlcv_early, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df_early['timestamp'] = pd.to_datetime(df_early['timestamp'], unit='ms', utc=True)
                df_early = df_early.drop_duplicates(subset=['timestamp'])
                all_dfs.append(df_early)
                logger.info(f"Bitstamp histórico descargado: {len(df_early)} velas diarias.")
        except Exception as e:
            logger.warning(f"No se pudo descargar de Bitstamp CCXT: {e}")

        # 2. Yahoo Finance BTC-USD (Cubre desde 2014-09-17 hasta hoy con alta precisión)
        try:
            import yfinance as yf
            ticker = yf.Ticker('BTC-USD')
            df_yf = ticker.history(start='2014-09-01', end=datetime.now(timezone.utc).strftime('%Y-%m-%d'), interval='1d')
            if not df_yf.empty:
                df_yf = df_yf.reset_index()
                date_col = 'Date' if 'Date' in df_yf.columns else 'Datetime'
                df_yf = df_yf.rename(columns={
                    date_col: 'timestamp',
                    'Open': 'open',
                    'High': 'high',
                    'Low': 'low',
                    'Close': 'close',
                    'Volume': 'volume'
                })
                if df_yf['timestamp'].dt.tz is None:
                    df_yf['timestamp'] = df_yf['timestamp'].dt.tz_localize('UTC')
                else:
                    df_yf['timestamp'] = df_yf['timestamp'].dt.tz_convert('UTC')
                
                df_yf = df_yf[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
                all_dfs.append(df_yf)
                logger.info(f"Yahoo Finance descargado: {len(df_yf)} velas diarias.")
        except Exception as e:
            logger.warning(f"No se pudo descargar de Yahoo Finance: {e}")

        # Consolidar DataFrames
        if all_dfs:
            combined = pd.concat(all_dfs, ignore_index=True)
            # Normalizar timestamp a inicio de día (00:00:00 UTC)
            combined['timestamp'] = combined['timestamp'].dt.floor('D')
            combined = combined.sort_values('timestamp').drop_duplicates(subset=['timestamp'], keep='last').reset_index(drop=True)
            
            # Guardar en parquet con fallback a CSV
            try:
                os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
                try:
                    combined.to_parquet(self.cache_file, index=False)
                    logger.info(f"Caché de datos BTC guardada en {self.cache_file}")
                except Exception:
                    combined.to_csv(csv_fallback, index=False)
                    logger.info(f"Caché de datos BTC guardada en CSV {csv_fallback}")
            except Exception as e:
                logger.warning(f"No se pudo guardar la caché: {e}")
                
            return combined
        
        # Si fallaron las descargas remotas, intentar cargar datos de la DB existente
        return self._fallback_load_from_db()

    def _fallback_load_from_db(self) -> pd.DataFrame:
        """Carga datos de la base de datos interna de trading_quant.db si existe."""
        try:
            from data_layer.storage import SessionLocal, OHLCV
            db = SessionLocal()
            records = db.query(OHLCV).filter(
                OHLCV.symbol.in_(['BTC/USDT', 'BTCUSDT', 'BTC-USD']),
                OHLCV.timeframe == '1d'
            ).order_by(OHLCV.timestamp.asc()).all()
            db.close()
            if records:
                data = [{
                    'timestamp': r.timestamp if r.timestamp.tzinfo else r.timestamp.replace(tzinfo=timezone.utc),
                    'open': r.open, 'high': r.high, 'low': r.low, 'close': r.close, 'volume': r.volume
                } for r in records]
                df = pd.DataFrame(data)
                df['timestamp'] = df['timestamp'].dt.floor('D')
                return df.drop_duplicates(subset=['timestamp']).reset_index(drop=True)
        except Exception as e:
            logger.warning(f"Error cargando fallback de DB: {e}")
        return pd.DataFrame()

    def ensure_data_loaded(self):
        """Asegura que el DataFrame histórico esté cargado en memoria."""
        if self.df_btc is None or self.df_btc.empty:
            self.df_btc = self.fetch_historical_btc_data()

    def get_halving_series(
        self,
        pre_days: int = 365,
        post_days: int = 1200
    ) -> Dict[str, pd.DataFrame]:
        """
        Genera series temporales indexadas por días relativos al Halving (H=0).
        Retorna un diccionario con cada Halving: 'H1', 'H2', 'H3', 'H4'.
        
        Columnas del DataFrame resultante para cada ciclo:
        - `rel_day`: Día relativo al Halving (ej: -365, ..., 0, 1, 2, ..., 1200)
        - `timestamp`: Fecha real (Timestamp)
        - `close`: Precio de cierre en USD
        - `halving_price`: Precio de cierre en el día del Halving (H=0)
        - `multiplier`: Múltiplo de precio respecto al día del Halving (P_t / P_H0)
        - `pct_return`: Retorno porcentual respecto al Halving ((P_t / P_H0 - 1) * 100)
        - `drawdown`: Drawdown desde el máximo alcanzado hasta esa fecha en el ciclo (%)
        """
        self.ensure_data_loaded()
        if self.df_btc.empty:
            return {}

        df = self.df_btc.copy()
        df['date_only'] = df['timestamp'].dt.strftime('%Y-%m-%d')
        
        cycle_series = {}

        for h_meta in self.halvings:
            h_id = h_meta["id"]
            h_date_str = h_meta["date"]
            h_date = pd.to_datetime(h_date_str, utc=True).floor('D')

            # Obtener el precio en el día del Halving (H=0)
            h_row = df[df['date_only'] == h_date_str]
            if h_row.empty:
                nearest_idx = (df['timestamp'] - h_date).abs().idxmin()
                h_price = float(df.loc[nearest_idx, 'close'])
            else:
                h_price = float(h_row.iloc[0]['close'])

            # Fechas del rango solicitado
            start_date = h_date - timedelta(days=pre_days)
            end_date = h_date + timedelta(days=post_days)

            # Filtrar datos del ciclo
            mask = (df['timestamp'] >= start_date) & (df['timestamp'] <= end_date)
            sub_df = df[mask].copy().sort_values('timestamp').reset_index(drop=True)

            if sub_df.empty:
                continue

            # Calcular días relativos (rel_day: H=0, H+1, H+2...)
            sub_df['rel_day'] = (sub_df['timestamp'] - h_date).dt.days
            sub_df['halving_price'] = h_price
            sub_df['multiplier'] = sub_df['close'] / h_price
            sub_df['pct_return'] = (sub_df['multiplier'] - 1.0) * 100.0
            
            # Calcular Drawdown acumulativo en el ciclo
            cummax = sub_df['close'].cummax()
            sub_df['drawdown'] = ((sub_df['close'] - cummax) / cummax) * 100.0
            
            # Metadatos del ciclo
            sub_df.attrs['id'] = h_id
            sub_df.attrs['name'] = h_meta['name']
            sub_df.attrs['color'] = h_meta['color']
            sub_df.attrs['halving_date'] = h_date_str
            sub_df.attrs['halving_price'] = h_price
            sub_df.attrs['is_completed'] = h_meta['is_completed']

            cycle_series[h_id] = sub_df

        return cycle_series

    def get_benchmark_trajectory(self, cycle_series: Dict[str, pd.DataFrame], max_days: int = 1000) -> pd.DataFrame:
        """
        Calcula la curva promedio y mediana histórica de los ciclos completados (H1, H2, H3).
        """
        completed_cycles = [df for h_id, df in cycle_series.items() if df.attrs.get('is_completed', False)]
        if not completed_cycles:
            return pd.DataFrame()

        all_rel_days = range(0, max_days + 1)
        multipliers_by_day = {d: [] for d in all_rel_days}

        for c_df in completed_cycles:
            post_df = c_df[c_df['rel_day'] >= 0]
            mapped = dict(zip(post_df['rel_day'], post_df['multiplier']))
            for d in all_rel_days:
                if d in mapped and not np.isnan(mapped[d]):
                    multipliers_by_day[d].append(mapped[d])

        bench_rows = []
        for d in all_rel_days:
            vals = multipliers_by_day[d]
            if vals:
                mean_mult = float(np.mean(vals))
                median_mult = float(np.median(vals))
                min_mult = float(np.min(vals))
                max_mult = float(np.max(vals))
                bench_rows.append({
                    'rel_day': d,
                    'multiplier_mean': mean_mult,
                    'pct_return_mean': (mean_mult - 1.0) * 100.0,
                    'multiplier_median': median_mult,
                    'pct_return_median': (median_mult - 1.0) * 100.0,
                    'multiplier_min': min_mult,
                    'multiplier_max': max_mult
                })

        return pd.DataFrame(bench_rows)

    def calculate_cycle_metrics(self) -> List[Dict[str, Any]]:
        """
        Calcula las métricas cuantitativas clave de impacto para cada uno de los 4 Halvings.
        """
        self.ensure_data_loaded()
        if self.df_btc.empty:
            return []

        df = self.df_btc.copy()
        df['date_only'] = df['timestamp'].dt.strftime('%Y-%m-%d')
        metrics_list = []

        for h_meta in self.halvings:
            h_id = h_meta["id"]
            h_name = h_meta["name"]
            h_date_str = h_meta["date"]
            h_date = pd.to_datetime(h_date_str, utc=True).floor('D')
            is_completed = h_meta["is_completed"]

            # Precio del Halving (H=0)
            h_row = df[df['date_only'] == h_date_str]
            if h_row.empty:
                nearest_idx = (df['timestamp'] - h_date).abs().idxmin()
                h_price = float(df.loc[nearest_idx, 'close'])
            else:
                h_price = float(h_row.iloc[0]['close'])

            # Ciclo Pre-Halving (500 días antes hasta Halving)
            pre_mask = (df['timestamp'] >= (h_date - timedelta(days=500))) & (df['timestamp'] <= h_date)
            pre_df = df[pre_mask]
            
            bottom_price = float(pre_df['close'].min()) if not pre_df.empty else h_price
            bottom_row = pre_df.loc[pre_df['close'].idxmin()] if not pre_df.empty else None
            bottom_date_str = bottom_row['timestamp'].strftime('%Y-%m-%d') if bottom_row is not None else "N/A"
            bottom_days_before = (h_date - bottom_row['timestamp']).days if bottom_row is not None else 0
            pre_halving_rally_pct = ((h_price / bottom_price) - 1.0) * 100.0 if bottom_price > 0 else 0.0

            # Ciclo Post-Halving (Desde Halving hasta siguiente Halving o pico o fecha actual)
            if is_completed:
                next_events = [ev for ev in HALVING_EVENTS if pd.to_datetime(ev['date'], utc=True) > h_date]
                next_h_date = pd.to_datetime(next_events[0]['date'], utc=True).floor('D') if next_events else h_date + timedelta(days=1400)
                post_mask = (df['timestamp'] >= h_date) & (df['timestamp'] <= next_h_date)
            else:
                post_mask = (df['timestamp'] >= h_date)

            post_df = df[post_mask].copy()

            if not post_df.empty:
                # Pico del Ciclo (ATH del ciclo)
                peak_idx = post_df['close'].idxmax()
                peak_row = post_df.loc[peak_idx]
                peak_price = float(peak_row['close'])
                peak_date_str = peak_row['timestamp'].strftime('%Y-%m-%d')
                days_to_peak = int((peak_row['timestamp'] - h_date).days)
                peak_multiplier = peak_price / h_price
                peak_return_pct = (peak_multiplier - 1.0) * 100.0

                # Max Drawdown post-pico en el ciclo
                post_peak_df = post_df[post_df['timestamp'] >= peak_row['timestamp']]
                if not post_peak_df.empty:
                    trough_price = float(post_peak_df['close'].min())
                    max_drawdown_post_cycle = ((trough_price - peak_price) / peak_price) * 100.0
                else:
                    max_drawdown_post_cycle = 0.0

                # Rendimientos por horizontes específicos: H+30, H+60, H+90, H+180, H+365, H+500, H+730 días
                horizon_returns = {}
                for days_h in [30, 60, 90, 180, 365, 500, 730]:
                    target_dt = h_date + timedelta(days=days_h)
                    match_row = post_df[post_df['timestamp'] >= target_dt]
                    if not match_row.empty:
                        price_at_h = float(match_row.iloc[0]['close'])
                        ret = ((price_at_h / h_price) - 1.0) * 100.0
                        mult = price_at_h / h_price
                        horizon_returns[f"H+{days_h}d"] = {"return_pct": round(ret, 2), "multiplier": round(mult, 2), "price": round(price_at_h, 2)}
                    else:
                        horizon_returns[f"H+{days_h}d"] = None

                # Días actuales transcurridos si es el ciclo actual
                current_days = int((post_df['timestamp'].max() - h_date).days)
                current_price = float(post_df.iloc[-1]['close'])
                current_mult = current_price / h_price
                current_return_pct = (current_mult - 1.0) * 100.0
            else:
                peak_price = h_price
                peak_date_str = "N/A"
                days_to_peak = 0
                peak_multiplier = 1.0
                peak_return_pct = 0.0
                max_drawdown_post_cycle = 0.0
                horizon_returns = {}
                current_days = 0
                current_price = h_price
                current_mult = 1.0
                current_return_pct = 0.0

            metrics_list.append({
                "id": h_id,
                "name": h_name,
                "halving_date": h_date_str,
                "halving_price": h_price,
                "block": h_meta["block"],
                "reward_reduction": f"{h_meta['reward_before']} -> {h_meta['reward_after']} BTC",
                "is_completed": is_completed,
                "color": h_meta["color"],
                "cycle_bottom_date": bottom_date_str,
                "cycle_bottom_price": bottom_price,
                "bottom_days_before_halving": bottom_days_before,
                "pre_halving_rally_pct": round(pre_halving_rally_pct, 2),
                "peak_date": peak_date_str,
                "peak_price": peak_price,
                "days_to_peak": days_to_peak,
                "peak_multiplier": round(peak_multiplier, 2),
                "peak_return_pct": round(peak_return_pct, 2),
                "max_drawdown_post_cycle": round(max_drawdown_post_cycle, 2),
                "horizon_returns": horizon_returns,
                "current_days": current_days,
                "current_price": current_price,
                "current_multiplier": round(current_mult, 2),
                "current_return_pct": round(current_return_pct, 2)
            })

        return metrics_list

    def calculate_correlation_matrix(self, max_day_window: int = 450) -> Dict[str, Any]:
        """
        Calcula la matriz de correlación de Pearson y Spearman entre todos los ciclos de Halving
        evaluados en el mismo horizonte temporal de días relativos post-Halving (0 a max_day_window).
        """
        series_dict = self.get_halving_series(pre_days=0, post_days=max_day_window)
        if not series_dict:
            return {"labels": [], "pearson_matrix": [], "spearman_matrix": [], "similarity_scores": {}}

        aligned_data = {}
        for h_id, df in series_dict.items():
            post_df = df[(df['rel_day'] >= 0) & (df['rel_day'] <= max_day_window)].set_index('rel_day')
            aligned_data[h_id] = post_df['multiplier']

        df_aligned = pd.DataFrame(aligned_data)
        
        pearson_corr = df_aligned.corr(method='pearson').round(3)
        spearman_corr = df_aligned.corr(method='spearman').round(3)

        labels = list(df_aligned.columns)
        
        # Similitud del ciclo actual (H4) con H1, H2, H3
        similarity_scores = {}
        if "H4" in df_aligned.columns:
            h4_series = df_aligned["H4"].dropna()
            for col in ["H1", "H2", "H3"]:
                if col in df_aligned.columns:
                    valid_idx = h4_series.index.intersection(df_aligned[col].dropna().index)
                    if len(valid_idx) > 10:
                        s1 = h4_series.loc[valid_idx]
                        s2 = df_aligned.loc[valid_idx, col]
                        p_corr = float(np.corrcoef(s1, s2)[0, 1])
                        similarity_scores[col] = {
                            "correlation": round(p_corr, 3),
                            "overlap_days": len(valid_idx),
                            "similarity_pct": round(max(0.0, p_corr) * 100.0, 1)
                        }

        return {
            "labels": labels,
            "pearson_matrix": pearson_corr.fillna(0).values.tolist(),
            "spearman_matrix": spearman_corr.fillna(0).values.tolist(),
            "similarity_scores": similarity_scores
        }

    def calculate_diminishing_returns_model(self) -> Dict[str, Any]:
        """
        Calcula el modelo de rendimientos decrecientes (Diminishing Returns)
        ajustando los múltiplos máximos alcanzados en cada ciclo a una curva de decaimiento potencial/exponencial,
        y proyecta escenarios para el Halving 4.
        """
        metrics = self.calculate_cycle_metrics()
        completed = [m for m in metrics if m["is_completed"]]
        
        if len(completed) < 2:
            return {}

        cycles_num = [1, 2, 3]
        multipliers = [m["peak_multiplier"] for m in completed]  # H1: ~93x, H2: ~30x, H3: ~7.5x
        
        # Ajuste logarítmico: log(Multiplier) = a * Cycle + b
        log_mults = np.log(multipliers)
        coeffs = np.polyfit(cycles_num, log_mults, 1)
        
        # Factores de decaimiento históricos
        decay_h1_to_h2 = multipliers[1] / multipliers[0]
        decay_h2_to_h3 = multipliers[2] / multipliers[1]
        avg_decay = float(np.mean([decay_h1_to_h2, decay_h2_to_h3]))

        # Proyecciones para Halving 4
        model_h4_multiplier = float(np.exp(coeffs[0] * 4 + coeffs[1]))
        
        h4_meta = next((m for m in metrics if m["id"] == "H4"), None)
        h4_base_price = h4_meta["halving_price"] if h4_meta else 63800.0

        scenarios = {
            "conservative": {
                "name": "Conservador (Decaimiento Alto / ~2.0x - 2.5x)",
                "multiplier": round(max(2.0, model_h4_multiplier * 0.75), 2),
                "target_price": round(h4_base_price * max(2.0, model_h4_multiplier * 0.75), 2),
                "estimated_roi_pct": round((max(2.0, model_h4_multiplier * 0.75) - 1.0) * 100.0, 1),
                "desc": "Asume una aceleración en la maduración institucional y menor multiplicador de oferta."
            },
            "base_model": {
                "name": "Modelo Base (Ajuste Exponencial / ~3.0x - 3.8x)",
                "multiplier": round(model_h4_multiplier, 2),
                "target_price": round(h4_base_price * model_h4_multiplier, 2),
                "estimated_roi_pct": round((model_h4_multiplier - 1.0) * 100.0, 1),
                "desc": "Proyección matemática ajustada por la curva de rendimientos decrecientes de los 3 ciclos previos."
            },
            "bullish": {
                "name": "Alcista (Mismo decaimiento H2->H3 / ~4.5x - 5.5x)",
                "multiplier": round(multipliers[-1] * 0.40, 2),
                "target_price": round(h4_base_price * (multipliers[-1] * 0.40), 2),
                "estimated_roi_pct": round(((multipliers[-1] * 0.40) - 1.0) * 100.0, 1),
                "desc": "Escenario de fuerte expansión de liquidez global y adopción récord de ETFs e inversión soberana."
            }
        }

        avg_days_to_peak = int(np.mean([m["days_to_peak"] for m in completed]))
        min_days_to_peak = int(np.min([m["days_to_peak"] for m in completed]))
        max_days_to_peak = int(np.max([m["days_to_peak"] for m in completed]))

        h4_date = pd.to_datetime("2024-04-19", utc=True)
        estimated_peak_window = {
            "avg_date": (h4_date + timedelta(days=avg_days_to_peak)).strftime('%B %Y'),
            "range_start": (h4_date + timedelta(days=min_days_to_peak)).strftime('%B %Y'),
            "range_end": (h4_date + timedelta(days=max_days_to_peak)).strftime('%B %Y'),
            "avg_days": avg_days_to_peak
        }

        return {
            "historical_multipliers": {
                "H1 (2012)": f"{multipliers[0]:.1f}x",
                "H2 (2016)": f"{multipliers[1]:.1f}x",
                "H3 (2020)": f"{multipliers[2]:.1f}x"
            },
            "average_decay_factor": round(avg_decay, 3),
            "scenarios": scenarios,
            "estimated_peak_window": estimated_peak_window
        }

    def calculate_periodic_growth_analysis(
        self,
        timeframe: str = "month",
        step_size: int = 1,
        max_days: int = 1200
    ) -> Dict[str, Any]:
        """
        Calcula el crecimiento y decrecimiento periódico y acumulado para todos los ciclos de Halving
        según la granularidad temporal seleccionada: día, semana, mes, trimestre, semestre o año.

        :param timeframe: 'day', 'week', 'month', 'quarter', 'semester', 'year'
        :param step_size: Cantidad o multiplicador de períodos (ej: 1, 2, 3, etc.)
        :param max_days: Ventana máxima de días post-Halving a evaluar (default: 1200)
        :return: Diccionario estructurado con períodos, series por ciclo, benchmarks y estadísticas resumidas.
        """
        unit_days_map = {
            "day": (1, "Día", "D"),
            "week": (7, "Semana", "S"),
            "month": (30, "Mes", "M"),
            "quarter": (90, "Trimestre", "T"),
            "semester": (180, "Semestre", "Sem"),
            "year": (365, "Año", "A")
        }

        tf_key = timeframe.lower() if timeframe.lower() in unit_days_map else "month"
        base_days, unit_name, unit_abbr = unit_days_map[tf_key]
        step = max(1, int(step_size))
        interval_days = base_days * step

        # Límite de períodos
        total_periods = int(np.ceil(max_days / interval_days))
        # Limitar a un máximo razonable para evitar sobrecarga en días individuales
        total_periods = min(total_periods, 120)

        series_dict = self.get_halving_series(pre_days=0, post_days=max_days + interval_days)
        if not series_dict:
            return {"timeframe": tf_key, "step_size": step, "interval_days": interval_days, "periods": [], "cycles": {}, "benchmark": []}

        # Generar metadatos de los períodos
        period_meta = []
        for p_idx in range(1, total_periods + 1):
            start_d = (p_idx - 1) * interval_days
            end_d = p_idx * interval_days
            if step == 1:
                label_short = f"{unit_abbr}{p_idx}"
                label_full = f"{unit_name} {p_idx} (H+{start_d} a H+{end_d}d)"
            else:
                label_short = f"{step}{unit_abbr}_{p_idx}"
                label_full = f"{step} {unit_name}s #{p_idx} (H+{start_d} a H+{end_d}d)"

            period_meta.append({
                "period_index": p_idx,
                "label_short": label_short,
                "label_full": label_full,
                "start_day": start_d,
                "end_day": end_d
            })

        cycles_data: Dict[str, List[Dict[str, Any]]] = {}

        for h_meta in self.halvings:
            h_id = h_meta["id"]
            if h_id not in series_dict:
                continue

            df = series_dict[h_id]
            post_df = df[df["rel_day"] >= 0].sort_values("rel_day").reset_index(drop=True)
            if post_df.empty:
                continue

            h_price = float(df.attrs.get("halving_price", post_df.iloc[0]["close"]))
            max_available_day = int(post_df["rel_day"].max())

            cycle_periods = []
            for p_info in period_meta:
                s_day = p_info["start_day"]
                e_day = p_info["end_day"]

                # Si el ciclo no ha llegado al inicio de este período
                if s_day > max_available_day:
                    cycle_periods.append({
                        "period_index": p_info["period_index"],
                        "has_data": False,
                        "is_in_progress": False,
                        "periodic_return_pct": None,
                        "cumulative_return_pct": None,
                        "cumulative_multiplier": None,
                        "start_price": None,
                        "end_price": None,
                        "high_price": None,
                        "low_price": None,
                        "intra_gain_pct": None,
                        "intra_drawdown_pct": None
                    })
                    continue

                # Filtrar datos dentro de la ventana del período
                sub = post_df[(post_df["rel_day"] >= s_day) & (post_df["rel_day"] <= e_day)]
                if sub.empty:
                    # Usar precio más cercano
                    closest_idx = (post_df["rel_day"] - s_day).abs().idxmin()
                    p_start = float(post_df.loc[closest_idx, "close"])
                    p_end = p_start
                    p_high = p_start
                    p_low = p_start
                    is_in_progress = (s_day <= max_available_day < e_day)
                else:
                    # Precio de inicio: si s_day == 0, halving_price, o primera vela del período
                    if s_day == 0:
                        p_start = h_price
                    else:
                        start_candidates = post_df[post_df["rel_day"] <= s_day]
                        p_start = float(start_candidates.iloc[-1]["close"]) if not start_candidates.empty else float(sub.iloc[0]["close"])

                    p_end = float(sub.iloc[-1]["close"])
                    p_high = float(sub["close"].max())
                    p_low = float(sub["close"].min())
                    is_in_progress = (not h_meta.get("is_completed", False)) and (max_available_day < e_day)

                periodic_return = round(((p_end - p_start) / p_start) * 100.0, 2) if p_start > 0 else 0.0
                cum_return = round(((p_end - h_price) / h_price) * 100.0, 2) if h_price > 0 else 0.0
                cum_mult = round(p_end / h_price, 3) if h_price > 0 else 1.0
                intra_gain = round(((p_high - p_start) / p_start) * 100.0, 2) if p_start > 0 else 0.0
                intra_dd = round(((p_low - p_start) / p_start) * 100.0, 2) if p_start > 0 else 0.0

                cycle_periods.append({
                    "period_index": p_info["period_index"],
                    "has_data": True,
                    "is_in_progress": is_in_progress,
                    "start_price": round(p_start, 2),
                    "end_price": round(p_end, 2),
                    "high_price": round(p_high, 2),
                    "low_price": round(p_low, 2),
                    "periodic_return_pct": periodic_return,
                    "cumulative_return_pct": cum_return,
                    "cumulative_multiplier": cum_mult,
                    "intra_gain_pct": intra_gain,
                    "intra_drawdown_pct": intra_dd
                })

            cycles_data[h_id] = cycle_periods

        # Calcular Benchmarks periódicos (Promedio de ciclos completados H1, H2, H3)
        benchmark_periods = []
        completed_ids = [h["id"] for h in self.halvings if h["is_completed"] and h["id"] in cycles_data]

        for idx, p_info in enumerate(period_meta):
            vals_periodic = []
            vals_cumulative = []
            vals_mult = []

            for cid in completed_ids:
                c_period = cycles_data[cid][idx]
                if c_period["has_data"] and c_period["periodic_return_pct"] is not None:
                    vals_periodic.append(c_period["periodic_return_pct"])
                    vals_cumulative.append(c_period["cumulative_return_pct"])
                    vals_mult.append(c_period["cumulative_multiplier"])

            if vals_periodic:
                mean_per = float(np.mean(vals_periodic))
                med_per = float(np.median(vals_periodic))
                mean_cum = float(np.mean(vals_cumulative))
                mean_mult = float(np.mean(vals_mult))
                pos_pct = round((sum(1 for v in vals_periodic if v > 0) / len(vals_periodic)) * 100.0, 1)

                benchmark_periods.append({
                    "period_index": p_info["period_index"],
                    "has_data": True,
                    "mean_periodic_pct": round(mean_per, 2),
                    "median_periodic_pct": round(med_per, 2),
                    "mean_cumulative_pct": round(mean_cum, 2),
                    "mean_cumulative_multiplier": round(mean_mult, 3),
                    "positive_win_rate_pct": pos_pct,
                    "count": len(vals_periodic)
                })
            else:
                benchmark_periods.append({
                    "period_index": p_info["period_index"],
                    "has_data": False,
                    "mean_periodic_pct": None,
                    "median_periodic_pct": None,
                    "mean_cumulative_pct": None,
                    "mean_cumulative_multiplier": None,
                    "positive_win_rate_pct": None,
                    "count": 0
                })

        # Resumen cuantitativo general
        all_completed_periodic_returns = [
            p["periodic_return_pct"]
            for cid in completed_ids
            for p in cycles_data[cid]
            if p["has_data"] and p["periodic_return_pct"] is not None
        ]

        summary = {
            "total_evaluated_periods": len(period_meta),
            "timeframe_label": unit_name,
            "step_label": f"{step} {unit_name}{'s' if step > 1 else ''}",
            "interval_days": interval_days,
            "global_positive_ratio": round((sum(1 for v in all_completed_periodic_returns if v > 0) / max(1, len(all_completed_periodic_returns))) * 100.0, 1) if all_completed_periodic_returns else 0.0,
            "avg_period_return": round(float(np.mean(all_completed_periodic_returns)), 2) if all_completed_periodic_returns else 0.0,
            "max_period_return": round(float(np.max(all_completed_periodic_returns)), 2) if all_completed_periodic_returns else 0.0,
            "min_period_return": round(float(np.min(all_completed_periodic_returns)), 2) if all_completed_periodic_returns else 0.0
        }

        return {
            "timeframe": tf_key,
            "step_size": step,
            "interval_days": interval_days,
            "unit_name": unit_name,
            "unit_abbr": unit_abbr,
            "periods": period_meta,
            "cycles": cycles_data,
            "benchmark": benchmark_periods,
            "summary": summary
        }

