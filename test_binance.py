import traceback
from execution_engine.binance_client import BinanceTestnetClient
try:
    c = BinanceTestnetClient()
    print("SUCCESS")
    print(c.get_historical_klines("BTC/USDT", "1h", "3 candles ago UTC"))
except Exception as e:
    print("ERROR:")
    traceback.print_exc()
