import streamlit as st
import pandas as pd
from data_layer.storage import SessionLocal, OHLCV, OnChainMetric
from sqlalchemy import func
from data_layer.market_data import MarketDataManager
from data_layer.onchain_data import OnChainDataManager

st.set_page_config(page_title="Catálogo de Datos", layout="wide")
st.title("Gestor de Datos Históricos")

db = SessionLocal()

st.subheader("Datos de Mercado (Binance)")
market_query = db.query(
    OHLCV.symbol, 
    OHLCV.timeframe, 
    func.min(OHLCV.timestamp).label('start'),
    func.max(OHLCV.timestamp).label('end'),
    func.count(OHLCV.id).label('count')
).group_by(OHLCV.symbol, OHLCV.timeframe)

df_market = pd.read_sql(market_query.statement, db.bind)
st.dataframe(df_market, use_container_width=True)

st.subheader("Datos On-Chain")
onchain_query = db.query(
    OnChainMetric.metric_name, 
    OnChainMetric.symbol, 
    func.min(OnChainMetric.timestamp).label('start'),
    func.max(OnChainMetric.timestamp).label('end'),
    func.count(OnChainMetric.id).label('count')
).group_by(OnChainMetric.metric_name, OnChainMetric.symbol)

df_onchain = pd.read_sql(onchain_query.statement, db.bind)
st.dataframe(df_onchain, use_container_width=True)

st.write("---")
st.subheader("Descargar Nuevos Datos")
col1, col2 = st.columns(2)

with col1:
    st.write("Datos de Mercado")
    symbol = st.text_input("Símbolo (ej. BTC/USDT)")
    timeframe = st.selectbox("Timeframe", ["1d", "4h", "1h", "15m"])
    start_date = st.date_input("Fecha Inicio Descarga", pd.to_datetime("2023-01-01"))
    if st.button("Descargar Mercado"):
        with st.spinner("Descargando..."):
            mgr = MarketDataManager(db)
            mgr.update_historical_data(symbol, timeframe, pd.to_datetime(start_date))
        st.success("Descarga de mercado completada.")
        st.rerun()

with col2:
    st.write("Datos On-Chain (DefiLlama)")
    metric_name = st.text_input("Métrica On-Chain", value="stablecoin_market_cap")
    symbol_oc = st.text_input("Símbolo On-Chain (ej. USDT)", value="USDT")
    start_date_oc = st.date_input("Fecha Inicio On-Chain", pd.to_datetime("2023-01-01"))
    if st.button("Descargar On-Chain"):
        with st.spinner("Descargando..."):
            mgr = OnChainDataManager(db)
            mgr.update_historical_data(metric_name, symbol_oc, pd.to_datetime(start_date_oc), 'defillama')
        st.success("Descarga on-chain completada.")
        st.rerun()
