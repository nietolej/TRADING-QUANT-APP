import yaml
import pandas as pd
from typing import Dict, Any
from .conditions import ConditionEvaluator
from .risk_management import RiskManager

class BaseStrategy:
    """
    Clase base para cargar y evaluar una estrategia desde un archivo YAML.
    """
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
            self.parameters.update(custom_parameters)
            
        if self.parameters:
            self._apply_parameters(self.config, self.parameters)
            
        self.risk_manager = RiskManager(self.config.get("risk_management", {}))
        
    def _apply_parameters(self, config_dict, params):
        if isinstance(config_dict, dict):
            for k, v in config_dict.items():
                if isinstance(v, str) and v in params:
                    config_dict[k] = params[v]
                elif isinstance(v, str) and v.startswith("@") and v[1:] in params:
                    config_dict[k] = params[v[1:]]
                elif isinstance(v, (dict, list)):
                    self._apply_parameters(v, params)
        elif isinstance(config_dict, list):
            for i, v in enumerate(config_dict):
                if isinstance(v, str) and v in params:
                    config_dict[i] = params[v]
                elif isinstance(v, str) and v.startswith("@") and v[1:] in params:
                    config_dict[i] = params[v[1:]]
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
