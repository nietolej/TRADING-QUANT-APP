"""
Proveedor 100% gratuito y sin API key: balance on-chain real de wallets de Bitcoin
públicamente verificadas, vía blockchain.info (blockchain.com), para aproximar el
"Exchange Reserve" de BTC sin depender de CryptoQuant o Glassnode (ambos de pago para
esta métrica — ver GLASSNODE_METRICS / conversación de soporte del 2026-09-16).

Fuente de verificación de la dirección: como parte de su iniciativa de Proof-of-Reserves
(noviembre 2022), Binance reveló públicamente sus wallets de custodia, incluyendo este
cold wallet de BTC, reportado entre otros por CoinDesk:
"Binance Releases Wallet Addresses of $69B Crypto Reserve" (coindesk.com, 2022-11-10).

LIMITACIÓN CONOCIDA: cubre una sola wallet (el cold wallet de Binance), no un agregado
de todo el mercado ni de todos los exchanges. Al ser un cold wallet, sus movimientos son
infrecuentes y de gran tamaño — sirve como tendencia de reserva, NO como señal de
"netflow" diario (para eso se necesitaría trackear wallets calientes, que no están
públicamente documentadas con la misma confiabilidad).
"""
from datetime import datetime, timezone
from typing import Dict, List

import requests
import pandas as pd

from .base_provider import BaseOnChainProvider


class BlockchainInfoProvider(BaseOnChainProvider):
    SUPPORTED_METRICS = {"exchange_reserve"}

    KNOWN_EXCHANGE_WALLETS: Dict[str, List[str]] = {
        "BTC": ["34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo"],  # Binance cold wallet (verificado, ver docstring)
    }

    def fetch_metric(self, metric_name: str, symbol: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
        if metric_name not in self.SUPPORTED_METRICS:
            print(f"Métrica {metric_name} no soportada por BlockchainInfoProvider.")
            return pd.DataFrame()

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
