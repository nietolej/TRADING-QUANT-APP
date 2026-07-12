from datetime import datetime
import pandas as pd
from sqlalchemy.orm import Session
from .storage import OnChainMetric, SessionLocal
from .data_sources.defillama import DefiLlamaProvider
from .data_sources.cryptoquant import CryptoQuantProvider

class OnChainDataManager:
    def __init__(self, db_session: Session = None):
        self.db = db_session or SessionLocal()
        self.providers = {
            'defillama': DefiLlamaProvider(),
            'cryptoquant': CryptoQuantProvider()
        }
        
    def update_historical_data(self, metric_name: str, symbol: str, start_date: datetime, provider_name: str):
        provider = self.providers.get(provider_name)
        if not provider:
            raise ValueError(f"Proveedor {provider_name} no configurado.")
            
        last_record = self.db.query(OnChainMetric).filter(
            OnChainMetric.metric_name == metric_name,
            OnChainMetric.symbol == symbol
        ).order_by(OnChainMetric.timestamp.desc()).first()
        
        since_dt = last_record.timestamp if last_record else start_date
        
        print(f"Actualizando métrica {metric_name} ({symbol}) desde {since_dt} vía {provider_name}")
        
        df = provider.fetch_metric(metric_name, symbol, since_dt, datetime.now())
        
        if df.empty:
            print("No hay datos nuevos.")
            return
            
        records = df.to_dict(orient='records')
        for rec in records:
            exists = self.db.query(OnChainMetric).filter_by(
                metric_name=rec['metric_name'], 
                symbol=rec['symbol'], 
                timestamp=rec['timestamp']
            ).first()
            if not exists:
                metric_obj = OnChainMetric(
                    metric_name=rec['metric_name'],
                    symbol=rec['symbol'],
                    timestamp=rec['timestamp'],
                    value=rec['value'],
                    source=rec['source']
                )
                self.db.add(metric_obj)
        
        self.db.commit()
        print(f"Descargados {len(df)} registros on-chain nuevos.")
        
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
