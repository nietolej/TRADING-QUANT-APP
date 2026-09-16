from datetime import datetime, timezone
import pandas as pd
from sqlalchemy.orm import Session
from .storage import OnChainMetric, SessionLocal
from .data_sources.defillama import DefiLlamaProvider
from .data_sources.cryptoquant import CryptoQuantProvider
from .data_sources.coingecko import CoinGeckoProvider
from .data_sources.glassnode import GlassnodeProvider
from .data_sources.binance_public_provider import BinancePublicOnChainProvider
from .data_sources.blockchain_info_provider import BlockchainInfoProvider

class OnChainDataManager:
    def __init__(self, db_session: Session = None):
        self.db = db_session or SessionLocal()
        self.providers = {
            'defillama': DefiLlamaProvider(),
            'cryptoquant': CryptoQuantProvider(),
            'coingecko': CoinGeckoProvider(),
            'glassnode': GlassnodeProvider(),
            # Sin API key: funding_rates, open_interest y taker_buy_sell_ratio vía
            # endpoints públicos de Binance Futures.
            'binance_public': BinancePublicOnChainProvider(),
            # Sin API key: exchange_reserve de BTC vía balance real de una wallet de
            # Binance públicamente verificada (blockchain.info).
            'blockchain_info': BlockchainInfoProvider(),
        }

    def update_historical_data(self, metric_name: str, symbol: str, start_date: datetime, provider_name: str):
        provider = self.providers.get(provider_name)
        if not provider:
            raise ValueError(f"Proveedor {provider_name} no configurado.")

        # Normalizar a UTC "aware": la columna DateTime de la BD guarda naive, así que un
        # last_record.timestamp leído de vuelta pierde la zona horaria. Sin esto, algunos
        # proveedores (CryptoQuant, Glassnode) comparaban ese naive contra sus propios
        # timestamps aware y reventaban con TypeError en toda sincronización incremental
        # (la segunda en adelante), o -en Glassnode- interpretaban el naive como hora local
        # al llamar .timestamp(), desplazando la ventana de descarga.
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)

        last_record = self.db.query(OnChainMetric).filter(
            OnChainMetric.metric_name == metric_name,
            OnChainMetric.symbol == symbol
        ).order_by(OnChainMetric.timestamp.desc()).first()

        if last_record:
            since_dt = last_record.timestamp
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
        else:
            since_dt = start_date

        print(f"Actualizando métrica {metric_name} ({symbol}) desde {since_dt} vía {provider_name}")

        df = provider.fetch_metric(metric_name, symbol, since_dt, datetime.now(timezone.utc))

        if df.empty:
            print("No hay datos nuevos.")
            return 0

        records = df.to_dict(orient='records')

        # Timestamps a naive UTC (así se almacenan) para poder comparar contra la BD.
        for rec in records:
            ts = rec['timestamp']
            if getattr(ts, 'tzinfo', None) is not None:
                rec['timestamp'] = ts.astimezone(timezone.utc).replace(tzinfo=None)

        # Una sola consulta para saber qué timestamps ya existen, en vez de un SELECT por
        # registro (con limit=10000 en CryptoQuant, eso eran hasta 10k idas y vueltas a la
        # BD en una sola sincronización). Mismo patrón que market_data.py:_save_df_to_db.
        candidate_timestamps = [rec['timestamp'] for rec in records]
        existing_ts = {
            row[0] for row in self.db.query(OnChainMetric.timestamp).filter(
                OnChainMetric.metric_name == metric_name,
                OnChainMetric.symbol == symbol,
                OnChainMetric.timestamp.in_(candidate_timestamps)
            ).all()
        }

        new_objects = [
            OnChainMetric(
                metric_name=rec['metric_name'],
                symbol=rec['symbol'],
                timestamp=rec['timestamp'],
                value=rec['value'],
                source=rec['source']
            )
            for rec in records if rec['timestamp'] not in existing_ts
        ]

        if new_objects:
            self.db.bulk_save_objects(new_objects)
            self.db.commit()

        print(f"Descargados {len(new_objects)} registros on-chain nuevos ({len(records)} recibidos).")
        return len(new_objects)

    def get_data(self, metric_name: str, symbol: str, start_date: datetime, end_date: datetime = None) -> pd.DataFrame:
        query = self.db.query(OnChainMetric).filter(
            OnChainMetric.metric_name == metric_name,
            OnChainMetric.symbol == symbol,
            OnChainMetric.timestamp >= start_date
        )
        if end_date:
            query = query.filter(OnChainMetric.timestamp <= end_date)

        query = query.order_by(OnChainMetric.timestamp.asc())

        df = pd.read_sql(query.statement, self.db.bind)
        if not df.empty:
            df.set_index('timestamp', inplace=True)

        return df
