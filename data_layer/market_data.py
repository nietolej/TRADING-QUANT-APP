import ccxt
import pandas as pd
from datetime import datetime, timezone
import time
from sqlalchemy import func
from sqlalchemy.orm import Session
from .storage import OHLCV, SessionLocal

import yfinance as yf

_working_exchange_factory = None

# Espejo oficial de Binance (global) solo para datos públicos de mercado. Sirve los mismos
# klines que api.binance.com y no está sujeto al bloqueo geográfico (HTTP 451) de este.
BINANCE_PUBLIC_DATA_URL = "https://data-api.binance.vision/api/v3"


def _binance_global(config):
    return ccxt.binance(config)


def _binance_global_mirror(config):
    cfg = dict(config)
    # El espejo solo expone el mercado spot: sin esto load_markets() también consulta los
    # endpoints de futuros (fapi/dapi), que siguen bloqueados.
    cfg['options'] = {**cfg.get('options', {}), 'fetchMarkets': ['spot']}
    exchange = ccxt.binance(cfg)
    exchange.urls['api']['public'] = BINANCE_PUBLIC_DATA_URL
    return exchange


def get_binance_exchange(config=None):
    """
    Exchange ccxt para datos PÚBLICOS de mercado (klines, símbolos). Orden de preferencia:
    1. Binance global.
    2. Espejo oficial de Binance global (data-api.binance.vision) si el anterior está
       bloqueado por región.
    3. Binance US, solo como último recurso: es otro exchange con precios parecidos pero
       volumen ~1000x menor. Antes se usaba en silencio apenas fallaba Binance global, y
       desde 2023 la serie BTC/USDT de la BD mezclaba ambos mercados.
    """
    global _working_exchange_factory

    if config is None:
        config = {'enableRateLimit': True}

    # Public market data does not require API keys, and testnet keys break mainnet endpoints.

    if _working_exchange_factory is not None:
        return _working_exchange_factory(config)

    for factory, label in (
        (_binance_global, "Binance global"),
        (_binance_global_mirror, f"Binance global (espejo {BINANCE_PUBLIC_DATA_URL})"),
        (lambda c: ccxt.binanceus(c), "Binance US"),
    ):
        try:
            exchange = factory(config)
            exchange.load_markets()
            _working_exchange_factory = factory
            if label == "Binance US":
                print("ADVERTENCIA: usando Binance US como fuente de datos. Es un mercado distinto "
                      "(volumen muy bajo); los datos NO son de Binance global.")
            else:
                print(f"Fuente de datos de mercado: {label}")
            return exchange
        except Exception as e:
            print(f"No se pudo usar {label}: {e}")

    # Default fallback
    return ccxt.binance(config)

def ensure_utc(dt: datetime) -> datetime:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def normalize_timeframe(tf: str) -> str:
    """
    Normaliza el timeframe convirtiendo traducciones del navegador (ej: '4 horas', '1 día')
    o variaciones de texto a los timeframes canónicos de CCXT/DB ('1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w').
    """
    if not tf or not isinstance(tf, str):
        return '1d'
    s = tf.strip().lower()
    mapping = {
        '1m': '1m', '1 minuto': '1m', '1 minutes': '1m', '1min': '1m',
        '5m': '5m', '5 minutos': '5m', '5 minutes': '5m', '5min': '5m',
        '15m': '15m', '15 minutos': '15m', '15 minutes': '15m', '15min': '15m',
        '30m': '30m', '30 minutos': '30m', '30 minutes': '30m', '30min': '30m',
        '1h': '1h', '1 hora': '1h', '1 hour': '1h', '1h': '1h',
        '4h': '4h', '4 horas': '4h', '4 hours': '4h', '4h': '4h',
        '1d': '1d', '1 día': '1d', '1 dia': '1d', '1 day': '1d', 'diario': '1d', 'daily': '1d',
        '1w': '1w', '1 semana': '1w', '1 week': '1w', 'semanal': '1w', 'weekly': '1w'
    }
    if s in mapping:
        return mapping[s]
    import re
    m = re.match(r'^(\d+)\s*([a-zñáéíóú]+)$', s)
    if m:
        num, unit = m.group(1), m.group(2)
        if unit.startswith('m'): return f"{num}m"
        if unit.startswith('h'): return f"{num}h"
        if unit.startswith('d'): return f"{num}d"
        if unit.startswith('w') or unit.startswith('s'): return f"{num}w"
    return tf


