import pandas as pd
import numpy as np

class FlowIndicators:
    """
    Clase con funciones estáticas para calcular los indicadores macro de liquidez
    y procesar el 'resampling' (sincronización temporal) de los datos.
    """
    
    @staticmethod
    def calculate_net_supply(df_mints: pd.DataFrame, df_burns: pd.DataFrame, timeframe: str = '1D') -> pd.DataFrame:
        """
        NetSupply = Minted - Burned
        Sincroniza y suma los flujos en la resolución de tiempo solicitada.
        """
        mints_resampled = FlowIndicators._resample_sum(df_mints, timeframe, col_name='mints')
        burns_resampled = FlowIndicators._resample_sum(df_burns, timeframe, col_name='burns')
        
        df_net = pd.concat([mints_resampled, burns_resampled], axis=1).fillna(0)
        df_net['net_supply'] = df_net['mints'] - df_net['burns']
        return df_net[['net_supply']]

    @staticmethod
    def calculate_netflow(df_inflows: pd.DataFrame, df_outflows: pd.DataFrame, timeframe: str = '1D') -> pd.DataFrame:
        """
        Netflow = Inflow - Outflow
        Aplica tanto a Stablecoins (poder adquisitivo latente) como a BTC (presión de venta).
        """
        inflows_resampled = FlowIndicators._resample_sum(df_inflows, timeframe, col_name='inflows')
        outflows_resampled = FlowIndicators._resample_sum(df_outflows, timeframe, col_name='outflows')
        
        df_net = pd.concat([inflows_resampled, outflows_resampled], axis=1).fillna(0)
        df_net['netflow'] = df_net['inflows'] - df_net['outflows']
        return df_net[['netflow']]

    @staticmethod
    def _resample_sum(df: pd.DataFrame, timeframe: str, col_name: str = 'value') -> pd.DataFrame:
        """
        Agrupa eventos asíncronos en velas regulares mediante suma.
        """
        if df.empty or 'value' not in df.columns:
            return pd.DataFrame(columns=[col_name])
            
        # Asegurar índice datetime
        if not isinstance(df.index, pd.DatetimeIndex):
            df = df.copy()
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
            
        resampled = df[['value']].resample(timeframe).sum()
        resampled.rename(columns={'value': col_name}, inplace=True)
        return resampled

    @staticmethod
    def compute_z_score_rolling(series: pd.Series, window: int = 30) -> pd.Series:
        """
        Normalización estadística usando Z-Score con ventana móvil (mu_30, sigma_30).
        """
        rolling_mean = series.rolling(window=window).mean()
        rolling_std = series.rolling(window=window).std()
        
        z_score = (series - rolling_mean) / rolling_std
        return z_score.fillna(0)
