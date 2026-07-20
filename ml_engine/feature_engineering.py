import pandas as pd
import numpy as np
import ta

class FeatureEngineer:
    def __init__(self, target_lookahead=1, target_threshold=0.0):
        self.target_lookahead = target_lookahead
        self.target_threshold = target_threshold

    def create_features(self, df):
        """
        Calcula indicadores técnicos como features para el modelo ML.
        """
        required_cols = ['open', 'high', 'low', 'close', 'volume']
        if not all(col in df.columns for col in required_cols):
            raise ValueError(f"El DataFrame debe contener las columnas: {required_cols}")

        df = df.copy()

        # Tendencia
        df['sma_20'] = ta.trend.sma_indicator(df['close'], window=20)
        df['sma_50'] = ta.trend.sma_indicator(df['close'], window=50)
        df['macd'] = ta.trend.macd(df['close'])
        df['macd_signal'] = ta.trend.macd_signal(df['close'])
        
        # Momentum
        df['rsi_14'] = ta.momentum.rsi(df['close'], window=14)
        df['stoch'] = ta.momentum.stoch(df['high'], df['low'], df['close'])
        
        # Volatilidad
        df['bb_high'] = ta.volatility.bollinger_hband(df['close'])
        df['bb_low'] = ta.volatility.bollinger_lband(df['close'])
        df['atr_14'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'])

        # Features derivadas
        df['dist_sma20'] = (df['close'] - df['sma_20']) / df['sma_20']
        
        df.dropna(inplace=True)
        return df

    def create_target(self, df):
        """
        Crea la variable objetivo (target). 
        1 = Sube más del threshold
        0 = Baja o sube menos del threshold
        """
        df = df.copy()
        # Retorno futuro a N velas
        df['future_return'] = df['close'].shift(-self.target_lookahead) / df['close'] - 1
        df['target'] = np.where(df['future_return'] > self.target_threshold, 1, 0)
        
        # Eliminar NaN final
        df.dropna(inplace=True)
        return df

    def prepare_data(self, df):
        df_features = self.create_features(df)
        df_target = self.create_target(df_features)
        
        exclude_cols = ['open', 'high', 'low', 'close', 'volume', 'timestamp', 'future_return', 'target', 'symbol', 'timeframe']
        feature_cols = [c for c in df_target.columns if c not in exclude_cols]
        
        X = df_target[feature_cols]
        y = df_target['target']
        
        return X, y
