"""
Base de datos unificada diaria de todo el módulo On-Chain.

Consolida en una sola tabla (OnChainUnifiedDaily, ver storage.py) TODAS las métricas
on-chain ya sincronizadas para TODOS los símbolos (BTC, ETH, USDT, USDC, ...) más el
precio de mercado y columnas de dirección futura del precio (labels), resampleadas a
granularidad diaria. Es la fuente pensada para detectar patrones y entrenar modelos que
predigan la dirección del precio con algún grado de confianza — no reemplaza a
OnChainMetric (que sigue guardando los registros crudos tal como llegan de cada
proveedor), sino que es una vista derivada y reconstruible a partir de ella.

Formato largo (date, symbol, metric_name, value) en vez de una tabla ancha con columnas
fijas: agregar una métrica nueva a METRICS_BY_SYMBOL en onchain_analyzer_page.py no
requiere ningún cambio de esquema aquí. get_unified_wide_df() la pivotea a formato
ancho (una columna 'SYMBOL_metric_name' por serie) para consumo directo en pandas/ML.
"""
from datetime import datetime
import pandas as pd
from sqlalchemy.orm import Session
from .storage import SessionLocal, OnChainMetric, OnChainUnifiedDaily
from .market_data import MarketDataManager

# Mismo criterio que onchain_analyzer_page.FLOW_METRICS: métricas aditivas (ocurrieron
# durante el día, se suman) vs. niveles/ratios (se toma el último valor del día). Se
# duplica aquí en vez de importar desde web_gui para no acoplar la capa de datos a la UI.
FLOW_METRICS = {
    'mint', 'burn', 'exchange_inflow', 'exchange_outflow', 'exchange_netflow',
}

# Símbolo on-chain -> par de mercado usado como referencia de precio para ese símbolo.
# Solo BTC/ETH tienen un par propio con USDT; USDT/USDC no se incluyen como "activo con
# precio propio" (ver _market_symbol_for en onchain_analyzer_page.py, que para
# stablecoins usa BTC/USDT como referencia general — no aporta una label de dirección
# específica de la stablecoin).
MARKET_PAIR_BY_SYMBOL = {
    'BTC': 'BTC/USDT',
    'ETH': 'ETH/USDT',
}

# Horizontes (en días) para las columnas de dirección futura ("¿subió o bajó N días
# después?"). 1 = sube, 0 = baja o igual. NaN (fila omitida) cuando el horizonte cae
# fuera del histórico de precio disponible.
DIRECTION_HORIZONS_DAYS = (1, 3, 7)


def _to_naive_utc(value) -> pd.Timestamp:
    """Normaliza un timestamp individual (str, datetime, o ya Timestamp; naive o
    tz-aware) a un pd.Timestamp naive en UTC. Se aplica elemento a elemento porque una
    columna con timestamps naive Y aware mezclados no se puede convertir de forma
    vectorizada con pd.to_datetime/.dt sin que pandas falle."""
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert('UTC').tz_localize(None)
    return ts


def _resample_daily(sub: pd.DataFrame, is_flow: bool, date_range: pd.DatetimeIndex) -> pd.Series:
    """sub: DataFrame con columnas ['timestamp', 'value'] de UN símbolo+métrica."""
    s = sub.set_index(pd.to_datetime(sub['timestamp']))['value']
    daily = s.resample('1D').sum() if is_flow else s.resample('1D').last()
    daily.index = daily.index.normalize()
    if is_flow:
        daily = daily.reindex(date_range, fill_value=0.0)
    else:
        daily = daily.reindex(date_range).ffill()
    return daily


