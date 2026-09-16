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

        # LIMITACIÓN CONOCIDA: solo 2 wallets calientes de Binance. Exchange_Inflow/Outflow
        # es por tanto una muestra pequeña y sesgada hacia un solo exchange, no un flujo de
        # mercado representativo (Coinbase, Kraken, OKX, etc. no están cubiertos). No se
        # agregan más direcciones aquí sin verificarlas contra una fuente confiable (ej.
        # Etherscan Label Cloud / Arkham) — una dirección incorrecta contaminaría los datos
        # guardados en la BD de forma silenciosa.
        self.EXCHANGE_WALLETS = [
            "0x28C6c06298d514Db089934071355E5743bf21d60", # Binance 14
            "0xF977814e90dA44bFA03b6295A0616a897441aceC"  # Binance 8
        ]

    def _fetch_etherscan_page_window(self, contract_address: str, target_address: str, end_block: int, min_timestamp: float, max_pages: int) -> tuple:
        """
        Trae hasta `max_pages` páginas (offset 1000) dentro de una sola ventana de
        `endblock`. Etherscan rechaza page*offset > 10000, así que con max_pages=10 nunca
        se toca ese límite duro dentro de una ventana; en cambio se detecta si la ventana
        se "llenó" (10 páginas completas) para que el llamador abra una ventana más
        antigua en vez de perder el histórico previo a la página 10.

        Returns: (resultados: list, ventana_llena: bool)
        """
        url = "https://api.etherscan.io/v2/api"
        window_results = []

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
                "endblock": end_block,
                "sort": "desc",
                "apikey": self.etherscan_key
            }
            try:
                res = requests.get(url, params=params, timeout=15)
                data = res.json()
            except Exception as e:
                logger.error(f"Error de red consultando Etherscan: {e}")
                return window_results, False

            status = data.get("status")
            message = str(data.get("message", "")).strip()
            # Etherscan a veces deja el texto descriptivo real en "result" (con "message"
            # en genérico "NOTOK") y a veces en "message" — se revisan ambos campos.
            result_field = data.get("result")
            detail_text = f"{message} {result_field if isinstance(result_field, str) else ''}".lower()

            if status == "1":
                batch = data.get("result", [])
                window_results.extend(batch)

                if len(batch) < 1000:
                    return window_results, False  # No hay más resultados en absoluto

                last_ts = int(batch[-1]['timeStamp'])
                if last_ts < min_timestamp:
                    return window_results, False  # Ya cubrimos el rango de fechas pedido
            elif "no transactions found" in detail_text:
                # Resultado legítimo: sin actividad en esta ventana (no es un error).
                return window_results, False
            else:
                # Error real de la API (key inválida, rate limit, parámetros
                # malformados...): antes se trataba igual que "sin resultados" y la
                # sincronización reportaba éxito con 0 registros, ocultando el fallo real.
                raise RuntimeError(f"Etherscan devolvió un error (status={status}): {message or result_field}")

            time.sleep(0.3)  # Rate limit para APIs gratuitas

        # El for terminó las max_pages sin cortar antes: la ventana se llenó por completo,
        # es probable que haya más historia por debajo del bloque más antiguo obtenido.
        return window_results, True

    def _fetch_etherscan_transfers(self, contract_address: str, target_address: str, min_timestamp: float = 0, max_windows: int = 500) -> list:
        """
        Descarga TODAS las transferencias de un token para una dirección desde el bloque
        más reciente hasta `min_timestamp` (o hasta el origen del contrato si
        min_timestamp=0), sin perder historial: Etherscan limita cada consulta a 10
        páginas de 1000 (10.000 registros) por rango de bloques, así que al llenarse una
        ventana se usa el `blockNumber` de la transferencia más antigua obtenida como
        nuevo `endblock` y se abre una ventana siguiente desde la página 1 — permitiendo
        recorrer todo el histórico en tramos de 10.000 en vez de perder todo lo anterior
        a la página 10 de la primera ventana.
        """
        if not self.etherscan_key:
            raise ValueError("Falta configurar 'ETHERSCAN_API_KEY' en el archivo .env")

        all_results = []
        end_block = 99999999

        for _ in range(max_windows):
            window_results, window_full = self._fetch_etherscan_page_window(
                contract_address, target_address, end_block, min_timestamp, max_pages=10
            )
            all_results.extend(window_results)

            if not window_results or not window_full:
                break

            oldest = min(window_results, key=lambda tx: int(tx['blockNumber']))
            oldest_block = int(oldest['blockNumber'])
            oldest_ts = int(oldest['timeStamp'])
            if oldest_ts < min_timestamp or oldest_block <= 1:
                break
            end_block = oldest_block - 1
        else:
            logger.warning(
                f"Se alcanzó el límite de seguridad de {max_windows} ventanas consultando "
                f"Etherscan para {contract_address}/{target_address}; puede haber histórico "
                f"más antiguo sin descargar."
            )

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
                    # Nombre "desnudo" (sin prefijo de símbolo), consistente con el resto
                    # de proveedores (CryptoQuant/DefiLlama/CoinGecko/Glassnode), que ya
                    # guardan el símbolo aparte en la columna `symbol`.
                    "metric_name": "Mint" if is_mint else "Burn",
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
        Calcula y guarda Inflows (depósitos), Outflows (retiros) y el Netflow diario
        derivado (Inflow - Outflow) de Exchanges, a partir de transferencias on-chain
        reales (no estimadas) vía Etherscan.
        """
        contract = self.TOKENS.get(symbol.upper())
        if not contract:
            return 0

        records = []
        decimals = 6
        daily_inflow: dict = {}
        daily_outflow: dict = {}

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
                    day_key = ts.date()
                    if is_inflow:
                        daily_inflow[day_key] = daily_inflow.get(day_key, 0.0) + val
                    else:
                        daily_outflow[day_key] = daily_outflow.get(day_key, 0.0) + val

                    records.append({
                        "metric_name": "Exchange_Inflow" if is_inflow else "Exchange_Outflow",
                        "symbol": symbol,
                        "timestamp": ts,
                        "value": val,
                        "source": "etherscan"
                    })
                except Exception as e:
                    pass

            time.sleep(0.5) # Rate limit

        # Netflow diario derivado (Inflow - Outflow); positivo = más depósitos que retiros.
        # Se calcula a partir de las mismas transferencias reales ya descargadas, no es un
        # dato inventado ni un proxy de otra fuente.
        for day in sorted(set(daily_inflow) | set(daily_outflow)):
            net = daily_inflow.get(day, 0.0) - daily_outflow.get(day, 0.0)
            records.append({
                "metric_name": "Exchange_Netflow",
                "symbol": symbol,
                "timestamp": datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc),
                "value": net,
                "source": "etherscan"
            })

        return self._save_records(records)

    def fetch_exchange_reserve(self, symbol: str) -> int:
        """
        Descarga el balance REAL actual (no derivado/estimado) de las wallets de exchange
        rastreadas, vía el endpoint gratuito `tokenbalance` de Etherscan — es el saldo
        on-chain real de esas direcciones en este momento, no una aproximación.
        """
        contract = self.TOKENS.get(symbol.upper())
        if not contract:
            return 0
        if not self.etherscan_key:
            raise ValueError("Falta configurar 'ETHERSCAN_API_KEY' en el archivo .env")

        decimals = 6
        total_balance = 0.0
        url = "https://api.etherscan.io/v2/api"

        for wallet in self.EXCHANGE_WALLETS:
            params = {
                "chainid": 1,
                "module": "account",
                "action": "tokenbalance",
                "contractaddress": contract,
                "address": wallet,
                "tag": "latest",
                "apikey": self.etherscan_key
            }
            try:
                res = requests.get(url, params=params, timeout=15)
                data = res.json()
                if data.get("status") == "1":
                    total_balance += float(data.get("result", 0)) / (10 ** decimals)
                else:
                    logger.warning(f"Etherscan tokenbalance falló para {wallet}: {data.get('message')}")
            except Exception as e:
                logger.warning(f"Error consultando balance de {wallet}: {e}")
            time.sleep(0.25)

        record = {
            "metric_name": "Exchange_Reserve",
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc),
            "value": total_balance,
            "source": "etherscan"
        }
        return self._save_records([record])

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
            res = requests.get(url, params=params, timeout=15)
            data = res.json()
            market_caps = data.get("market_caps", [])
            
            records = []
            for item in market_caps:
                ts_ms = item[0]
                val = float(item[1])
                ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
                
                if ts >= start_date.replace(tzinfo=timezone.utc):
                    records.append({
                        "metric_name": "Total_Supply",
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
            
        # Una sola consulta de existencia por cada (metric_name, symbol) presente en el
        # batch, en vez de un SELECT por registro (antes: hasta cientos de idas y vueltas
        # a la BD por sincronización, una por cada transferencia de Etherscan).
        by_metric_symbol: dict = {}
        for (metric_name, symbol, ts), rec in grouped_records.items():
            by_metric_symbol.setdefault((metric_name, symbol), []).append(ts)

        existing_keys = set()
        for (metric_name, symbol), timestamps in by_metric_symbol.items():
            rows = self.db.query(OnChainMetric.timestamp).filter(
                OnChainMetric.metric_name == metric_name,
                OnChainMetric.symbol == symbol,
                OnChainMetric.timestamp.in_(timestamps)
            ).all()
            for (ts,) in rows:
                existing_keys.add((metric_name, symbol, ts))

        new_objects = [
            OnChainMetric(
                metric_name=rec['metric_name'],
                symbol=rec['symbol'],
                timestamp=rec['timestamp'],
                value=rec['value'],
                source=rec['source']
            )
            for key, rec in grouped_records.items() if key not in existing_keys
        ]

        if not new_objects:
            return 0

        try:
            self.db.bulk_save_objects(new_objects)
            self.db.commit()
            logger.info(f"Guardados {len(new_objects)} nuevos registros on-chain flow.")
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error al guardar en BD: {e}")
            raise e

        return len(new_objects)
