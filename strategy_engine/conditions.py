import copy

import pandas as pd
import numpy as np
import ta

# Operador de "cruce" (evento) -> su equivalente de "estado", para la salida por estado.
STATE_OPERATOR = {"crosses_below": "is_below", "crosses_above": "is_above"}


def state_exit_conditions(exit_conditions: dict):
    """
    Versión "por estado" de las reglas de salida: cada cruce se convierte en la condición de
    estado equivalente (EMA rápida POR DEBAJO de la lenta, en vez de "la cruza hacia abajo").
    La usa el motor en vivo para salir aunque el evento de cruce no se haya visto, y el
    backtest para reproducir esa misma regla. Devuelve None si no aplica: sin reglas de cruce
    técnicas, o con lógica AND y reglas que no se pueden convertir (evaluar solo una parte de
    un AND cambiaría su significado).
    """
    exit_cfg = copy.deepcopy(exit_conditions or {})
    rules = exit_cfg.get("rules", []) or []
    state_rules = []
    for rule in rules:
        if rule.get("type") == "technical_indicator" and rule.get("operator") in STATE_OPERATOR:
            rule["operator"] = STATE_OPERATOR[rule["operator"]]
            state_rules.append(rule)
    if not state_rules:
        return None
    if len(state_rules) != len(rules) and str(exit_cfg.get("logic", "OR")).upper() != "OR":
        return None
    exit_cfg["rules"] = state_rules
    return exit_cfg


def is_short_direction(config: dict) -> bool:
    """Misma regla de dirección en backtest y en vivo ('Short', 'short', ...)."""
    return "short" in str((config or {}).get("trade_direction", "Long")).strip().lower()


def apply_state_exit(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Añade a exit_long/exit_short la salida por estado del motor en vivo. Sin esto el backtest
    solo salía con el evento de cruce, mientras el bot también sale si al cierre de una vela
    la condición se cumple como estado: distinto cuando las reglas de salida usan indicadores
    diferentes a las de entrada (ej. entrar con EMA1>EMA35 y salir con EMA1<EMA10).
    """
    cfg = state_exit_conditions((config or {}).get("exit_conditions", {}))
    if cfg is None:
        return df
    state = ConditionEvaluator.evaluate_conditions(df, cfg).fillna(False).astype(bool)
    col = "exit_short" if is_short_direction(config) else "exit_long"
    df[col] = df[col].fillna(False).astype(bool) | state if col in df.columns else state
    return df

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
        ind1_dict = rule.get("indicator_1", {})
        ind2_dict = rule.get("indicator_2", {})
        
        ind1 = ind1_dict.get("name") if ind1_dict else rule.get("indicator1", "EMA")
        p1 = ind1_dict.get("period") if ind1_dict else rule.get("period1", 20)
        
        op = rule.get("operator", "crosses_above")
        
        ind2 = ind2_dict.get("name") if ind2_dict else rule.get("indicator2", "Price")
        p2 = ind2_dict.get("period") if ind2_dict else rule.get("period2", 50)
        
        def get_series(ind, p):
            try:
                p_int = max(1, int(float(p)))
            except (ValueError, TypeError):
                p_int = 20
            if ind == "Price": return df['close']
            if ind == "Volume": return df['volume']
            if ind == "SMA": return ta.trend.sma_indicator(df['close'], window=p_int)
            if ind == "EMA": return ta.trend.ema_indicator(df['close'], window=p_int)
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
            # Si past_val es 0 (o NaN por estar al inicio de la serie), la división genera
            # +/-inf o NaN. inf > min_change_pct evalúa True, disparando una señal de
            # "incremento" falsa que no representa un cambio real. Esas filas deben quedar
            # sin señal (False), no un comparador silenciosamente engañado por -inf/inf.
            pct_change = (df[metric] - past_val) / past_val.replace(0, np.nan) * 100
            pct_change = pct_change.replace([np.inf, -np.inf], np.nan)
            return (pct_change > min_change_pct).fillna(False)
            
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
