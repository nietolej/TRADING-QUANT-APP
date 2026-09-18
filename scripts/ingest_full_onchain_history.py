import sys
import os
from datetime import datetime, timezone

# Asegurar que el directorio raíz está en el path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_layer.onchain_data import OnChainDataManager
from data_layer.onchain_flows import BlockExplorerClient
from data_layer.market_data import MarketDataManager
from web_gui.pages.onchain_analyzer_page import (
    fetch_data_async,
    CRYPTOQUANT_METRICS,
)

# Suficientemente antiguo para cubrir el histórico completo de cualquier fuente del
# módulo (BTC genesis 2009, listados de exchanges, APIs on-chain, etc.). No hace falta
# afinarlo: cada fetch ya es incremental (retoma desde el último registro guardado en
# BD, ver OnChainDataManager.update_historical_data / BlockExplorerClient._effective_start),
# así que pedir de más aquí solo importa la primera vez que se corre para cada métrica.
GENESIS_START = datetime(2009, 1, 3, tzinfo=timezone.utc)
GENESIS_DAYS = (datetime.now(timezone.utc) - GENESIS_START).days


def ingest_defillama_coingecko():
    """Métricas agregadas de mercado/stablecoins que no dependen de un símbolo de la UI."""
    manager = OnChainDataManager()
    jobs = [
        ("stablecoin_market_cap", "GLOBAL", "defillama"),
        ("usdt_market_cap", "USDT", "defillama"),
        ("usdc_market_cap", "USDC", "defillama"),
        ("btc_market_cap", "BTC", "coingecko"),
        ("btc_volume", "BTC", "coingecko"),
    ]
    for metric, symbol, provider in jobs:
        print(f"\n--- {symbol} / {metric} (vía {provider}) ---")
        try:
            manager.update_historical_data(
                metric_name=metric,
                symbol=symbol,
                start_date=GENESIS_START,
                provider_name=provider,
            )
        except Exception as e:
            print(f"Error procesando {metric}: {e}")


def ingest_btc_eth_metrics():
    """Métricas por símbolo (CryptoQuant/Glassnode/Binance público/blockchain.info),
    usando el mismo enrutamiento de proveedor que ya usa la UI de Análisis On-Chain."""
    for symbol, metrics in (("BTC", CRYPTOQUANT_METRICS + ["Total_Supply"]), ("ETH", CRYPTOQUANT_METRICS)):
        for metric in metrics:
            print(f"\n--- {symbol} / {metric} ---")
            try:
                saved = fetch_data_async(symbol, GENESIS_DAYS, metric)
                print(f"Guardados {saved} registros nuevos.")
            except Exception as e:
                print(f"Error en {symbol}/{metric}: {e}")


def ingest_stablecoin_flows():
    """Mint/Burn, Exchange In/Out/Netflow, Exchange Reserve y Total Supply de USDT/USDC.

    Se llama una vez por método (no por métrica) porque cada método de
    BlockExplorerClient ya cubre varias métricas de una sola descarga a Etherscan/
    DefiLlama (ej. fetch_stablecoin_supply calcula Mint Y Burn juntos); repetir la
    llamada por cada métrica derivada re-descargaría el mismo histórico de Etherscan
    varias veces sin necesidad.
    """
    client = BlockExplorerClient()
    for symbol in ("USDT", "USDC"):
        print(f"\n--- {symbol} / Mint+Burn ---")
        try:
            print(f"Guardados {client.fetch_stablecoin_supply(symbol, GENESIS_START)} registros nuevos.")
        except Exception as e:
            print(f"Error en {symbol}/Mint+Burn: {e}")

        print(f"\n--- {symbol} / Exchange In/Out/Netflow ---")
        try:
            print(f"Guardados {client.fetch_exchange_flows(symbol, GENESIS_START)} registros nuevos.")
        except Exception as e:
            print(f"Error en {symbol}/Exchange flows: {e}")

        print(f"\n--- {symbol} / Exchange Reserve ---")
        try:
            print(f"Guardados {client.fetch_exchange_reserve(symbol)} registros nuevos.")
        except Exception as e:
            print(f"Error en {symbol}/Exchange Reserve: {e}")

        print(f"\n--- {symbol} / Total Supply ---")
        try:
            print(f"Guardados {client.fetch_total_supply(symbol, GENESIS_START)} registros nuevos.")
        except Exception as e:
            print(f"Error en {symbol}/Total Supply: {e}")


def ingest_reference_prices():
    """Precios OHLCV diarios usados como referencia en los gráficos on-chain."""
    market_mgr = MarketDataManager()
    for pair in ("BTC/USDT", "ETH/USDT"):
        print(f"\n--- Precio de referencia {pair} (1d) ---")
        try:
            market_mgr.update_historical_data(pair, "1d", GENESIS_START)
        except Exception as e:
            print(f"Error descargando precio {pair}: {e}")


def main():
    print("Iniciando ingesta histórica COMPLETA del módulo On-Chain (desde el dato más antiguo disponible)...")
    print("Nota: cada descarga es incremental, así que volver a correr este script solo trae lo que falte.")

    ingest_defillama_coingecko()
    ingest_btc_eth_metrics()
    ingest_stablecoin_flows()
    ingest_reference_prices()

    print("\n¡Ingesta histórica completa finalizada!")


if __name__ == "__main__":
    main()
