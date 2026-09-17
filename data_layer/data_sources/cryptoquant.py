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
            raise ValueError("API Key de CryptoQuant no configurada en el archivo .env")

        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        if end_date and end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)

        # CryptoQuant espera el activo (ej. BTC) en lugar del par completo. Solo BTC y ETH
        # tienen cobertura de métricas en la API v1; cualquier otro símbolo cae a BTC por
        # defecto. Este `asset` DEBE usarse al construir cada endpoint: antes quedaba
        # calculado pero sin usar, y todos los endpoints estaban hardcodeados a /btc/, así
        # que pedir métricas de ETH devolvía (y guardaba en la BD) datos de Bitcoin
        # etiquetados como si fueran de otro activo.
        asset = symbol.split('/')[0].lower() if '/' in symbol else symbol.lower()
        if asset not in ["btc", "eth"]:
            asset = "btc"

        # Mapeo de métricas internas a endpoints de CryptoQuant
        endpoint_map = {
            "exchange_netflow": f"/{asset}/exchange-flows/netflow?exchange=all_exchange&window=day",
            "exchange_inflow": f"/{asset}/exchange-flows/inflow?exchange=all_exchange&window=day",
            "exchange_outflow": f"/{asset}/exchange-flows/outflow?exchange=all_exchange&window=day",
            "exchange_reserve": f"/{asset}/exchange-flows/reserve?exchange=all_exchange&window=day",
            "miner_reserve": f"/{asset}/miner-flows/reserve?window=day",
            "miner_netflow": f"/{asset}/miner-flows/netflow?window=day",
            # Rutas verificadas contra el OpenAPI spec real de CryptoQuant
            # (https://docs.cryptoquant.com/openapi/v1.json): puell-multiple, nvt-golden-cross,
            # nupl y stock-to-flow viven bajo 'network-indicator', no 'market-data' (devolvian
            # 404 con la ruta anterior); mvrv y sopr viven bajo 'market-indicator'; y
            # 'active-addresses' no existe como tal, el campo real es 'addresses-count' bajo
            # 'network-data'. Nota: ninguno de estos 6 indicadores existe para ETH en el spec
            # (solo estimated_leverage_ratio sí), asi que para ETH seguiran devolviendo 404.
            "puell_multiple": f"/{asset}/network-indicator/puell-multiple?window=day",
            "mvrv": f"/{asset}/market-indicator/mvrv?window=day",
            "nvt_golden_cross": f"/{asset}/network-indicator/nvt-golden-cross?window=day",
            "sopr": f"/{asset}/market-indicator/sopr?window=day",
            "active_addresses": f"/{asset}/network-data/addresses-count?window=day",
            "funding_rates": f"/{asset}/market-data/funding-rates?window=day",
            "open_interest": f"/{asset}/market-data/open-interest?window=day",
            "estimated_leverage_ratio": f"/{asset}/market-indicator/estimated-leverage-ratio?window=day",
            # 'taker-buy-sell-ratio' no existe en la API real; el endpoint correcto es
            # 'taker-buy-sell-stats' (verificado contra el OpenAPI spec).
            "taker_buy_sell_ratio": f"/{asset}/market-data/taker-buy-sell-stats?window=day",
            "nupl": f"/{asset}/network-indicator/nupl?window=day",
            "stock_to_flow": f"/{asset}/network-indicator/stock-to-flow?window=day"
        }

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
            response = requests.get(url, headers=self.headers, params=params, timeout=15)
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
