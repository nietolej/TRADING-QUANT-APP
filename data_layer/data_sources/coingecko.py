import requests
import pandas as pd
from datetime import datetime, timezone
import time
from dotenv import load_dotenv
import os
from .base_provider import BaseOnChainProvider

load_dotenv()

class CoinGeckoProvider(BaseOnChainProvider):
    """
    Proveedor para CoinGecko API.
    Utiliza el tier gratuito (API Key en COINGECKO_API_KEY).
    """
    def __init__(self):
        self.base_url = "https://api.coingecko.com/api/v3"
        self.api_key = os.getenv("COINGECKO_API_KEY")

    def fetch_metric(self, metric_name: str, symbol: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
        """
        metric_name soportados: 
        - "btc_market_cap"
        - "btc_volume"
        """
        if not self.api_key:
            print("ERROR: COINGECKO_API_KEY no configurada. CoinGecko ahora requiere una demo key gratuita.")
            return pd.DataFrame()

        # Mapeo de metric_name a datos de CoinGecko
        if metric_name not in ["btc_market_cap", "btc_volume"]:
            print(f"CoinGeckoProvider no soporta la métrica: {metric_name}")
            return pd.DataFrame()

        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        if end_date and end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
        end_limit = end_date or datetime.now(timezone.utc)

        # Ventana realmente pedida (antes se ignoraba y siempre se bajaban 365 días fijos,
        # gastando cuota de la API gratuita en cada sincronización incremental aunque solo
        # faltara un día). El tier gratuito de CoinGecko limita el histórico diario a 365 días.
        days_requested = max(1, (end_limit - start_date).days + 1)
        days = min(days_requested, 365)

        # Para el MVP, usaremos la moneda "bitcoin"
        url = f"{self.base_url}/coins/bitcoin/market_chart"
        headers = {
            "x-cg-demo-api-key": self.api_key
        }
        params = {
            "vs_currency": "usd",
            "days": str(days),
            "interval": "daily"
        }

        try:
            # Respetar rate limits de CoinGecko (10-50 calls/min)
            time.sleep(1.5)
            response = requests.get(url, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()

            records = []
            
            # Extraer la serie correcta
            target_series = "market_caps" if metric_name == "btc_market_cap" else "total_volumes"
            
            for item in data.get(target_series, []):
                # item es una lista [timestamp_ms, valor]
                ts_ms = item[0]
                val = item[1]
                ts = pd.to_datetime(ts_ms, unit='ms', utc=True)
                
                if start_date <= ts <= end_limit:
                    records.append({
                        'timestamp': ts,
                        'metric_name': metric_name,
                        'symbol': symbol,  # Usualmente BTC/USDT u otro que el usuario pida
                        'value': float(val),
                        'source': 'coingecko'
                    })
                    
            df = pd.DataFrame(records)
            return df
            
        except Exception as e:
            print(f"Error fetching CoinGecko data para {metric_name}: {e}")
            return pd.DataFrame()
