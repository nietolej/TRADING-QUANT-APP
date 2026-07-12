import pandas as pd
import numpy as np
import ta

class RiskManager:
    """
    Calcula niveles de Stop Loss y Take Profit basados en la configuración de la estrategia.
    """
    
    def __init__(self, risk_config: dict):
        self.config = risk_config
        self.sl_config = self.config.get("stop_loss", {})
        self.tp_config = self.config.get("take_profit", {})
        self.sizing_config = self.config.get("position_sizing", {})
        
    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        return ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=period)

    def compute_sl_tp(self, df: pd.DataFrame, entry_index: int, side: str = "long") -> tuple:
        """
        Retorna (stop_loss_price, take_profit_price) para una entrada dada.
        """
        entry_price = df['close'].iloc[entry_index]
        
        # Stop Loss
        sl_type = self.sl_config.get("type", "fixed").replace(" ", "_")
        sl_val = self.sl_config.get("value", 2.0) / 100.0 if sl_type != "fixed_price" else self.sl_config.get("value", 0.0)
        sl_price = None
        
        if sl_type == "percentage" or sl_type == "fixed" or sl_type == "trailing_percent":
            if side == "long":
                sl_price = entry_price * (1 - sl_val)
            else:
                sl_price = entry_price * (1 + sl_val)
        elif sl_type == "dynamic":
            method = self.sl_config.get("dynamic_method", "atr")
            if method == "atr":
                atr_period = self.sl_config.get("atr_period", 14)
                atr_mult = self.sl_config.get("atr_multiplier", 2.0)
                if 'ATR' not in df.columns:
                    df['ATR'] = self.calculate_atr(df, atr_period)
                atr_val = df['ATR'].iloc[entry_index]
                if pd.isna(atr_val):
                    atr_val = entry_price * 0.02
                if side == "long":
                    sl_price = entry_price - (atr_val * atr_mult)
                else:
                    sl_price = entry_price + (atr_val * atr_mult)
                    
        # Take Profit
        tp_type = self.tp_config.get("type", "fixed").replace(" ", "_")
        tp_val = self.tp_config.get("value", 4.0) / 100.0 if tp_type != "fixed_price" else self.tp_config.get("value", 0.0)
        tp_price = None
        
        if tp_type == "percentage" or tp_type == "fixed":
            if side == "long":
                tp_price = entry_price * (1 + tp_val)
            else:
                tp_price = entry_price * (1 - tp_val)
        elif tp_type == "fixed_price":
            tp_price = tp_val
            if tp_price <= 0:
                tp_price = None
        elif tp_type == "dynamic":
            method = self.tp_config.get("dynamic_method", "risk_reward_ratio")
            if method == "risk_reward_ratio" and sl_price is not None:
                rr_ratio = self.tp_config.get("risk_reward_ratio", 2.0)
                risk_amount = abs(entry_price - sl_price)
                if side == "long":
                    tp_price = entry_price + (risk_amount * rr_ratio)
                else:
                    tp_price = entry_price - (risk_amount * rr_ratio)
                    
        return sl_price, tp_price

    def compute_position_size(self, capital: float, entry_price: float, sl_price: float = None) -> float:
        """
        Calcula el tamaño de la posición basado en el capital y el riesgo.
        """
        method = self.sizing_config.get("method", "fixed_fractional")
        
        if method == "fixed_fractional" and sl_price is not None:
            risk_pct = self.sizing_config.get("risk_per_trade_pct", 1.0) / 100.0
            risk_amount = capital * risk_pct
            price_risk = abs(entry_price - sl_price)
            if price_risk > 0:
                quantity = risk_amount / price_risk
                return quantity
                
        # Por defecto invertir todo el capital disponible
        # Asume que 'capital' es la fracción que se desea invertir si es fijo
        return capital / entry_price
