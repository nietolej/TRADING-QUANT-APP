"""
Proveedor 100% gratuito y sin API key para métricas de derivados de Binance Futures.

Envuelve los mismos endpoints públicos que ya usa `binance_derivatives_provider.py` para
el módulo de Derivados, y los expone con la interfaz `BaseOnChainProvider` para que
`OnChainDataManager` pueda usarlos como alternativa sin costo a CryptoQuant para las
métricas que Binance sí publica gratis: funding rate, open interest y ratio taker
buy/sell. El resto de métricas "on-chain" (MVRV, SOPR, Puell Multiple, exchange
netflow/reserve, miner flows, active addresses, NUPL, stock-to-flow) son cálculos
propietarios de CryptoQuant/Glassnode y no tienen equivalente público gratuito real.
"""
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from .base_provider import BaseOnChainProvider
from .binance_derivatives_provider import BinanceDerivativesProvider


class BinancePublicOnChainProvider(BaseOnChainProvider):
    """
    Nota de Binance (no de esta app): los endpoints `/futures/data/*` (open interest,
    ratios long/short/taker) solo permiten consultar los últimos 30 días de historial;
    pedir un rango más antiguo simplemente no devuelve esos días. El funding rate sí
    tiene histórico completo desde el listado del contrato.
    """

    SUPPORTED_METRICS = {"funding_rates", "open_interest", "taker_buy_sell_ratio"}

    def __init__(self):
        super().__init__()
        self.client = BinanceDerivativesProvider()

    def fetch_metric(self, metric_name: str, symbol: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
        if metric_name not in self.SUPPORTED_METRICS:
            print(f"Métrica {metric_name} no soportada por BinancePublicOnChainProvider.")
            return pd.DataFrame()

        binance_symbol = symbol.replace('/', '').upper()
        if not binance_symbol.endswith('USDT'):
            binance_symbol += 'USDT'

        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        end_limit = end_date or datetime.now(timezone.utc)
        if end_limit.tzinfo is None:
            end_limit = end_limit.replace(tzinfo=timezone.utc)

        start_ms = int(start_date.timestamp() * 1000)
        end_ms = int(end_limit.timestamp() * 1000)

        records = []
        try:
            if metric_name == "funding_rates":
                # get_funding_rate_history trata su `limit` como tope TOTAL de resultados
                # (no como tamaño de página), así que una sola llamada con limit=1000 se
                # detenía para siempre tras el primer bloque de ~333 días desde start_date,
                # sin importar cuánta historia más quedara hasta end_date. Para cubrir todo
                # el rango pedido hay que seguir llamando en bloques de 1000, avanzando
                # cur_start tras cada bloque, hasta agotar los datos o llegar a end_ms.
                cur_start = start_ms
                while True:
                    batch = self.client.get_funding_rate_history(binance_symbol, cur_start, end_ms, limit=1000)
                    if not batch:
                        break
                    for item in batch:
                        ts = datetime.fromtimestamp(item['funding_time'] / 1000.0, tz=timezone.utc)
                        records.append({
                            'timestamp': ts, 'metric_name': metric_name, 'symbol': symbol,
                            'value': item['funding_rate_pct'], 'source': 'binance_public'
                        })
                    if len(batch) < 1000:
                        break
                    cur_start = batch[-1]['funding_time'] + 1
                    if end_ms and cur_start >= end_ms:
                        break
            elif metric_name == "open_interest":
                for item in self.client.get_open_interest_history(
                    binance_symbol, period="1d", start_time_ms=start_ms, end_time_ms=end_ms, limit=500
                ):
                    ts = datetime.fromtimestamp(item['timestamp'] / 1000.0, tz=timezone.utc)
                    records.append({
                        'timestamp': ts, 'metric_name': metric_name, 'symbol': symbol,
                        'value': item['sum_open_interest_usd'], 'source': 'binance_public'
                    })
            elif metric_name == "taker_buy_sell_ratio":
                for item in self.client.get_taker_long_short_ratio(
                    binance_symbol, period="1d", start_time_ms=start_ms, end_time_ms=end_ms, limit=500
                ):
                    ts = datetime.fromtimestamp(item['timestamp'] / 1000.0, tz=timezone.utc)
                    records.append({
                        'timestamp': ts, 'metric_name': metric_name, 'symbol': symbol,
                        'value': item['buy_sell_ratio'], 'source': 'binance_public'
                    })
        except Exception as e:
            print(f"Error fetching Binance public data para {metric_name}: {e}")
            return pd.DataFrame()

        return pd.DataFrame(records)
