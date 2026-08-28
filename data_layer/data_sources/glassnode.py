import os
import requests
import pandas as pd
from datetime import datetime, timezone
from dotenv import load_dotenv
from .base_provider import BaseOnChainProvider

load_dotenv()

class GlassnodeProvider(BaseOnChainProvider):
    """
    Proveedor para Glassnode API.
    Requiere una cuenta (Tier 1 gratuito disponible) y una API Key en el archivo .env.
    Nota: Las métricas de Tier 1 suelen tener un retraso de 24h a 48h.
    """
    def __init__(self):
        self.api_key = os.getenv("GLASSNODE_API_KEY")
        self.base_url = "https://api.glassnode.com/v1/metrics"
        
    def fetch_metric(self, metric_name: str, symbol: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
        if not self.api_key or self.api_key == "tu_clave_glassnode_aqui":
            raise ValueError("API Key de Glassnode no configurada en el archivo .env")
            
        asset = symbol.split('/')[0].upper() if '/' in symbol else symbol.upper()
        
        # Mapeo de métricas internas a endpoints de Glassnode
        # Glassnode estructura sus endpoints por categoría, ej: /indicators/sopr, /transactions/transfers_volume_sum
        endpoint_map = {
            "sopr": "/indicators/sopr",
            "puell_multiple": "/indicators/puell_multiple",
            "mvrv": "/market/mvrv",
            "nupl": "/indicators/net_unrealized_profit_loss",
            "active_addresses": "/addresses/active_count",
            "exchange_netflow": "/transactions/transfers_volume_exchanges_net",
            "exchange_inflow": "/transactions/transfers_volume_to_exchanges_sum",
            "exchange_outflow": "/transactions/transfers_volume_from_exchanges_sum",
            "exchange_reserve": "/distribution/balance_exchanges",
            "miner_reserve": "/distribution/balance_miners_all"
        }
        
        endpoint = endpoint_map.get(metric_name)
        if not endpoint:
            print(f"Métrica {metric_name} no mapeada o soportada para GlassnodeProvider.")
            return pd.DataFrame()

        url = f"{self.base_url}{endpoint}"
        
        # Parámetros (a = asset, s = since, u = until)
        # Glassnode espera timestamps UNIX
        s_ts = int(start_date.timestamp())
        u_ts = int((end_date or datetime.now(timezone.utc)).timestamp())
        
        params = {
            "a": asset,
            "api_key": self.api_key,
            "s": s_ts,
            "u": u_ts,
            "i": "24h" # Resolución diaria (Tier 1 default)
        }

        try:
            response = requests.get(url, params=params)
            
            # Glassnode devuelve 401 si la API key es inválida, 
            # y 403 si la métrica requiere un Tier de pago (Tier 2/3)
            if response.status_code == 401:
                raise ValueError("API Key de Glassnode inválida.")
            elif response.status_code == 403:
                raise ValueError(f"La métrica '{metric_name}' requiere una suscripción de pago (Tier 2/3) en Glassnode.")
                
            response.raise_for_status()
            data = response.json()
            
            # Formato de respuesta de Glassnode:
            # [{"t": 1609459200, "v": 12345.67}, ...]
            
            if not isinstance(data, list) or not data:
                return pd.DataFrame()

            records = []
            for item in data:
                ts_unix = item.get("t")
                val = item.get("v")
                
                if ts_unix is None or val is None:
                    continue
                    
                ts = datetime.fromtimestamp(ts_unix, tz=timezone.utc)
                
                if start_date <= ts <= (end_date or datetime.now(timezone.utc)):
                    records.append({
                        'timestamp': ts,
                        'metric_name': metric_name,
                        'symbol': symbol,
                        'value': float(val),
                        'source': 'glassnode'
                    })
                        
            return pd.DataFrame(records)

        except requests.exceptions.RequestException as e:
            print(f"Error HTTP fetching Glassnode data: {e}")
            raise Exception(f"Error conectando con Glassnode: {e}")
        except Exception as e:
            print(f"Error parsing Glassnode data: {e}")
            raise Exception(f"Glassnode API Error: {e}")
