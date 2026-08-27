import os
import time
import requests
import pandas as pd
from datetime import datetime, timezone
import logging
from sqlalchemy.orm import Session
from .storage import OnChainMetric, SessionLocal

logger = logging.getLogger(__name__)

class BlockExplorerClient:
    """
    Cliente para descargar Mints, Burns y Exchange Flows directamente de Etherscan y Tronscan.
    """
    def __init__(self, db_session: Session = None):
        self.db = db_session or SessionLocal()
        self.etherscan_key = os.getenv("ETHERSCAN_API_KEY")
        self.tronscan_key = os.getenv("TRONSCAN_API_KEY")
        
        # Diccionarios simplificados de direcciones conocidas (para el MVP del motor)
        # 0x0 para mints y burns de tokens ERC20
        self.ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
        
        # Direcciones de USDT y USDC en Ethereum
        self.TOKENS = {
            "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
            "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        }

        # Wallets calientes de Exchanges conocidos (mock-ups de direcciones reales para la arquitectura)
        self.EXCHANGE_WALLETS = [
            "0x28C6c06298d514Db089934071355E5743bf21d60", # Binance 14
            "0xF977814e90dA44bFA03b6295A0616a897441aceC"  # Binance 8
        ]

    def _fetch_etherscan_transfers(self, contract_address: str, target_address: str, min_timestamp: float = 0, max_pages: int = 50) -> list:
        if not self.etherscan_key:
            raise ValueError("Falta configurar 'ETHERSCAN_API_KEY' en el archivo .env")
            
        url = "https://api.etherscan.io/v2/api"
        all_results = []
        
        for page in range(1, max_pages + 1):
            params = {
                "chainid": 1,
                "module": "account",
                "action": "tokentx",
                "contractaddress": contract_address,
                "address": target_address,
                "page": page,
                "offset": 1000,
                "startblock": 0,
                "endblock": 99999999,
                "sort": "desc",
                "apikey": self.etherscan_key
            }
            try:
                res = requests.get(url, params=params)
                data = res.json()
                if data.get("status") == "1":
                    batch = data.get("result", [])
                    all_results.extend(batch)
                    
                    if len(batch) < 1000:
                        break  # No hay más resultados disponibles
                        
                    last_ts = int(batch[-1]['timeStamp'])
                    if last_ts < min_timestamp:
                        break  # Alcanzamos el límite de fecha histórico solicitado
                else:
                    break
            except Exception as e:
                logger.error(f"Error fetching from Etherscan: {e}")
                break
                
            time.sleep(0.3)  # Rate limit para APIs gratuitas
            
        return all_results

    def fetch_stablecoin_supply(self, symbol: str, start_date: datetime):
        """
        Calcula y guarda Mints y Burns (basados en transferencias desde/hacia la dirección cero).
        """
        contract = self.TOKENS.get(symbol.upper())
        if not contract:
            logger.error(f"Token {symbol} no soportado en BlockExplorerClient.")
            return 0
            
        logger.info(f"Descargando transferencias a Zero Address (Mints/Burns) para {symbol}")
        # Asumimos Mints (de 0x0 a cualquier lugar) y Burns (de cualquier lugar a 0x0)
        # Al filtrar por "address" = 0x0 en Etherscan, nos trae transferencias donde 0x0 es from o to.
        min_ts = start_date.replace(tzinfo=timezone.utc).timestamp()
        txs = self._fetch_etherscan_transfers(contract, self.ZERO_ADDRESS, min_timestamp=min_ts)
        
        records = []
        decimals = 6 # USDT y USDC usan 6 decimales
        
        for tx in txs:
            try:
                val = float(tx['value']) / (10 ** decimals)
                ts = datetime.fromtimestamp(int(tx['timeStamp']), tz=timezone.utc)
                if ts < start_date.replace(tzinfo=timezone.utc):
                    continue
                    
                is_mint = tx['from'].lower() == self.ZERO_ADDRESS.lower()
                
                records.append({
                    "metric_name": f"{symbol}_Mint" if is_mint else f"{symbol}_Burn",
                    "symbol": symbol,
                    "timestamp": ts,
                    "value": val,
                    "source": "etherscan"
                })
            except Exception as e:
                pass
                
        return self._save_records(records)

    def fetch_exchange_flows(self, symbol: str, start_date: datetime):
        """
        Calcula y guarda Inflows (depósitos) y Outflows (retiros) de Exchanges.
        """
        contract = self.TOKENS.get(symbol.upper())
        if not contract:
            return 0
            
        records = []
        decimals = 6
        
        for wallet in self.EXCHANGE_WALLETS:
            logger.info(f"Descargando Exchange Flows para {symbol} en wallet {wallet}")
            min_ts = start_date.replace(tzinfo=timezone.utc).timestamp()
            txs = self._fetch_etherscan_transfers(contract, wallet, min_timestamp=min_ts)
            
            for tx in txs:
                try:
                    val = float(tx['value']) / (10 ** decimals)
                    ts = datetime.fromtimestamp(int(tx['timeStamp']), tz=timezone.utc)
                    if ts < start_date.replace(tzinfo=timezone.utc):
                        continue
                        
                    # Si el exchange recibe, es un Inflow. Si envía, es un Outflow.
                    is_inflow = tx['to'].lower() == wallet.lower()
                    
                    records.append({
                        "metric_name": f"{symbol}_Exchange_Inflow" if is_inflow else f"{symbol}_Exchange_Outflow",
                        "symbol": symbol,
                        "timestamp": ts,
                        "value": val,
                        "source": "etherscan"
                    })
                except Exception as e:
                    pass
                    
            time.sleep(0.5) # Rate limit
            
        return self._save_records(records)
        
    def fetch_total_supply(self, symbol: str, start_date: datetime):
        """
        Descarga el Total Supply histórico (Market Cap) usando la API gratuita de CoinGecko.
        Para stablecoins (precio = 1 USD), Market Cap == Circulating / Total Supply.
        """
        cg_ids = {
            "USDT": "tether",
            "USDC": "usd-coin"
        }
        
        coin_id = cg_ids.get(symbol.upper())
        if not coin_id:
            logger.error(f"Símbolo {symbol} no mapeado en CoinGecko.")
            return 0
            
        logger.info(f"Descargando Total Supply (CoinGecko) para {symbol}")
        
        # Calcular los días desde start_date hasta hoy
        days = (datetime.now(timezone.utc) - start_date.replace(tzinfo=timezone.utc)).days
        if days < 1:
            days = 1
            
        if days > 365:
            logger.warning(f"CoinGecko API pública limita a 365 días. Acotando de {days} a 365.")
            days = 365
            
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
        params = {
            "vs_currency": "usd",
            "days": days
        }
        
        try:
            res = requests.get(url, params=params)
            data = res.json()
            market_caps = data.get("market_caps", [])
            
            records = []
            for item in market_caps:
                ts_ms = item[0]
                val = float(item[1])
                ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
                
                if ts >= start_date.replace(tzinfo=timezone.utc):
                    records.append({
                        "metric_name": f"{symbol}_Total_Supply",
                        "symbol": symbol,
                        "timestamp": ts,
                        "value": val,
                        "source": "coingecko"
                    })
                    
            return self._save_records(records)
        except Exception as e:
            logger.error(f"Error fetching Total Supply from CoinGecko: {e}")
            return 0

    def _save_records(self, records: list) -> int:
        if not records:
            return 0
            
        # Agrupar registros con el mismo timestamp para evitar IntegrityError (UNIQUE constraint)
        # ya que Etherscan puede devolver múltiples transferencias en el mismo bloque/segundo.
        grouped_records = {}
        for rec in records:
            key = (rec['metric_name'], rec['symbol'], rec['timestamp'].replace(tzinfo=None))
            if key in grouped_records:
                grouped_records[key]['value'] += rec['value']
            else:
                grouped_records[key] = {
                    "metric_name": rec['metric_name'],
                    "symbol": rec['symbol'],
                    "timestamp": rec['timestamp'].replace(tzinfo=None),
                    "value": rec['value'],
                    "source": rec['source']
                }
            
        saved_count = 0
        for key, rec in grouped_records.items():
            exists = self.db.query(OnChainMetric).filter_by(
                metric_name=rec['metric_name'], 
                symbol=rec['symbol'], 
                timestamp=rec['timestamp']
            ).first()
            
            if not exists:
                metric_obj = OnChainMetric(
                    metric_name=rec['metric_name'],
                    symbol=rec['symbol'],
                    timestamp=rec['timestamp'],
                    value=rec['value'],
                    source=rec['source']
                )
                self.db.add(metric_obj)
                saved_count += 1
                
        try:
            self.db.commit()
            logger.info(f"Guardados {saved_count} nuevos registros on-chain flow.")
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error al guardar en BD: {e}")
            raise e
            
        return saved_count
