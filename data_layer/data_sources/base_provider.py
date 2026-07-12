from abc import ABC, abstractmethod
import pandas as pd
from datetime import datetime

class BaseOnChainProvider(ABC):
    """
    Interfaz abstracta para todos los proveedores de datos on-chain.
    Cualquier nueva fuente (CoinGecko, DefiLlama, Glassnode, etc.) debe implementar esta clase.
    """
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key

    @abstractmethod
    def fetch_metric(self, metric_name: str, symbol: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
        """
        Descarga una métrica on-chain y retorna un DataFrame estandarizado.
        
        El DataFrame devuelto debe tener obligatoriamente las columnas:
        - timestamp (datetime)
        - metric_name (str)
        - symbol (str)
        - value (float)
        - source (str)
        """
        pass