def timeframe_seconds(timeframe: str) -> int:
    """Duración de una vela en segundos ('4h' -> 14400). 0 si no se reconoce."""
    try:
        return int(ccxt.Exchange.parse_timeframe(normalize_timeframe(timeframe)))
    except Exception:
        return 0


def drop_unclosed_candles(df: pd.DataFrame, timeframe: str, now: datetime = None) -> pd.DataFrame:
    """
    Quita las velas que todavía no cerraron (timestamp de apertura + duración > ahora).
    Los exchanges devuelven la vela en curso como última fila; guardarla dejaba en la BD
    un OHLC parcial (ej. BTC/USDT 1d del 18/09/2026: rango 0.2% y cierre 76,396 cuando
    la vela siguiente abrió en 80,896) que luego nunca se corregía.
    """
    if df is None or df.empty or 'timestamp' not in df.columns:
        return df
    tf_sec = timeframe_seconds(timeframe)
    if tf_sec <= 0:
        return df
    now = ensure_utc(now or datetime.now(timezone.utc))
    ts = pd.to_datetime(df['timestamp'], utc=True)
    closed = (ts + pd.Timedelta(seconds=tf_sec)) <= pd.Timestamp(now)
    return df[closed.values]


class MarketDataManager:
    # Velas finales que se re-descargan en cada actualización incremental para corregir
    # las que se guardaron antes de cerrar.
    REFRESH_LAST_CANDLES = 5

    def __init__(self, db_session: Session = None):
        self.exchange = get_binance_exchange({
            'enableRateLimit': True,
        })
        self.db = db_session or SessionLocal()
        
    def fetch_ohlcv(self, symbol: str, timeframe: str, since: datetime = None, limit: int = 1000) -> pd.DataFrame:
        """
        Descarga datos OHLCV de Binance.
        """
        timeframe = normalize_timeframe(timeframe)
        since = ensure_utc(since)
        since_ms = int(since.timestamp() * 1000) if since else None
        
        try:
            ccxt_symbol = symbol
            if '/' not in ccxt_symbol:
                for q in ['USDT', 'BTC', 'ETH', 'BNB', 'BUSD', 'USDC']:
                    if ccxt_symbol.endswith(q) and len(ccxt_symbol) > len(q):
                        ccxt_symbol = f"{ccxt_symbol[:-len(q)]}/{q}"
                        break

            ohlcv = self.exchange.fetch_ohlcv(ccxt_symbol, timeframe, since=since_ms, limit=limit)
            
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
                # Si ya cubre la fecha solicitada, resumimos desde el último registro guardado,
                # retrocediendo unas velas para re-descargar (y corregir vía upsert en
                # _save_df_to_db) las últimas que pudieron guardarse incompletas.
                tf_sec = timeframe_seconds(timeframe)
                since_dt = max_db - pd.Timedelta(seconds=tf_sec * self.REFRESH_LAST_CANDLES) if tf_sec > 0 else max_db
                since_dt = max(since_dt, min_db)
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
            df = drop_unclosed_candles(self.fetch_ohlcv_yahoo(symbol, timeframe, start_date=since_dt, end_date=_end_dt), timeframe)
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
            raw = self.fetch_ohlcv(symbol, timeframe, since=since_dt)
            if raw.empty or len(raw) <= 1:
                # Una sola fila = solo la vela ya guardada (o la vela en curso): no hay más.
                # Igual se guarda por si corrige una vela incompleta previa.
                self._save_df_to_db(drop_unclosed_candles(raw, timeframe))
                break
            df = drop_unclosed_candles(raw, timeframe)
            if df.empty:
                break

            # Filter out records beyond end_date
            if end_date:
                df = df[df['timestamp'] <= end_date]
                if df.empty:
                    break

            self._save_df_to_db(df)

            prev_since = since_dt
            since_dt = ensure_utc(df['timestamp'].iloc[-1])
            # Se descartó la vela en curso (ya estamos en la cabeza de la serie) o no hubo
            # avance: sin esta salida el bucle volvería a pedir siempre la misma página.
            if len(df) < len(raw) or since_dt <= ensure_utc(prev_since):
                break
            msg2 = f"Downloaded {len(df)} candles for {symbol}. Next fetch from {since_dt}"
            print(msg2)
            if progress_callback: progress_callback(msg2)
            
            if end_date and since_dt >= end_date:
                break
                
            time.sleep(self.exchange.rateLimit / 1000) # Respetar rate limits
            
    def _save_df_to_db(self, df):
        """Inserta velas nuevas y corrige las existentes. Devuelve (insertadas, actualizadas)."""
        if df is None or df.empty:
            return 0, 0
            
        symbol = df['symbol'].iloc[0]
        timeframe = df['timeframe'].iloc[0]
        
        # Batch timestamp conversion
        records = df.to_dict(orient='records')
        ts_map = {ensure_utc(rec['timestamp']).replace(tzinfo=None): rec for rec in records}
        ts_list = list(ts_map.keys())
        
        # Single query (batched) to fetch all existing timestamps in this batch. Se trocea
        # el IN(...) en lotes de 500: una descarga histórica grande puede traer más
        # timestamps de los que SQLite admite en un solo IN() ("too many SQL variables"),
        # ver mismo fix en onchain_data.py:update_historical_data.
        existing = {}
        batch_size = 500
        for i in range(0, len(ts_list), batch_size):
            batch = ts_list[i:i + batch_size]
            for obj in self.db.query(OHLCV).filter(
                OHLCV.symbol == symbol,
                OHLCV.timeframe == timeframe,
                OHLCV.timestamp.in_(batch)
            ).all():
                existing[obj.timestamp] = obj

        new_objects = []
        updated = 0
        for ts_naive, rec in ts_map.items():
            vals = {k: float(rec[k]) for k in ('open', 'high', 'low', 'close', 'volume')}
            obj = existing.get(ts_naive)
            if obj is None:
                new_objects.append(OHLCV(
                    symbol=rec['symbol'],
                    timeframe=rec['timeframe'],
                    timestamp=ts_naive,
                    **vals
                ))
            elif any(abs((getattr(obj, k) or 0.0) - v) > 1e-9 for k, v in vals.items()):
                # Upsert: antes una vela ya guardada nunca se actualizaba, así que una vela
                # descargada antes de cerrar quedaba incompleta para siempre.
                for k, v in vals.items():
                    setattr(obj, k, v)
                updated += 1

        if new_objects:
            self.db.bulk_save_objects(new_objects)
        if new_objects or updated:
            self.db.commit()
        return len(new_objects), updated
            
    def get_data_refreshed(self, symbol: str, timeframe: str, start_date: datetime, end_date: datetime = None) -> pd.DataFrame:
        """
        Como get_data, pero descarga lo que falta si la BD está vacía o si su última vela
        cerrada queda antes del final del rango pedido. Antes los backtests solo descargaban
        cuando no había NINGÚN dato, así que una serie desactualizada se usaba en silencio.
        Si la descarga falla se devuelve lo que haya en la BD.
        """
        df = self.get_data(symbol, timeframe, start_date, end_date)
        tf_sec = timeframe_seconds(timeframe)
        target_end = min(ensure_utc(end_date) if end_date else ensure_utc(datetime.now(timezone.utc)),
                         ensure_utc(datetime.now(timezone.utc)))
        stale = df.empty or (tf_sec > 0 and df.index[-1] + pd.Timedelta(seconds=2 * tf_sec) <= pd.Timestamp(target_end))
        if stale:
            try:
                self.update_historical_data(symbol, timeframe, start_date, end_date)
            except Exception as e:
                print(f"No se pudo actualizar {symbol} {timeframe}: {e}")
            df = self.get_data(symbol, timeframe, start_date, end_date)
        return df

    def get_data(self, symbol: str, timeframe: str, start_date: datetime, end_date: datetime = None) -> pd.DataFrame:
        """
        Obtiene datos históricos desde la base de datos local.
        """
        timeframe = normalize_timeframe(timeframe)
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
