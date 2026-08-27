import pandas as pd
import numpy as np
import logging
from .base_strategy import BaseStrategy
from .indicators_flow import FlowIndicators

logger = logging.getLogger(__name__)

class OnChainFlowStrategy(BaseStrategy):
    """
    Estrategia de matriz de condiciones basada en flujos netos (Netflow)
    de Bitcoin y Stablecoins hacia exchanges, y emisión (NetSupply).
    """
    def __init__(self, config_or_path, custom_parameters=None):
        super().__init__(config_or_path, custom_parameters)
        
        # Parámetros de la matriz estadística
        self.z_window = int(self.parameters.get("z_window", 30))
        self.z_threshold = float(self.parameters.get("z_threshold", 1.5))

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        El df que entra debe contener ya las métricas on-chain unificadas en el mismo índice que OHLCV.
        Asumimos que el DataEngine ya mergeó 'net_supply', 'netflow_stables', 'netflow_btc'.
        """
        df = df.copy()
        
        # Validación de dependencias
        required_cols = ['net_supply', 'netflow_stables', 'netflow_btc']
        for col in required_cols:
            if col not in df.columns:
                logger.warning(f"Columna on-chain '{col}' no encontrada en el DataFrame. Rellenando con 0.")
                df[col] = 0.0

        # 1. Normalización Estadística (Z-Scores)
        df['z_net_supply'] = FlowIndicators.compute_z_score_rolling(df['net_supply'], window=self.z_window)
        df['z_netflow_stables'] = FlowIndicators.compute_z_score_rolling(df['netflow_stables'], window=self.z_window)
        df['z_netflow_btc'] = FlowIndicators.compute_z_score_rolling(df['netflow_btc'], window=self.z_window)
        
        # 2. Matriz de Condiciones (Regímenes)
        # Bullish Exhaustivo:
        # - Netflow_BTC < 0 (Retiros masivos de BTC, Z-Score < -Threshold)
        # - Netflow_Stables > 0 (Depósitos de fiat, Z-Score > Threshold)
        # - NetSupply > 0 (Impresión fresca, Z-Score > Threshold)
        bullish_cond = (
            (df['z_netflow_btc'] < -self.z_threshold) & 
            (df['z_netflow_stables'] > self.z_threshold) & 
            (df['z_net_supply'] > self.z_threshold)
        )
        
        # Bearish Exhaustivo:
        # - Netflow_BTC > 0 (Depósitos masivos de BTC para vender, Z-Score > Threshold)
        # - NetSupply < 0 (Quema de stablecoins, Z-Score < -Threshold)
        bearish_cond = (
            (df['z_netflow_btc'] > self.z_threshold) &
            (df['z_net_supply'] < -self.z_threshold)
        )
        
        # 3. Mapeo de Señales
        df['entry_long'] = bullish_cond
        df['exit_long'] = bearish_cond | (df['z_netflow_btc'] > self.z_threshold) # Salida temprana si entra BTC
        
        df['entry_short'] = bearish_cond
        df['exit_short'] = bullish_cond
        
        logger.info(f"Señales OnChainFlow generadas. Longs: {df['entry_long'].sum()}, Shorts: {df['entry_short'].sum()}")
        return df
