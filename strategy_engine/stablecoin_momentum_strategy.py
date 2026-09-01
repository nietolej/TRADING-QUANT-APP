import os
import logging
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from .base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

HALVING_DATES = [
    pd.to_datetime("2012-11-28", utc=True),
    pd.to_datetime("2016-07-09", utc=True),
    pd.to_datetime("2020-05-11", utc=True),
    pd.to_datetime("2024-04-19", utc=True)
]

class StablecoinEmissionEMAStrategy(BaseStrategy):
    """
    Estrategia Cuantitativa: Stablecoin-Halving Momentum (SHM).
    Combina:
    1. Impulso de Liquidez On-Chain: Z-Score de la emisión/inyección neta de Stablecoins.
    2. Seguimiento de Tendencia: Filtros y cruces de Medias Móviles Exponenciales (EMA).
    3. Filtro de Régimen de Halving: Restringe las operaciones a las fases de acumulación
       y aceleración post-Halving (ej: H+150d a H+550d).
    """
    def __init__(self, config_or_path, custom_parameters: Optional[Dict[str, Any]] = None):
        super().__init__(config_or_path, custom_parameters)

        # 1. Parámetros de Tendencia (EMA)
        self.ema_fast = int(self.parameters.get("ema_fast", 20))
        self.ema_slow = int(self.parameters.get("ema_slow", 50))
        self.ema_trend = int(self.parameters.get("ema_trend", 100))
        self.trend_mode = str(self.parameters.get("trend_mode", "price_above")).lower()

        # 2. Parámetros de Liquidez de Stablecoins (Z-Score)
        self.flow_window = int(self.parameters.get("flow_window", 14))
        self.z_window = int(self.parameters.get("z_window", 60))
        self.z_entry_threshold = float(self.parameters.get("z_entry_threshold", 1.0))
        self.z_exit_threshold = float(self.parameters.get("z_exit_threshold", -0.5))

        # 3. Parámetros de Filtro de Régimen de Halving
        self.halving_filter_enabled = bool(self.parameters.get("halving_filter_enabled", True))
        self.min_post_halving_days = int(self.parameters.get("min_post_halving_days", 150))
        self.max_post_halving_days = int(self.parameters.get("max_post_halving_days", 550))

        # 4. Datos cacheados de stablecoins
        self._stables_cache: Optional[pd.DataFrame] = None

    def _get_stables_data(self) -> Optional[pd.DataFrame]:
        """Carga el dataset histórico de stablecoins desde caché local."""
        if self._stables_cache is not None:
            return self._stables_cache

        cache_path = os.path.join(os.getcwd(), "data", "stablecoins_daily_historical.csv")
        if os.path.exists(cache_path):
            try:
                df_s = pd.read_csv(cache_path)
                df_s['timestamp'] = pd.to_datetime(df_s['timestamp'], utc=True)
                if 'market_cap' in df_s.columns:
                    df_s = df_s.rename(columns={'market_cap': 'stables_mcap'})
                self._stables_cache = df_s[['timestamp', 'stables_mcap']].sort_values('timestamp').reset_index(drop=True)
                return self._stables_cache
            except Exception as e:
                logger.error(f"Error cargando caché de stablecoins: {e}")
        return None

    def _calculate_days_post_halving(self, timestamps: pd.Series) -> pd.Series:
        """Calcula los días transcurridos desde el Halving más reciente previo a cada timestamp."""
        ts = pd.to_datetime(timestamps, utc=True)
        rel_days = pd.Series(index=ts.index, dtype=float)

        for idx, t in ts.items():
            past_halvings = [h for h in HALVING_DATES if h <= t]
            if past_halvings:
                last_h = max(past_halvings)
                rel_days.loc[idx] = (t - last_h).days
            else:
                rel_days.loc[idx] = -999.0

        return rel_days

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calcula indicadores técnicos, on-chain de liquidez y filtros de halving,
        generando las columnas 'entry_long', 'exit_long', 'entry_short', 'exit_short'.
        """
        df = df.copy()

        # Asegurar timestamp datetime
        if 'timestamp' not in df.columns:
            if isinstance(df.index, pd.DatetimeIndex):
                df['timestamp'] = df.index
            else:
                logger.warning("No se encontró columna 'timestamp' en el DataFrame.")
                df['entry_long'] = False
                df['exit_long'] = False
                df['entry_short'] = False
                df['exit_short'] = False
                return df

        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)

        # 1. Integrar datos de Stablecoins si no están presentes
        if 'stables_mcap' not in df.columns and 'stables_flow' not in df.columns:
            stables_df = self._get_stables_data()
            if stables_df is not None and not stables_df.empty:
                # Merge con tolerancia de fecha
                df['date_only'] = df['timestamp'].dt.floor('D')
                stables_df['date_only'] = stables_df['timestamp'].dt.floor('D')
                merged_s = pd.merge(
                    df[['date_only']],
                    stables_df[['date_only', 'stables_mcap']],
                    on='date_only',
                    how='left'
                )
                df['stables_mcap'] = merged_s['stables_mcap'].ffill().bfill()
                df.drop(columns=['date_only'], inplace=True, errors='ignore')
            else:
                logger.warning("Datos de Stablecoins no disponibles; usando serie aproximada.")
                df['stables_mcap'] = 1e9

        # 2. Indicadores de Flujo de Stablecoins (Z-Score)
        if 'stables_mcap' in df.columns:
            df['stables_flow'] = df['stables_mcap'].diff(self.flow_window)
        elif 'stables_flow' not in df.columns:
            df['stables_flow'] = 0.0

        df['stables_flow_mean'] = df['stables_flow'].rolling(self.z_window).mean()
        df['stables_flow_std'] = df['stables_flow'].rolling(self.z_window).std()
        df['z_stables_flow'] = (df['stables_flow'] - df['stables_flow_mean']) / (df['stables_flow_std'] + 1e-9)
        df['z_stables_flow'] = df['z_stables_flow'].fillna(0.0)

        # 3. Indicadores de Tendencia (EMAs)
        close_series = df['close']
        df['ema_fast'] = close_series.ewm(span=self.ema_fast, adjust=False).mean()
        df['ema_slow'] = close_series.ewm(span=self.ema_slow, adjust=False).mean()
        df['ema_trend'] = close_series.ewm(span=self.ema_trend, adjust=False).mean()

        # 4. Días Relativos Post-Halving
        df['rel_halving_days'] = self._calculate_days_post_halving(df['timestamp'])

        # 5. Condiciones de Entrada y Salida
        # A. Condición de Tendencia
        if self.trend_mode == "crossover":
            bullish_trend = (df['ema_fast'] > df['ema_slow'])
        elif self.trend_mode == "price_above":
            bullish_trend = (close_series > df['ema_slow'])
        else: # "both"
            bullish_trend = (close_series > df['ema_slow']) & (df['ema_fast'] >= df['ema_slow'])

        # B. Condición de Impulso de Liquidez
        liquidity_expansion = (df['z_stables_flow'] >= self.z_entry_threshold)
        liquidity_contraction = (df['z_stables_flow'] <= self.z_exit_threshold)

        # C. Condición de Ventana de Halving
        if self.halving_filter_enabled:
            halving_regime = (
                (df['rel_halving_days'] >= self.min_post_halving_days) &
                (df['rel_halving_days'] <= self.max_post_halving_days)
            )
            halving_exhaustion = (df['rel_halving_days'] > self.max_post_halving_days + 30)
        else:
            halving_regime = pd.Series(True, index=df.index)
            halving_exhaustion = pd.Series(False, index=df.index)

        # D. Mapeo de Señales
        # Entrada LONG: Tendencia alcista + Impulso de Liquidez + Ventana Halving
        entry_long = bullish_trend & liquidity_expansion & halving_regime

        # Salida LONG: Pérdida de EMA rápida O Contracción severa de liquidez O Fin de ventana cíclica
        exit_long = (close_series < df['ema_fast']) | liquidity_contraction | halving_exhaustion

        # Entrada SHORT (si aplica): Pérdida de EMA lenta + Contracción de liquidez + Fase post-clímax
        entry_short = (~bullish_trend) & liquidity_contraction & (df['rel_halving_days'] > self.max_post_halving_days)
        exit_short = (close_series > df['ema_fast']) | liquidity_expansion

        direction = self.config.get("trade_direction", "Long").lower()
        if direction == "short":
            df['entry_short'] = entry_short
            df['exit_short'] = exit_short
            df['entry_long'] = False
            df['exit_long'] = False
        elif direction == "both":
            df['entry_long'] = entry_long
            df['exit_long'] = exit_long
            df['entry_short'] = entry_short
            df['exit_short'] = exit_short
        else: # "long"
            df['entry_long'] = entry_long
            df['exit_long'] = exit_long
            df['entry_short'] = False
            df['exit_short'] = False

        logger.info(f"Señales SHM generadas. Longs: {df['entry_long'].sum()}, Exits: {df['exit_long'].sum()}")
        return df