def build_unified_daily(db: Session = None) -> int:
    """
    Reconstruye por completo OnChainUnifiedDaily a partir de OnChainMetric (todas las
    métricas, todos los símbolos ya sincronizados) más precio de mercado y labels de
    dirección futura. Se puede volver a ejecutar tras cada sincronización on-chain: hace
    upsert por (date, symbol, metric_name), no acumula duplicados.

    Devuelve la cantidad de filas escritas.
    """
    own_session = db is None
    db = db or SessionLocal()
    try:
        df_all = pd.read_sql(db.query(OnChainMetric).statement, db.bind)
        if df_all.empty:
            return 0

        # Filas de sincronizaciones antiguas (previas a la normalización de timezone en
        # onchain_data.py) pueden tener timestamps naive junto a otras aware en la misma
        # columna. pd.to_datetime(...) vectorizado sobre esa mezcla no produce un
        # datetime64 uniforme (queda dtype object con Timestamps tz-naive y tz-aware
        # entremezclados), y cualquier comparación posterior (.min()/.max()/reindex)
        # revienta con "Cannot compare tz-naive and tz-aware timestamps". Se normaliza
        # elemento a elemento a naive UTC para tolerar esa mezcla.
        df_all['timestamp'] = df_all['timestamp'].map(_to_naive_utc)
        min_ts = df_all['timestamp'].min().normalize()
        max_ts = max(df_all['timestamp'].max().normalize(), pd.Timestamp.now(tz='UTC').tz_localize(None).normalize())
        date_range = pd.date_range(start=min_ts, end=max_ts, freq='1D')

        rows = {}  # (date, symbol, metric_name) -> value

        combos = df_all[['symbol', 'metric_name']].drop_duplicates()
        for sym, m_name in combos.itertuples(index=False):
            is_flow = m_name.lower() in FLOW_METRICS
            sub = df_all.loc[
                (df_all['symbol'] == sym) & (df_all['metric_name'] == m_name),
                ['timestamp', 'value']
            ]
            daily = _resample_daily(sub, is_flow, date_range)
            for ts, val in daily.items():
                if pd.isna(val):
                    continue
                rows[(ts.to_pydatetime(), sym, m_name)] = float(val)

        # Precio de mercado + labels de dirección futura, por cada símbolo con par conocido.
        market_mgr = MarketDataManager()
        for sym, market_symbol in MARKET_PAIR_BY_SYMBOL.items():
            df_price = market_mgr.get_data(market_symbol, '1d', min_ts.to_pydatetime())
            if df_price is None or df_price.empty:
                continue

            close = df_price['close'].sort_index()
            close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
            close = close.reindex(date_range).ffill()

            ret_1d = close.pct_change(1)

            for ts, val in close.items():
                if pd.isna(val):
                    continue
                rows[(ts.to_pydatetime(), sym, 'price_close')] = float(val)
            for ts, val in ret_1d.items():
                if pd.isna(val):
                    continue
                rows[(ts.to_pydatetime(), sym, 'price_return_1d')] = float(val)

            for horizon in DIRECTION_HORIZONS_DAYS:
                future_close = close.shift(-horizon)
                direction = pd.Series(
                    [1.0 if fc > c else 0.0 if pd.notna(fc) else float('nan')
                     for c, fc in zip(close, future_close)],
                    index=close.index,
                )
                for ts, val in direction.items():
                    if pd.isna(val):
                        continue
                    rows[(ts.to_pydatetime(), sym, f'direction_{horizon}d')] = float(val)

        if not rows:
            return 0

        # Reconstrucción completa en vez de UPDATE fila por fila: la tabla es pequeña
        # (días x símbolos x métricas, no ticks intradía), así que borrar y reinsertar es
        # simple y suficientemente rápido, y evita tener que resolver upserts fila por
        # fila contra SQLite.
        db.query(OnChainUnifiedDaily).delete()
        db.bulk_save_objects([
            OnChainUnifiedDaily(date=d, symbol=sym, metric_name=m_name, value=val)
            for (d, sym, m_name), val in rows.items()
        ])
        db.commit()
        return len(rows)
    finally:
        if own_session:
            db.close()


def get_unified_wide_df(start_date: datetime = None, end_date: datetime = None, db: Session = None) -> pd.DataFrame:
    """
    Devuelve OnChainUnifiedDaily en formato ANCHO: una fila por día (índice de fecha),
    una columna por 'SYMBOL_metric_name' (ej. 'BTC_mvrv', 'ETH_price_close',
    'BTC_direction_7d'). Lista para alimentar un modelo o para mostrarse en la UI.
    """
    own_session = db is None
    db = db or SessionLocal()
    try:
        query = db.query(OnChainUnifiedDaily)
        if start_date:
            query = query.filter(OnChainUnifiedDaily.date >= start_date.replace(tzinfo=None) if start_date.tzinfo else start_date)
        if end_date:
            query = query.filter(OnChainUnifiedDaily.date <= end_date.replace(tzinfo=None) if end_date.tzinfo else end_date)
        df = pd.read_sql(query.statement, db.bind)
    finally:
        if own_session:
            db.close()

    if df.empty:
        return df

    df['column'] = [_column_name(sym, m) for sym, m in zip(df['symbol'], df['metric_name'])]
    wide = df.pivot_table(index='date', columns='column', values='value', aggfunc='last')
    wide.index = pd.to_datetime(wide.index)
    return wide.sort_index()


def _column_name(symbol: str, metric_name: str) -> str:
    """'SYMBOL_metric_name', salvo que metric_name ya incluya el símbolo como prefijo
    (ej. CoinGecko guarda 'btc_market_cap', DefiLlama 'usdt_market_cap'/'usdc_market_cap')
    — en ese caso se usa solo metric_name en mayúsculas, para no repetir el símbolo dos
    veces (bug visto como columnas 'BTC_BTC_MARKET_CAP')."""
    if metric_name.lower().startswith(f"{symbol.lower()}_"):
        return metric_name.upper()
    return f"{symbol}_{metric_name}"
