import ccxt
import pandas as pd
from datetime import datetime, timezone
import time
from sqlalchemy import func
from sqlalchemy.orm import Session
from .storage import OHLCV, SessionLocal

import yfinance as yf

_working_exchange_class = None

def get_binance_exchange(config=None):
    global _working_exchange_class
    if config is None:
        config = {'enableRateLimit': True}
    if _working_exchange_class is not None:
        return _working_exchange_class(config)
    # Try global Binance first
    try:
        exchange = ccxt.binance(config)
        exchange.load_markets()
        _working_exchange_class = ccxt.binance
        return exchange
    except Exception as e:
        print(f"Failed to load global Binance: {e}. Trying Binance US...")
    # Try Binance US fallback
    try:
        exchange = ccxt.binanceus(config)
        exchange.load_markets()
        _working_exchange_class = ccxt.binanceus
        return exchange
    except Exception as e:
        print(f"Failed to load Binance US: {e}")
    # Default fallback
    return ccxt.binance(config)

def ensure_utc(dt: datetime) -> datetime:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

class MarketDataManager:
    def __init__(self, db_session: Session = None):
        self.exchange = get_binance_exchange({
            'enableRateLimit': True,
        })
        self.db = db_session or SessionLocal()
        
    def fetch_ohlcv(self, symbol: str, timeframe: str, since: datetime = None, limit: int = 1000) -> pd.DataFrame:
        """
        Descarga datos OHLCV de Binance.
        """
        since = ensure_utc(since)
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
        start_date = ensure_utc(start_date)
        end_date = ensure_utc(end_date)
        
        # Para evitar re-descargar todo, buscamos el rango de fechas existente en la base de datos
        db_range = self.db.query(
            func.min(OHLCV.timestamp).label('min_ts'),
            func.max(OHLCV.timestamp).label('max_ts')
        ).filter(
            OHLCV.symbol == symbol,
            OHLCV.timeframe == timeframe
        ).first()
        
        if db_range and db_range.min_ts is not None:
            min_db = ensure_utc(db_range.min_ts)
            max_db = ensure_utc(db_range.max_ts)
            
            # Si la fecha de inicio solicitada es anterior a la que tenemos en la BD,
            # debemos descargar desde la fecha solicitada para rellenar el vacío (gap) del pasado.
            if start_date < min_db:
                since_dt = start_date
            else:
                # Si ya cubre la fecha solicitada, resumimos desde el último registro guardado
                since_dt = max_db
        else:
            since_dt = start_date
        
        if end_date and since_dt >= end_date:
            msg = f"{symbol} {timeframe} is already updated up to {end_date}."
            print(msg)
            if progress_callback: progress_callback(msg)
            return
            
        msg = f"Updating {symbol} {timeframe} starting from {since_dt} (Source: {source})"
        print(msg)
        if progress_callback: progress_callback(msg)
        
        if source == "yahoo":
            _end_dt = end_date if end_date else ensure_utc(datetime.now(timezone.utc))
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
            
            since_dt = ensure_utc(df['timestamp'].iloc[-1])
            msg2 = f"Downloaded {len(df)} candles for {symbol}. Next fetch from {since_dt}"
            print(msg2)
            if progress_callback: progress_callback(msg2)
            
            if end_date and since_dt >= end_date:
                break
                
            time.sleep(self.exchange.rateLimit / 1000) # Respetar rate limits
            
    def _save_df_to_db(self, df):
        records = df.to_dict(orient='records')
        for rec in records:
            # Para la comparación con la base de datos (SQLite), convertimos timestamp a naive
            ts_naive = ensure_utc(rec['timestamp']).replace(tzinfo=None)
            
            exists = self.db.query(OHLCV).filter_by(
                symbol=rec['symbol'], 
                timeframe=rec['timeframe'], 
                timestamp=ts_naive
            ).first()
            if not exists:
                ohlcv_obj = OHLCV(
                    symbol=rec['symbol'],
                    timeframe=rec['timeframe'],
                    timestamp=ts_naive,
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
        start_naive = ensure_utc(start_date).replace(tzinfo=None)
        query = self.db.query(OHLCV).filter(
            OHLCV.symbol == symbol,
            OHLCV.timeframe == timeframe,
            OHLCV.timestamp >= start_naive
        )
        if end_date:
            end_naive = ensure_utc(end_date).replace(tzinfo=None)
            query = query.filter(OHLCV.timestamp <= end_naive)
            
        query = query.order_by(OHLCV.timestamp.asc())
        
        df = pd.read_sql(query.statement, self.db.bind)
        if not df.empty:
            df.set_index('timestamp', inplace=True)
            if df.index.tz is None:
                df.index = pd.to_datetime(df.index).tz_localize('UTC')
            else:
                df.index = pd.to_datetime(df.index).tz_convert('UTC')
            
        return df
