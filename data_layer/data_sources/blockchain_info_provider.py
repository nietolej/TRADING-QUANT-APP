"""
Proveedor 100% gratuito y sin API key: balance on-chain real de wallets de Bitcoin
públicamente verificadas, vía blockchain.info (blockchain.com), para aproximar el
"Exchange Reserve" de BTC sin depender de CryptoQuant o Glassnode (ambos de pago para
esta métrica — ver GLASSNODE_METRICS / conversación de soporte del 2026-09-16).

Fuente de verificación de la dirección: como parte de su iniciativa de Proof-of-Reserves
(noviembre 2022), Binance reveló públicamente sus wallets de custodia, incluyendo este
cold wallet de BTC, reportado entre otros por CoinDesk:
"Binance Releases Wallet Addresses of $69B Crypto Reserve" (coindesk.com, 2022-11-10).

LIMITACIÓN CONOCIDA (exchange_reserve): cubre una sola wallet (el cold wallet de
Binance), no un agregado de todo el mercado ni de todos los exchanges. Al ser un cold
wallet, sus movimientos son infrecuentes y de gran tamaño — sirve como tendencia de
reserva, NO como señal de "netflow" diario (para eso se necesitaría trackear wallets
calientes, que no están públicamente documentadas con la misma confiabilidad).

total_supply (BTC): usa el endpoint público de Charts de blockchain.info
(`/charts/total-bitcoins`), que reporta la oferta circulante real de BTC calculada a
partir del calendario de emisión de la cadena (no un proxy de market cap ni datos de un
tercero) — histórico completo desde el bloque génesis (2009-01-03), gratis, sin API key.
Reemplaza al proveedor anterior (CoinGecko `market_chart`), que topaba el histórico a 365
días por su tier gratuito — ver auditoría 2026-09-17.
"""
from datetime import datetime, timezone
from typing import Dict, List

import requests
import pandas as pd

from .base_provider import BaseOnChainProvider


class BlockchainInfoProvider(BaseOnChainProvider):
    SUPPORTED_METRICS = {"exchange_reserve", "total_supply"}

    KNOWN_EXCHANGE_WALLETS: Dict[str, List[str]] = {
        "BTC": ["34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo"],  # Binance cold wallet (verificado, ver docstring)
    }

    def fetch_metric(self, metric_name: str, symbol: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
        if metric_name not in self.SUPPORTED_METRICS:
            print(f"Métrica {metric_name} no soportada por BlockchainInfoProvider.")
            return pd.DataFrame()

        if metric_name == "total_supply":
            return self._fetch_total_supply(symbol, start_date, end_date)

        asset = symbol.split('/')[0].upper() if '/' in symbol else symbol.upper()
        wallets = self.KNOWN_EXCHANGE_WALLETS.get(asset)
        if not wallets:
            return pd.DataFrame()

        total_satoshis = 0
        for addr in wallets:
            try:
                res = requests.get(f"https://blockchain.info/q/addressbalance/{addr}", timeout=15)
                res.raise_for_status()
                total_satoshis += int(res.text.strip())
            except Exception as e:
                print(f"Error consultando balance de {addr} en blockchain.info: {e}")

        if total_satoshis <= 0:
            return pd.DataFrame()

        return pd.DataFrame([{
            'timestamp': datetime.now(timezone.utc),
            'metric_name': metric_name,
            'symbol': symbol,
            'value': total_satoshis / 1e8,
            'source': 'blockchain_info'
        }])

    def _fetch_total_supply(self, symbol: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
        asset = symbol.split('/')[0].upper() if '/' in symbol else symbol.upper()
        if asset != "BTC":
            print(f"total_supply de BlockchainInfoProvider solo cubre BTC, no {asset}.")
            return pd.DataFrame()

        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        end_limit = end_date or datetime.now(timezone.utc)
        if end_limit.tzinfo is None:
            end_limit = end_limit.replace(tzinfo=timezone.utc)

        try:
            res = requests.get(
                "https://api.blockchain.info/charts/total-bitcoins",
                params={"timespan": "all", "format": "json"},
                timeout=20,
            )
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            print(f"Error consultando total-bitcoins en blockchain.info: {e}")
            return pd.DataFrame()

        records = []
        for point in data.get("values", []):
            ts = datetime.fromtimestamp(point["x"], tz=timezone.utc)
            if start_date <= ts <= end_limit:
                records.append({
                    'timestamp': ts,
                    'metric_name': 'total_supply',
                    'symbol': symbol,
                    'value': float(point["y"]),
                    'source': 'blockchain_info'
                })

        return pd.DataFrame(records)
