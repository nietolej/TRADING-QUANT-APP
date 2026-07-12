import streamlit as st
import pandas as pd
from data_layer.storage import SessionLocal, BacktestRun

st.set_page_config(page_title="Historial de Resultados", layout="wide")
st.title("Strategy Analyzer: Historial de Resultados")

db = SessionLocal()

query = db.query(BacktestRun).order_by(BacktestRun.created_at.desc())
df_runs = pd.read_sql(query.statement, db.bind)

if df_runs.empty:
    st.info("No hay backtests ejecutados aún.")
else:
    st.dataframe(
        df_runs[['run_id', 'strategy_name', 'symbol', 'timeframe', 'cagr', 'sharpe_ratio', 'max_drawdown_pct', 'profit_factor', 'total_trades', 'created_at']],
        use_container_width=True
    )
    
    st.write("---")
    st.subheader("Comparar Curvas de Equity (Futuro MVP)")
    st.info("En futuras iteraciones, podrás seleccionar múltiples run_id y superponer sus curvas de equity.")
