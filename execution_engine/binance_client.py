import os
from dotenv import load_dotenv
from binance.client import Client
from binance import ThreadedWebsocketManager

load_dotenv()

class BinanceTestnetClient:
    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY", "")
        self.api_secret = os.getenv("BINANCE_SECRET_KEY", "")
        
        # Testnet URL
        self.client = Client(self.api_key, self.api_secret, testnet=True)
        self.twm = ThreadedWebsocketManager(api_key=self.api_key, api_secret=self.api_secret, testnet=True)
        self.twm.start()

    def get_historical_klines(self, symbol: str, interval: str, lookback_str: str):
        """
        Obtiene velas históricas (necesario para "calentar" indicadores al iniciar el bot).
        """
        # Convertir símbolo estándar a formato Binance (ej. BTC/USDT -> BTCUSDT)
        binance_symbol = symbol.replace("/", "").upper()
        return self.client.get_historical_klines(binance_symbol, interval, lookback_str)

    def subscribe_to_klines(self, symbol: str, interval: str, callback):
        """
        Suscribe a las velas en tiempo real. 
        El callback recibirá diccionarios de la vela recién cerrada.
        """
        binance_symbol = symbol.replace("/", "").upper()
        
        def handle_socket_message(msg):
            # msg['e'] == 'kline'
            # msg['k']['x'] is True if the kline is closed
            if msg.get('e') == 'error':
                print(f"Error en WebSocket: {msg}")
                return

            if msg.get('e') == 'kline':
                k = msg['k']
                if k['x']:  # La vela se ha cerrado
                    # Parsear datos de la vela
                    kline_data = {
                        'timestamp': int(k['t']),
                        'open': float(k['o']),
                        'high': float(k['h']),
                        'low': float(k['l']),
                        'close': float(k['c']),
                        'volume': float(k['v'])
                    }
                    callback(kline_data)

        print(f"Suscrito a WebSockets de Binance Testnet para {binance_symbol} ({interval})")
        stream_name = self.twm.start_kline_socket(callback=handle_socket_message, symbol=binance_symbol, interval=interval)
        return stream_name

    def stop(self):
        print("Cerrando conexiones WebSocket...")
        self.twm.stop()
