import pandas as pd
import numpy as np
import ta


class RiskManager:
    """
    Motor de Gestión de Riesgo (Stop Loss y Take Profit).
    Soporta múltiples lógicas cuantitativas:
    
    STOP LOSS:
      - 'fixed' / 'percentage': Stop Loss porcentual fijo desde la entrada (ej. 2%).
      - 'trailing_percent': Trailing Stop que sigue el pico más alto (Long) o bajo (Short).
      - 'break_even': Mueve el Stop al precio de entrada al superar cierto % de ganancia.
      - 'atr': Stop dinámico basado en múltiplos de volatilidad (K * ATR).
      - 'chandelier': Chandelier Exit basado en el máximo de N períodos menos K * ATR.
      - 'swing': Stop Loss en el soporte/resistencia reciente (Swing Low en Longs / Swing High en Shorts).

    TAKE PROFIT:
      - 'fixed' / 'percentage': Take Profit porcentual fijo (ej. 4%).
      - 'risk_reward': Ratio Riesgo/Beneficio dinámico respecto al Stop Loss (ej. 2.0R).
      - 'atr': Objetivo de beneficio por múltiplo de volatilidad (M * ATR).
      - 'partial' / 'multi_tp': Salida escalonada (TP1 con toma parcial, TP2 objetivo final).
    """

    def __init__(self, risk_config: dict):
        self.config = risk_config or {}
        self.sl_config = self.config.get("stop_loss", {})
        self.tp_config = self.config.get("take_profit", {})
        self.sizing_config = self.config.get("position_sizing", {})

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calcula el Average True Range (ATR)."""
        period = max(1, int(period))
        return ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=period)

    def compute_sl_tp(self, df: pd.DataFrame, entry_index: int, side: str = "long") -> tuple:
        """
        Retorna (stop_loss_price, take_profit_price) para una entrada dada.
        """
        entry_price = float(df['close'].iloc[entry_index])
        
        # ═════════════════════════════════════════════════════════════
        # 1. CÁLCULO DE STOP LOSS (SL)
        # ═════════════════════════════════════════════════════════════
        raw_sl_type = str(self.sl_config.get("type", "fixed")).lower().strip().replace(" ", "_")
        sl_val = float(self.sl_config.get("value", 2.0))
        sl_price = None

        if raw_sl_type in ["fixed", "percentage", "trailing_percent", "trailing", "break_even", "breakeven"]:
            pct = (sl_val / 100.0) if sl_val != 0 else 0.02
            if side == "long":
                sl_price = entry_price * (1.0 - pct)
            else:
                sl_price = entry_price * (1.0 + pct)

        elif raw_sl_type in ["atr", "volatility", "dynamic"]:
            atr_period = int(self.sl_config.get("atr_period", 14))
            atr_mult = float(self.sl_config.get("atr_multiplier", self.sl_config.get("value", 2.0)))
            if 'ATR' not in df.columns or df['ATR'].isnull().all():
                df['ATR'] = self.calculate_atr(df, atr_period)
            atr_val = df['ATR'].iloc[entry_index]
            if pd.isna(atr_val) or atr_val <= 0:
                atr_val = entry_price * 0.02

            if side == "long":
                sl_price = entry_price - (atr_val * atr_mult)
            else:
                sl_price = entry_price + (atr_val * atr_mult)

        elif raw_sl_type in ["chandelier", "chandelier_exit"]:
            atr_period = int(self.sl_config.get("atr_period", 14))
            atr_mult = float(self.sl_config.get("atr_multiplier", self.sl_config.get("value", 2.5)))
            lookback = int(self.sl_config.get("lookback", 14))
            if 'ATR' not in df.columns or df['ATR'].isnull().all():
                df['ATR'] = self.calculate_atr(df, atr_period)
            atr_val = df['ATR'].iloc[entry_index]
            if pd.isna(atr_val) or atr_val <= 0:
                atr_val = entry_price * 0.02

            start_idx = max(0, entry_index - lookback + 1)
            if side == "long":
                highest_h = float(df['high'].iloc[start_idx:entry_index + 1].max())
                sl_price = highest_h - (atr_val * atr_mult)
            else:
                lowest_l = float(df['low'].iloc[start_idx:entry_index + 1].min())
                sl_price = lowest_l + (atr_val * atr_mult)

        elif raw_sl_type in ["swing", "swing_low_high", "support_resistance"]:
            lookback = int(self.sl_config.get("lookback", self.sl_config.get("value", 10)))
            start_idx = max(0, entry_index - lookback + 1)
            if side == "long":
                swing_low = float(df['low'].iloc[start_idx:entry_index + 1].min())
                sl_price = swing_low if swing_low < entry_price else entry_price * 0.98
            else:
                swing_high = float(df['high'].iloc[start_idx:entry_index + 1].max())
                sl_price = swing_high if swing_high > entry_price else entry_price * 1.02

        elif raw_sl_type == "fixed_price":
            sl_price = sl_val if sl_val > 0 else None

        # ═════════════════════════════════════════════════════════════
        # 2. CÁLCULO DE TAKE PROFIT (TP)
        # ═════════════════════════════════════════════════════════════
        raw_tp_type = str(self.tp_config.get("type", "fixed")).lower().strip().replace(" ", "_")
        tp_val = float(self.tp_config.get("value", 4.0))
        tp_price = None

        if raw_tp_type in ["fixed", "percentage", "partial", "multi_tp"]:
            pct = (tp_val / 100.0) if tp_val != 0 else 0.04
            if side == "long":
                tp_price = entry_price * (1.0 + pct)
            else:
                tp_price = entry_price * (1.0 - pct)

        elif raw_tp_type in ["risk_reward", "risk_reward_ratio", "rr"]:
            rr_ratio = float(self.tp_config.get("risk_reward_ratio", self.tp_config.get("value", 2.0)))
            risk_dist = abs(entry_price - sl_price) if (sl_price and sl_price > 0) else (entry_price * 0.02)
            if side == "long":
                tp_price = entry_price + (risk_dist * rr_ratio)
            else:
                tp_price = entry_price - (risk_dist * rr_ratio)

        elif raw_tp_type in ["atr", "volatility"]:
            atr_period = int(self.tp_config.get("atr_period", 14))
            atr_mult = float(self.tp_config.get("atr_multiplier", self.tp_config.get("value", 3.0)))
            if 'ATR' not in df.columns or df['ATR'].isnull().all():
                df['ATR'] = self.calculate_atr(df, atr_period)
            atr_val = df['ATR'].iloc[entry_index]
            if pd.isna(atr_val) or atr_val <= 0:
                atr_val = entry_price * 0.02

            if side == "long":
                tp_price = entry_price + (atr_val * atr_mult)
            else:
                tp_price = entry_price - (atr_val * atr_mult)

        elif raw_tp_type == "fixed_price":
            tp_price = tp_val if tp_val > 0 else None

        return sl_price, tp_price

    def compute_position_size(self, capital: float, entry_price: float, sl_price: float = None) -> float:
        """
        Calcula el tamaño de la posición basado en el capital y el método de sizing.
        """
        method = str(self.sizing_config.get("method", "compounding")).lower()

        if method in ["compounding", "percent_equity", "full_capital"]:
            pct = float(self.sizing_config.get("value", 100.0)) / 100.0
            return (capital * pct) / entry_price

        elif method == "fixed_fractional" and sl_price is not None:
            risk_pct = float(self.sizing_config.get("risk_per_trade_pct", 1.0)) / 100.0
            risk_amount = capital * risk_pct
            price_risk = abs(entry_price - sl_price)
            if price_risk > 0:
                return risk_amount / price_risk

        elif method == "fixed_amount":
            amount = float(self.sizing_config.get("value", 1000.0))
            return min(amount, capital) / entry_price

        return capital / entry_price

    def update_trailing_sl(
        self,
        current_sl: float,
        current_price: float,
        current_high: float,
        current_low: float,
        current_atr: float = None,
        side: str = "long",
        entry_price: float = None
    ) -> float:
        """
        Actualiza el nivel de Stop Loss dinámicamente según la lógica de Trailing / Break-Even.
        """
        raw_sl_type = str(self.sl_config.get("type", "")).lower().strip().replace(" ", "_")

        # 1. Trailing Stop Porcentual
        if raw_sl_type in ["trailing_percent", "trailing"]:
            sl_pct = float(self.sl_config.get("value", 2.0)) / 100.0
            if side == "long":
                new_sl = current_high * (1.0 - sl_pct)
                return max(current_sl, new_sl) if current_sl is not None else new_sl
            else:
                new_sl = current_low * (1.0 + sl_pct)
                return min(current_sl, new_sl) if current_sl is not None else new_sl

        # 2. Break-Even + Trailing
        elif raw_sl_type in ["break_even", "breakeven"]:
            be_trigger = float(self.sl_config.get("be_trigger_pct", self.sl_config.get("value", 1.5))) / 100.0
            sl_pct = float(self.sl_config.get("trailing_after_be_pct", self.sl_config.get("value", 2.0))) / 100.0
            e_price = entry_price if entry_price is not None else current_sl

            if side == "long":
                if e_price and current_high >= e_price * (1.0 + be_trigger):
                    new_sl = max(e_price, current_high * (1.0 - sl_pct))
                    return max(current_sl, new_sl) if current_sl is not None else new_sl
            else:
                if e_price and current_low <= e_price * (1.0 - be_trigger):
                    new_sl = min(e_price, current_low * (1.0 + sl_pct))
                    return min(current_sl, new_sl) if current_sl is not None else new_sl

        # 3. Chandelier Exit (Trailing ATR)
        elif raw_sl_type in ["chandelier", "chandelier_exit"] or (
            raw_sl_type == "dynamic" and str(self.sl_config.get("dynamic_method")).lower() == "chandelier"
        ):
            if current_atr is not None and current_atr > 0:
                atr_mult = float(self.sl_config.get("atr_multiplier", self.sl_config.get("value", 2.5)))
                if side == "long":
                    new_sl = current_high - (current_atr * atr_mult)
                    return max(current_sl, new_sl) if current_sl is not None else new_sl
                else:
                    new_sl = current_low + (current_atr * atr_mult)
                    return min(current_sl, new_sl) if current_sl is not None else new_sl

        return current_sl
