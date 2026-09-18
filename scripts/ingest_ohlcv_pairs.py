import sys
import os
from datetime import datetime, timezone

# Asegurar que el directorio raíz está en el path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_layer.market_data import MarketDataManager

TIMEFRAME = "1d"

# Fecha de inicio "de sobra": ninguno de estos pares tiene historia real anterior a esto
# (Binance abrió en 2017, SOL listó en 2020), así que update_historical_data simplemente
# trae todo lo que el exchange tenga y arranca desde ahí. No hace falta afinarlo por par:
# la lógica incremental (ver MarketDataManager.update_historical_data) ya resume desde el
# último candle guardado en corridas siguientes, así que pedir de más aquí solo importa la
# primera vez.
GENESIS_START = datetime(2010, 1, 1, tzinfo=timezone.utc)

# Pares con spot directo en Binance (vía CCXT) - la ruta normal de MarketDataManager.
BINANCE_PAIRS = [
    "BTC/USDT",
    "ETH/BTC",
    "ADA/BTC",
    "SOL/BTC",
    "ETH/USDT",
    "SOL/USDT",
]

# BTC/USD no cotiza en Binance (solo USDT); se completa en dos tramos igual que
# data_layer/halving_analyzer.py: Bitstamp (2011-2014, la fuente más antigua disponible
# para este par) + Yahoo Finance (2014-presente). Ambos tramos se guardan bajo el mismo
# símbolo 'BTC-USD' en la tabla OHLCV para que quede unificado y la sincronización
# incremental de Yahoo (más abajo) sepa desde dónde continuar.
BTC_USD_SYMBOL = "BTC-USD"
BITSTAMP_CUTOFF = datetime(2014, 10, 1, tzinfo=timezone.utc)


def ingest_bitstamp_btc_usd_early_history(mgr: MarketDataManager):
    """Rellena 2011-08 a 2014-10 de BTC/USD vía Bitstamp (Binance/Yahoo no cubren ese
    tramo). Solo hace falta una vez: si ya hay datos de ese rango en la BD, no repite
    la descarga."""
    from sqlalchemy import func
    from data_layer.storage import OHLCV

    existing_min = mgr.db.query(func.min(OHLCV.timestamp)).filter(
        OHLCV.symbol == BTC_USD_SYMBOL, OHLCV.timeframe == TIMEFRAME
    ).scalar()
    if existing_min and existing_min.replace(tzinfo=timezone.utc) <= BITSTAMP_CUTOFF:
        print("Histórico temprano de BTC/USD (Bitstamp 2011-2014) ya está en la BD, se omite.")
        return

    print("Descargando histórico temprano de BTC/USD (Bitstamp, 2011-2014)...")
    try:
        import ccxt
        import pandas as pd

        bitstamp = ccxt.bitstamp({"enableRateLimit": True})
        since_ms = bitstamp.parse8601("2011-08-01T00:00:00Z")
        end_cutoff_ms = bitstamp.parse8601(BITSTAMP_CUTOFF.strftime("%Y-%m-%dT%H:%M:%SZ"))

        candles = []
        current_since = since_ms
        while current_since < end_cutoff_ms:
            chunk = bitstamp.fetch_ohlcv("BTC/USD", "1d", since=current_since, limit=1000)
            if not chunk:
                break
            candles.extend(chunk)
            last_ts = chunk[-1][0]
            if last_ts <= current_since:
                break
            current_since = last_ts + 86400000
            if len(chunk) < 500:
                break

        if not candles:
            print("Bitstamp no devolvió velas tempranas de BTC/USD.")
            return

        df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df[df["timestamp"] < BITSTAMP_CUTOFF]
        df["symbol"] = BTC_USD_SYMBOL
        df["timeframe"] = TIMEFRAME
        mgr._save_df_to_db(df)
        print(f"Guardadas {len(df)} velas tempranas de BTC/USD (Bitstamp).")
    except Exception as e:
        print(f"Error descargando histórico temprano de BTC/USD vía Bitstamp: {e}")


def ingest_btc_usd(mgr: MarketDataManager):
    ingest_bitstamp_btc_usd_early_history(mgr)
    print(f"\n--- {BTC_USD_SYMBOL} / {TIMEFRAME} (vía Yahoo Finance) ---")
    try:
        mgr.update_historical_data(
            symbol=BTC_USD_SYMBOL,
            timeframe=TIMEFRAME,
            start_date=BITSTAMP_CUTOFF,
            source="yahoo",
        )
    except Exception as e:
        print(f"Error descargando {BTC_USD_SYMBOL}: {e}")


def main():
    print("Iniciando descarga histórica de velas diarias (1d) para los pares solicitados...")
    print("Nota: la sincronización es incremental, así que volver a correr este script solo trae lo que falte.")

    mgr = MarketDataManager()

    ingest_btc_usd(mgr)

    for pair in BINANCE_PAIRS:
        print(f"\n--- {pair} / {TIMEFRAME} ---")
        try:
            mgr.update_historical_data(symbol=pair, timeframe=TIMEFRAME, start_date=GENESIS_START)
        except Exception as e:
            print(f"Error descargando {pair}: {e}")

    print("\n¡Descarga histórica de pares finalizada!")


if __name__ == "__main__":
    main()
