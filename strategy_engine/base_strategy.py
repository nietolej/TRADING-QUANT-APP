import yaml
import pandas as pd
from typing import Dict, Any
from .conditions import ConditionEvaluator
from .risk_management import RiskManager

class BaseStrategy:
    """
    Clase base para cargar y evaluar una estrategia desde un archivo YAML.
    """
    def __new__(cls, config_or_path, custom_parameters=None):
        if cls is BaseStrategy:
            cfg = None
            if isinstance(config_or_path, str):
                try:
                    with open(config_or_path, 'r', encoding='utf-8') as f:
                        cfg = yaml.safe_load(f)
                except Exception:
                    pass
            elif isinstance(config_or_path, dict):
                cfg = config_or_path

            if cfg and isinstance(cfg, dict):
                c_name = cfg.get("class_name")
                if c_name == "StablecoinEmissionEMAStrategy":
                    from .stablecoin_momentum_strategy import StablecoinEmissionEMAStrategy
                    return super().__new__(StablecoinEmissionEMAStrategy)
                elif c_name == "OnChainFlowStrategy":
                    from .onchain_flow_strategy import OnChainFlowStrategy
                    return super().__new__(OnChainFlowStrategy)

        return super().__new__(cls)

    def __init__(self, config_or_path, custom_parameters=None):
        if isinstance(config_or_path, str):
            self.config = self._load_config(config_or_path)
        elif isinstance(config_or_path, dict):
            self.config = config_or_path
        else:
            raise TypeError("Expected a string path or a dictionary config.")
            
        self.name = self.config.get("strategy_name", "Unknown Strategy")
        self.symbol = self.config.get("symbol", "BTC/USDT")
        self.timeframe = self.config.get("timeframe", "1h")
        
        # Parameter substitution
        self.parameters = self.config.get("parameters", {})
        if custom_parameters:
            cleaned_params = {}
            for k, v in custom_parameters.items():
                if isinstance(v, str):
                    v_str = v.strip()
                    try:
                        if '.' in v_str:
                            cleaned_params[k] = float(v_str)
                        else:
                            cleaned_params[k] = int(v_str)
                    except ValueError:
                        cleaned_params[k] = v
                else:
                    cleaned_params[k] = v
            self.parameters.update(cleaned_params)
            
        if self.parameters:
            self._apply_parameters(self.config, self.parameters)
            
            # Sync SL and TP parameters into risk_management section if parameters specify SL/TP
            rm = self.config.get("risk_management", {})
            sl_param = self.parameters.get("SL") or self.parameters.get("stop_loss") or self.parameters.get("sl")
            tp_param = self.parameters.get("TP") or self.parameters.get("take_profit") or self.parameters.get("tp")
            
            if sl_param is not None:
                try:
                    sl_val = float(sl_param)
                    if sl_val > 0:
                        rm_sl = rm.get("stop_loss", {})
                        if rm_sl.get("type", "none") in ("none", "", None):
                            rm["stop_loss"] = {"type": "percentage", "value": sl_val}
                except (TypeError, ValueError):
                    pass

            if tp_param is not None:
                try:
                    tp_val = float(tp_param)
                    if tp_val > 0:
                        rm_tp = rm.get("take_profit", {})
                        if rm_tp.get("type", "none") in ("none", "", None):
                            rm["take_profit"] = {"type": "percentage", "value": tp_val}
                except (TypeError, ValueError):
                    pass

            self.config["risk_management"] = rm

        self.risk_manager = RiskManager(self.config.get("risk_management", {}))
        
    def _resolve_param_value(self, raw_val, params):
        if not isinstance(raw_val, str):
            return raw_val
        val_str = raw_val.strip()
        val_no_at = val_str[1:] if val_str.startswith('@') else val_str
        
        # 1. Exact matches
        if raw_val in params:
            return params[raw_val]
        if val_str in params:
            return params[val_str]
        if val_no_at in params:
            return params[val_no_at]

        # 2. Case-insensitive & normalized matches
        params_norm = {str(k).strip().lstrip('@').lower(): v for k, v in params.items()}
        key_norm = val_no_at.lower()
        if key_norm in params_norm:
            return params_norm[key_norm]

        return raw_val

    def _apply_parameters(self, config_dict, params):
        if isinstance(config_dict, dict):
            for k, v in list(config_dict.items()):
                if isinstance(v, str):
                    resolved = self._resolve_param_value(v, params)
                    if resolved != v:
                        config_dict[k] = resolved
                elif isinstance(v, (dict, list)):
                    self._apply_parameters(v, params)
        elif isinstance(config_dict, list):
            for i, v in enumerate(config_dict):
                if isinstance(v, str):
                    resolved = self._resolve_param_value(v, params)
                    if resolved != v:
                        config_dict[i] = resolved
                elif isinstance(v, (dict, list)):
                    self._apply_parameters(v, params)
        
    def _load_config(self, config_path: str) -> Dict[str, Any]:
        with open(config_path, 'r', encoding='utf-8') as file:
            return yaml.safe_load(file)
            
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Evalúa las condiciones de entrada y salida sobre el histórico y genera columnas booleanas.
        """
        df = df.copy()
        
        direction = self.config.get("trade_direction", "Long").lower()
        
        # Calcular condiciones de entrada
        entry_conditions = self.config.get("entry_conditions", {})
        entry_signals = ConditionEvaluator.evaluate_conditions(df, entry_conditions)
        
        # Calcular condiciones de salida
        exit_conditions = self.config.get("exit_conditions", {})
        exit_signals = ConditionEvaluator.evaluate_conditions(df, exit_conditions)
        
        if direction == "short":
            df['entry_short'] = entry_signals
            df['exit_short'] = exit_signals
            df['entry_long'] = False
            df['exit_long'] = False
        else:
            df['entry_long'] = entry_signals
            df['exit_long'] = exit_signals
            df['entry_short'] = False
            df['exit_short'] = False
            
        return df
