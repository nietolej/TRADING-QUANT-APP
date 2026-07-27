import os
import requests
import pandas as pd
from datetime import datetime, timezone
from dotenv import load_dotenv
from .base_provider import BaseOnChainProvider

load_dotenv()

class CryptoQuantProvider(BaseOnChainProvider):
    """
    Proveedor para CryptoQuant API.
    Requiere una cuenta gratuita y una API Key en el archivo .env.
    """
    def __init__(self):
        self.api_key = os.getenv("CRYPTOQUANT_API_KEY")
        self.base_url = "https://api.cryptoquant.com/v1"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}"
        }

    def fetch_metric(self, metric_name: str, symbol: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
        if not self.api_key or self.api_key == "tu_clave_api_aqui":
            print("ERROR: API Key de CryptoQuant no configurada.")
            return pd.DataFrame()
            
        # CryptoQuant espera el activo (ej. BTC) en lugar del par completo
        asset = symbol.split('/')[0].lower() if '/' in symbol else symbol.lower()
        
        # Mapeo de métricas internas a endpoints de CryptoQuant
        endpoint_map = {
            "exchange_netflow": f"/btc/exchange-flows/netflow?exchange=all_exchange&window=day",
            "exchange_inflow": f"/btc/exchange-flows/inflow?exchange=all_exchange&window=day",
            "exchange_outflow": f"/btc/exchange-flows/outflow?exchange=all_exchange&window=day",
            "exchange_reserve": f"/btc/exchange-flows/reserve?exchange=all_exchange&window=day",
            "miner_reserve": f"/btc/miner-flows/reserve?window=day",
            "miner_netflow": f"/btc/miner-flows/netflow?window=day",
            "puell_multiple": f"/btc/market-data/puell-multiple?window=day",
            "mvrv": f"/btc/market-data/mvrv?window=day",
            "nvt_golden_cross": f"/btc/market-data/nvt-golden-cross?window=day",
            "sopr": f"/btc/market-data/sopr?window=day",
            "active_addresses": f"/btc/network-data/active-addresses?window=day",
            "funding_rates": f"/btc/market-data/funding-rates?window=day",
            "open_interest": f"/btc/market-data/open-interest?window=day",
            "estimated_leverage_ratio": f"/btc/market-data/estimated-leverage-ratio?window=day",
            "taker_buy_sell_ratio": f"/btc/market-data/taker-buy-sell-ratio?window=day",
            "nupl": f"/btc/market-data/nupl?window=day",
            "stock_to_flow": f"/btc/market-data/stock-to-flow?window=day"
        }
        
        # Por ahora CryptoQuant API v1 a menudo requiere especificar si es btc o eth
        # Usaremos BTC para el ejemplo si el activo es BTC
        if asset not in ["btc", "eth"]:
            asset = "btc" # Default to BTC for CryptoQuant metrics if symbol is GLOBAL or generic


        endpoint = endpoint_map.get(metric_name)
        if not endpoint:
            print(f"Métrica {metric_name} no soportada por CryptoQuantProvider.")
            return pd.DataFrame()

        url = f"{self.base_url}{endpoint}"
        
        # Parámetros de tiempo
        limit = 10000
        params = {
            "limit": limit,
            # Se podría añadir from y to dependiendo de la documentación específica del endpoint, 
            # pero típicamente CryptoQuant v1 trae el histórico completo o paginado.
        }

        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            # Formato de respuesta típico de CryptoQuant:
            # {"result": {"data": [{"date": "2021-01-01", "netflow_total": 1234}, ...]}}
            
            results = data.get("result", {}).get("data", [])
            if not results:
                return pd.DataFrame()

            records = []
            for item in results:
                # El campo de fecha y valor varían por endpoint, asumimos formato general:
                ts_str = item.get("date")
                if not ts_str: continue
                ts = pd.to_datetime(ts_str, utc=True)
                
                if start_date <= ts <= (end_date or datetime.now(timezone.utc)):
                    # Buscar la primera llave numérica
                    val = None
                    for k, v in item.items():
                        if k != "date" and isinstance(v, (int, float)):
                            val = v
                            break
                            
                    if val is not None:
                        records.append({
                            'timestamp': ts,
                            'metric_name': metric_name,
                            'symbol': symbol,
                            'value': float(val),
                            'source': 'cryptoquant'
                        })
                        
            return pd.DataFrame(records)

        except Exception as e:
            print(f"Error fetching CryptoQuant data: {e}")
            raise Exception(f"CryptoQuant API Error: {e}")
