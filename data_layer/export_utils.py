import pandas as pd
import logging

logger = logging.getLogger(__name__)

def export_df_to_ninjatrader8(
    df: pd.DataFrame, 
    timeframe: str = '1h', 
    timestamp_mode: str = 'end_of_bar', 
    delimiter: str = ';', 
    date_format_mode: str = 'auto',
    volume_as_int: bool = True
) -> str:
    """
    Convierte un DataFrame con datos OHLCV al formato exacto de datos históricos de NinjaTrader 8 (.txt).
    
    Formato Estándar Diario NinjaTrader 8 (date_format_mode = 'daily_only' o timeframe '1d'):
    YYYYMMDD;OPEN;HIGH;LOW;CLOSE;VOLUME
    Ejemplo exacto: 20200101;7165.72;7238.14;7136.05;7174.33;335135212594
    """
    if df is None or df.empty:
        return ""
        
    df_export = df.copy()
    
    # Extraer timestamps
    if 'timestamp' in df_export.columns:
        ts_series = pd.to_datetime(df_export['timestamp'])
    else:
        ts_series = pd.to_datetime(df_export.index)

    # Remover zona horaria si existe (convertir a naive UTC)
    if isinstance(ts_series, pd.DatetimeIndex):
        if ts_series.tz is not None:
            ts_series = ts_series.tz_convert('UTC').tz_localize(None)
    else:
        if ts_series.dt.tz is not None:
            ts_series = ts_series.dt.tz_convert('UTC').dt.tz_localize(None)

    tf_str = str(timeframe).lower()
    is_daily_tf = tf_str in ['1d', '1w', 'd', 'w', 'daily', 'weekly']
    
    if date_format_mode == 'daily_only':
        include_time = False
        date_split = False
    elif date_format_mode == 'split_field':
        include_time = True
        date_split = True
    elif date_format_mode == 'single_field':
        include_time = True
        date_split = False
    else: # 'auto'
        include_time = not is_daily_tf
        date_split = False

    # Aplicar desplazamiento "Fin de la barra (End of Bar)" si se solicita
    if timestamp_mode == 'end_of_bar':
        tf_offsets = {
            '1m': pd.Timedelta(minutes=1),
            '3m': pd.Timedelta(minutes=3),
            '5m': pd.Timedelta(minutes=5),
            '15m': pd.Timedelta(minutes=15),
            '30m': pd.Timedelta(minutes=30),
            '1h': pd.Timedelta(hours=1),
            '2h': pd.Timedelta(hours=2),
            '4h': pd.Timedelta(hours=4),
            '6h': pd.Timedelta(hours=6),
            '8h': pd.Timedelta(hours=8),
            '12h': pd.Timedelta(hours=12),
            '1d': pd.Timedelta(days=1),
            '1w': pd.Timedelta(weeks=1),
        }
        offset = tf_offsets.get(tf_str, pd.Timedelta(minutes=0))
        ts_series = ts_series + offset

    def _fmt_price(val):
        if pd.isna(val): return "0"
        f = float(val)
        if f.is_integer():
            return str(int(f))
        s = f"{f:.8f}".rstrip('0').rstrip('.')
        return s

    lines = []
    for i, (_, row) in enumerate(df_export.iterrows()):
        dt = ts_series[i]
        date_str = dt.strftime('%Y%m%d')
        time_str = dt.strftime('%H%M%S')
        
        try:
            o = _fmt_price(row['open'])
            h = _fmt_price(row['high'])
            l = _fmt_price(row['low'])
            c = _fmt_price(row['close'])
            
            vol_val = float(row['volume'])
            if volume_as_int:
                v = str(int(round(vol_val)))
            else:
                v = f"{vol_val:.2f}"
        except Exception as e:
            logger.warning(f"Error procesando fila {i}: {e}")
            continue
            
        if not include_time:
            # Formato Diario Estándar NinjaTrader 8: YYYYMMDD;OPEN;HIGH;LOW;CLOSE;VOLUME
            line = f"{date_str}{delimiter}{o}{delimiter}{h}{delimiter}{l}{delimiter}{c}{delimiter}{v}"
        elif date_split:
            # Formato Fecha y Hora separadas: YYYYMMDD;HHMMSS;OPEN;HIGH;LOW;CLOSE;VOLUME
            line = f"{date_str}{delimiter}{time_str}{delimiter}{o}{delimiter}{h}{delimiter}{l}{delimiter}{c}{delimiter}{v}"
        else:
            # Formato Campo único Fecha Hora: YYYYMMDD HHMMSS;OPEN;HIGH;LOW;CLOSE;VOLUME
            line = f"{date_str} {time_str}{delimiter}{o}{delimiter}{h}{delimiter}{l}{delimiter}{c}{delimiter}{v}"
            
        lines.append(line)
        
    return "\n".join(lines)
