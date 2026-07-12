import os
from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

load_dotenv()

db_url = os.getenv("DB_URL", "sqlite:///data/trading_quant.db")
# Asegurarse de que el directorio existe
db_path = db_url.replace("sqlite:///", "")
if db_path and not db_path.startswith(":memory:"):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

engine = create_engine(db_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class OHLCV(Base):
    __tablename__ = "ohlcv"
    
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True)
    timeframe = Column(String, index=True)
    timestamp = Column(DateTime, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    
    __table_args__ = (
        UniqueConstraint('symbol', 'timeframe', 'timestamp', name='_symbol_tf_ts_uc'),
    )

class OnChainMetric(Base):
    __tablename__ = "onchain_metrics"
    
    id = Column(Integer, primary_key=True, index=True)
    metric_name = Column(String, index=True)
    symbol = Column(String, index=True) # Generalmente BTC, ETH, USDT
    timestamp = Column(DateTime, index=True)
    value = Column(Float)
    source = Column(String)
    
    __table_args__ = (
        UniqueConstraint('metric_name', 'symbol', 'timestamp', name='_metric_sym_ts_uc'),
    )

class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    
    run_id = Column(String, primary_key=True, index=True)
    strategy_name = Column(String)
    config_snapshot = Column(String) # YAML como string
    symbol = Column(String)
    timeframe = Column(String)
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    created_at = Column(DateTime)
    
    # Métricas clave
    cagr = Column(Float)
    sharpe_ratio = Column(Float)
    sortino_ratio = Column(Float)
    max_drawdown_pct = Column(Float)
    win_rate = Column(Float)
    profit_factor = Column(Float)
    total_trades = Column(Integer)
    percent_profitable = Column(Float)
    average_trade_net_profit = Column(Float)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
