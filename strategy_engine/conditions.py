import pandas as pd
import numpy as np
import ta

class ConditionEvaluator:
    """
    Evalúa condiciones técnicas y on-chain sobre un DataFrame.
    """
    
    @staticmethod
    def evaluate_rule(df: pd.DataFrame, rule: dict) -> pd.Series:
        rule_type = rule.get("type")
        
        if rule_type == "ma_cross":
            return ConditionEvaluator._eval_ma_cross(df, rule)
        elif rule_type == "rsi_threshold":
            return ConditionEvaluator._eval_rsi(df, rule)
        elif rule_type == "onchain_threshold":
            return ConditionEvaluator._eval_onchain(df, rule)
        elif rule_type == "technical_indicator":
            return ConditionEvaluator._eval_technical_indicator(df, rule)
        else:
            raise ValueError(f"Regla no soportada: {rule_type}")
            
    @staticmethod
    def _eval_ma_cross(df: pd.DataFrame, rule: dict) -> pd.Series:
        fast_period = rule.get("fast_period", 20)
        slow_period = rule.get("slow_period", 50)
        ma_type = rule.get("ma_type", "SMA")
        direction = rule.get("direction", "bullish")
        
        if ma_type == "SMA":
            fast_ma = ta.trend.sma_indicator(df['close'], window=fast_period)
            slow_ma = ta.trend.sma_indicator(df['close'], window=slow_period)
        elif ma_type == "EMA":
            fast_ma = ta.trend.ema_indicator(df['close'], window=fast_period)
            slow_ma = ta.trend.ema_indicator(df['close'], window=slow_period)
        else:
            raise ValueError("ma_type debe ser SMA o EMA")
            
        # Cruce
        if direction == "bullish":
            # Fast cruza hacia arriba la Slow
            cross = (fast_ma > slow_ma) & (fast_ma.shift(1) <= slow_ma.shift(1))
        else:
            # Fast cruza hacia abajo la Slow
            cross = (fast_ma < slow_ma) & (fast_ma.shift(1) >= slow_ma.shift(1))
            
        return cross
        
    @staticmethod
    def _eval_rsi(df: pd.DataFrame, rule: dict) -> pd.Series:
        period = rule.get("period", 14)
        condition = rule.get("condition", "above")
        value = rule.get("value", 70)
        
        rsi = ta.momentum.rsi(df['close'], window=period)
        
        if condition == "above":
            return rsi > value
        elif condition == "below":
            return rsi < value
        elif condition == "cross_above":
            return (rsi > value) & (rsi.shift(1) <= value)
        elif condition == "cross_below":
            return (rsi < value) & (rsi.shift(1) >= value)
            
        return pd.Series(False, index=df.index)

    @staticmethod
    def _eval_technical_indicator(df: pd.DataFrame, rule: dict) -> pd.Series:
        ind1 = rule.get("indicator1", "EMA")
        p1 = rule.get("period1", 20)
        op = rule.get("operator", "crosses_above")
        ind2 = rule.get("indicator2", "Price")
        p2 = rule.get("period2", 50)
        
        def get_series(ind, p):
            if ind == "Price": return df['close']
            if ind == "Volume": return df['volume']
            if ind == "SMA": return ta.trend.sma_indicator(df['close'], window=p)
            if ind == "EMA": return ta.trend.ema_indicator(df['close'], window=p)
            return df['close']
            
        s1 = get_series(ind1, p1)
        s2 = get_series(ind2, p2)
        
        if op == "crosses_above": return (s1 > s2) & (s1.shift(1) <= s2.shift(1))
        elif op == "crosses_below": return (s1 < s2) & (s1.shift(1) >= s2.shift(1))
        elif op == "is_above": return s1 > s2
        elif op == "is_below": return s1 < s2
        
        return pd.Series(False, index=df.index)

    @staticmethod
    def _eval_onchain(df: pd.DataFrame, rule: dict) -> pd.Series:
        metric = rule.get("metric")
        condition = rule.get("condition", "above")
        value = rule.get("value", 0)
        
        if metric not in df.columns:
            # Si no existe la métrica en el dataset (ej. no se unieron los datos), devolver False
            return pd.Series(False, index=df.index)
            
        if condition == "above":
            return df[metric] > value
        elif condition == "below":
            return df[metric] < value
        elif condition == "increasing":
            lookback_days = rule.get("lookback_days", 1)
            min_change_pct = rule.get("min_change_pct", 0)
            
            # Aproximación del lookback asumiendo datos diarios para on-chain
            past_val = df[metric].shift(lookback_days)
            pct_change = (df[metric] - past_val) / past_val * 100
            return pct_change > min_change_pct
            
        return pd.Series(False, index=df.index)

    @staticmethod
    def evaluate_conditions(df: pd.DataFrame, conditions: dict) -> pd.Series:
        """
        Evalúa un bloque completo de condiciones (AND/OR).
        """
        if not conditions or not conditions.get("rules"):
            return pd.Series(False, index=df.index)
            
        logic = conditions.get("logic", "AND").upper()
        rules = conditions.get("rules", [])
        
        result = None
        
        for rule in rules:
            rule_eval = ConditionEvaluator.evaluate_rule(df, rule)
            if result is None:
                result = rule_eval
            else:
                if logic == "AND":
                    result = result & rule_eval
                else:
                    result = result | rule_eval
                    
        return result.fillna(False)
