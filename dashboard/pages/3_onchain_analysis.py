import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from data_layer.storage import SessionLocal, OnChainMetric, OHLCV

st.set_page_config(page_title="Análisis On-Chain", layout="wide")
st.title("Análisis On-Chain vs Precio")

st.write("Selecciona una métrica para comparar con el precio histórico.")

db = SessionLocal()

# Obtener métricas disponibles
metrics_query = db.query(OnChainMetric.metric_name, OnChainMetric.symbol).distinct().all()
if not metrics_query:
    st.warning("No hay datos on-chain descargados. Ve a Catálogo de Datos.")
else:
    options = [f"{m[0]} ({m[1]})" for m in metrics_query]
    selected = st.selectbox("Métrica On-Chain", options)
    metric_name, symbol = selected.split(" (")
    symbol = symbol.replace(")", "")
    
    start_date = st.date_input("Fecha Inicio", pd.to_datetime("2023-01-01"))
    end_date = st.date_input("Fecha Fin", pd.to_datetime("today"))
    
    if st.button("Analizar"):
        # Obtener Precio
        price_query = db.query(OHLCV.timestamp, OHLCV.close).filter(
            OHLCV.symbol == symbol,
            OHLCV.timeframe == "1d",
            OHLCV.timestamp >= pd.to_datetime(start_date),
            OHLCV.timestamp <= pd.to_datetime(end_date)
        ).order_by(OHLCV.timestamp.asc())
        
        df_price = pd.read_sql(price_query.statement, db.bind)
        
        # Obtener OnChain
        onchain_query = db.query(OnChainMetric.timestamp, OnChainMetric.value).filter(
            OnChainMetric.metric_name == metric_name,
            OnChainMetric.symbol == symbol,
            OnChainMetric.timestamp >= pd.to_datetime(start_date),
            OnChainMetric.timestamp <= pd.to_datetime(end_date)
        ).order_by(OnChainMetric.timestamp.asc())
        
        df_onchain = pd.read_sql(onchain_query.statement, db.bind)
        
        if df_price.empty or df_onchain.empty:
            st.error("Datos insuficientes para el rango de fechas.")
        else:
            from plotly.subplots import make_subplots
            fig = make_subplots(specs=[[{"secondary_y": True}]])
            
            fig.add_trace(
                go.Scatter(x=df_price['timestamp'], y=df_price['close'], name=f"{symbol} Price"),
                secondary_y=False,
            )
            fig.add_trace(
                go.Scatter(x=df_onchain['timestamp'], y=df_onchain['value'], name=metric_name, opacity=0.7),
                secondary_y=True,
            )
            
            fig.update_layout(title_text=f"{symbol} Precio vs {metric_name}")
            st.plotly_chart(fig, use_container_width=True)
