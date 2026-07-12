import ccxt
import pandas as pd
from datetime import datetime, timezone
import time
from sqlalchemy.orm import Session
from .storage import OHLCV, SessionLocal

import yfinance as yf

class MarketDataManager:
    def __init__(self, db_session: Session = None):
        # Usamos binanceus por defecto para evitar errores 451 de restricción geográfica
        # si estás en un país restringido por binance.com
        self.exchange = ccxt.binanceus({
            'enableRateLimit': True,
        })
        self.db = db_session or SessionLocal()
        
    def fetch_ohlcv(self, symbol: str, timeframe: str, since: datetime = None, limit: int = 1000) -> pd.DataFrame:
        """
        Descarga datos OHLCV de Binance.
        """
        since_ms = int(since.timestamp() * 1000) if since else None
        
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=limit)
            
            if not ohlcv:
                return pd.DataFrame()
                
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
            df['symbol'] = symbol
            df['timeframe'] = timeframe
            
            return df
        except Exception as e:
            print(f"Error fetching data for {symbol} {timeframe}: {e}")
            return pd.DataFrame()

    def fetch_ohlcv_yahoo(self, symbol: str, timeframe: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
        """
        Descarga datos OHLCV de Yahoo Finance.
        """
        # Mapeo de timeframes de Binance a Yahoo Finance
        tf_map = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "4h": "1h", # yf no tiene 4h directo en history, pero podemos resamplear o usar 1h
            "1d": "1d",
            "1wk": "1wk",
            "1mo": "1mo"
        }
        yf_tf = tf_map.get(timeframe, "1d")
        
        try:
            # yfinance expects date strings or datetime
            ticker = yf.Ticker(symbol)
            # Fetch data
            df = ticker.history(start=start_date, end=end_date, interval=yf_tf)
            
            if df is None or df.empty:
                return pd.DataFrame()
                
            df = df.reset_index()
            
            # Renombrar columnas
            if 'Date' in df.columns:
                df = df.rename(columns={'Date': 'timestamp'})
            elif 'Datetime' in df.columns:
                df = df.rename(columns={'Datetime': 'timestamp'})
                
            df = df.rename(columns={
                'Open': 'open',
                'High': 'high',
                'Low': 'low',
                'Close': 'close',
                'Volume': 'volume'
            })
            
            # Ajustar zona horaria a UTC
            if df['timestamp'].dt.tz is None:
                df['timestamp'] = df['timestamp'].dt.tz_localize('UTC')
            else:
                df['timestamp'] = df['timestamp'].dt.tz_convert('UTC')
                
            df['symbol'] = symbol
            df['timeframe'] = timeframe
            
            return df[['timestamp', 'open', 'high', 'low', 'close', 'volume', 'symbol', 'timeframe']]
        except Exception as e:
            print(f"Error fetching Yahoo Finance data: {e}")
            return pd.DataFrame()

    def update_historical_data(self, symbol: str, timeframe: str, start_date: datetime, end_date: datetime = None, progress_callback=None, source="binance"):
        """
        Lógica de descarga incremental iterando desde start_date hasta end_date.
        """
        # Para evitar re-descargar todo, buscamos la última fecha guardada después de start_date
        last_record = self.db.query(OHLCV).filter(
            OHLCV.symbol == symbol,
            OHLCV.timeframe == timeframe,
            OHLCV.timestamp >= start_date
        ).order_by(OHLCV.timestamp.desc()).first()
        
        since_dt = last_record.timestamp if last_record else start_date
        
        if end_date and since_dt >= end_date:
            msg = f"{symbol} {timeframe} is already updated up to {end_date}."
            print(msg)
            if progress_callback: progress_callback(msg)
            return
            
        msg = f"Updating {symbol} {timeframe} starting from {since_dt} (Source: {source})"
        print(msg)
        if progress_callback: progress_callback(msg)
        
        if source == "yahoo":
            _end_dt = end_date if end_date else datetime.now(timezone.utc)
            df = self.fetch_ohlcv_yahoo(symbol, timeframe, start_date=since_dt, end_date=_end_dt)
            if df.empty:
                msg2 = f"No data found in Yahoo Finance for {symbol}."
                print(msg2)
                if progress_callback: progress_callback(msg2)
                return
                
            self._save_df_to_db(df)
            msg2 = f"Downloaded {len(df)} candles from Yahoo Finance for {symbol}."
            print(msg2)
            if progress_callback: progress_callback(msg2)
            return
        
        # Binance source logic
        while True:
            df = self.fetch_ohlcv(symbol, timeframe, since=since_dt)
            if df.empty or len(df) <= 1: 
                break
                
            # Filter out records beyond end_date
            if end_date:
                df = df[df['timestamp'] <= end_date]
                if df.empty:
                    break
                    
            self._save_df_to_db(df)
            
            since_dt = df['timestamp'].iloc[-1]
            msg2 = f"Downloaded {len(df)} candles for {symbol}. Next fetch from {since_dt}"
            print(msg2)
            if progress_callback: progress_callback(msg2)
            
            if end_date and since_dt >= end_date:
                break
                
            time.sleep(self.exchange.rateLimit / 1000) # Respetar rate limits
            
    def _save_df_to_db(self, df):
        records = df.to_dict(orient='records')
        for rec in records:
            exists = self.db.query(OHLCV).filter_by(
                symbol=rec['symbol'], 
                timeframe=rec['timeframe'], 
                timestamp=rec['timestamp']
            ).first()
            if not exists:
                ohlcv_obj = OHLCV(
                    symbol=rec['symbol'],
                    timeframe=rec['timeframe'],
                    timestamp=rec['timestamp'],
                    open=rec['open'],
                    high=rec['high'],
                    low=rec['low'],
                    close=rec['close'],
                    volume=rec['volume']
                )
                self.db.add(ohlcv_obj)
        self.db.commit()
            
    def get_data(self, symbol: str, timeframe: str, start_date: datetime, end_date: datetime = None) -> pd.DataFrame:
        """
        Obtiene datos históricos desde la base de datos local.
        """
        query = self.db.query(OHLCV).filter(
            OHLCV.symbol == symbol,
            OHLCV.timeframe == timeframe,
            OHLCV.timestamp >= start_date
        )
        if end_date:
            query = query.filter(OHLCV.timestamp <= end_date)
            
        query = query.order_by(OHLCV.timestamp.asc())
        
        df = pd.read_sql(query.statement, self.db.bind)
        if not df.empty:
            df.set_index('timestamp', inplace=True)
            
        return df
