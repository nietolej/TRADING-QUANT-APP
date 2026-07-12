import requests
import pandas as pd
from datetime import datetime, timezone
from .base_provider import BaseOnChainProvider

class DefiLlamaProvider(BaseOnChainProvider):
    def fetch_metric(self, metric_name: str, symbol: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
        """
        Para el MVP, simularemos o descargaremos data básica.
        DefiLlama es ideal para Total Value Locked (TVL) y Stablecoin Market Cap.
        """
        # Endpoint de ejemplo para obtener el histórico de mcap de stablecoins:
        # https://stablecoins.llama.fi/stablecoincharts/all
        
        if metric_name == "stablecoin_market_cap":
            url = "https://stablecoins.llama.fi/stablecoincharts/all"
            try:
                response = requests.get(url)
                response.raise_for_status()
                data = response.json()
                
                if start_date.tzinfo is None:
                    start_date = start_date.replace(tzinfo=timezone.utc)
                if end_date and end_date.tzinfo is None:
                    end_date = end_date.replace(tzinfo=timezone.utc)
                end_limit = end_date or datetime.now(timezone.utc)
                
                records = []
                for item in data:
                    ts = pd.to_datetime(int(item['date']), unit='s', utc=True)
                    if start_date <= ts <= end_limit:
                        records.append({
                            'timestamp': ts,
                            'metric_name': metric_name,
                            'symbol': symbol,
                            'value': float(item['totalCirculatingUSD']['peggedUSD']),
                            'source': 'defillama'
                        })
                        
                df = pd.DataFrame(records)
                return df
            except Exception as e:
                print(f"Error fetching DefiLlama data: {e}")
                return pd.DataFrame()
                
        elif metric_name in ["usdt_market_cap", "usdc_market_cap"]:
            # Defillama IDs para stablecoins: 1 = USDT, 2 = USDC
            sc_id = "1" if metric_name == "usdt_market_cap" else "2"
            url = f"https://stablecoins.llama.fi/stablecoincharts/all?stablecoin={sc_id}"
            
            try:
                response = requests.get(url)
                response.raise_for_status()
                data = response.json()
                
                if start_date.tzinfo is None:
                    start_date = start_date.replace(tzinfo=timezone.utc)
                if end_date and end_date.tzinfo is None:
                    end_date = end_date.replace(tzinfo=timezone.utc)
                end_limit = end_date or datetime.now(timezone.utc)
                
                records = []
                for item in data:
                    ts = pd.to_datetime(int(item['date']), unit='s', utc=True)
                    if start_date <= ts <= end_limit:
                        records.append({
                            'timestamp': ts,
                            'metric_name': metric_name,
                            'symbol': symbol,
                            'value': float(item['totalCirculatingUSD']['peggedUSD']),
                            'source': 'defillama'
                        })
                        
                df = pd.DataFrame(records)
                return df
            except Exception as e:
                print(f"Error fetching DefiLlama data para {metric_name}: {e}")
                return pd.DataFrame()
                
        return pd.DataFrame()
