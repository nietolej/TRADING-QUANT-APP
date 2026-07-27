import sys
import os
from datetime import datetime, timezone

# Asegurar que el directorio raíz está en el path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_layer.onchain_data import OnChainDataManager

def main():
    print("Iniciando ingesta masiva de datos On-Chain gratuitos...")
    manager = OnChainDataManager()

    # Descargamos histórico desde 2021 (inicio del último ciclo alcista grande)
    start_date = datetime(2021, 1, 1, tzinfo=timezone.utc)
    
    # Lista de trabajos a ejecutar
    # Tupla: (Métrica, Símbolo/Activo, Proveedor)
    jobs = [
        ("stablecoin_market_cap", "GLOBAL", "defillama"),
        ("usdt_market_cap", "USDT", "defillama"),
        ("usdc_market_cap", "USDC", "defillama"),
        ("btc_market_cap", "BTC", "coingecko"),
        ("btc_volume", "BTC", "coingecko")
    ]

    for metric, symbol, provider in jobs:
        print(f"\n--- Descargando {metric} vía {provider} ---")
        try:
            manager.update_historical_data(
                metric_name=metric,
                symbol=symbol,
                start_date=start_date,
                provider_name=provider
            )
        except Exception as e:
            print(f"Error procesando {metric}: {e}")

    print("\n¡Ingesta de datos finalizada!")

if __name__ == "__main__":
    main()
