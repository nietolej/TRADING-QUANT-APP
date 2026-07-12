import streamlit as st
import os
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
import glob
from strategy_engine.base_strategy import BaseStrategy
from backtest_engine.backtester import Backtester
from data_layer.market_data import MarketDataManager
from data_layer.onchain_data import OnChainDataManager
from data_layer.storage import SessionLocal, BacktestRun

st.set_page_config(page_title="Backtest", layout="wide")
st.title("Ejecutar Backtest")

# Listar estrategias
strategy_files = glob.glob("config/strategies/*.yaml")
strategy_names = [os.path.basename(f) for f in strategy_files]

col1, col2 = st.columns(2)
with col1:
    selected_strategy = st.selectbox("Seleccionar Estrategia", strategy_names)
    start_date = st.date_input("Fecha Inicio", pd.to_datetime("2023-01-01"))
    initial_capital = st.number_input("Capital Inicial", min_value=10.0, value=10000.0, step=100.0)
    
with col2:
    end_date = st.date_input("Fecha Fin", pd.to_datetime("today"))
    symbol_override = st.text_input("Activo a operar (Símbolo)", value="BTC/USDT", help="Sobreescribe el activo de la estrategia YAML.")
    st.write("Asegúrate de haber descargado los datos en el Catálogo de Datos.")

if st.button("Correr Backtest"):
    strategy_path = os.path.join("config", "strategies", selected_strategy)
    try:
        strategy = BaseStrategy(strategy_path)
        # Usar override del símbolo si se ingresó algo, sino el de la estrategia
        symbol = symbol_override if symbol_override else strategy.symbol
        strategy.symbol = symbol # actualizamos para que quede registrado en los resultados
        timeframe = strategy.timeframe
        
        db = SessionLocal()
        market_mgr = MarketDataManager(db)
        df = market_mgr.get_data(symbol, timeframe, pd.to_datetime(start_date), pd.to_datetime(end_date))
        
        if df.empty:
            st.error(f"No hay datos locales para {symbol} {timeframe}. Por favor descárgalos primero.")
        else:
            # Aquí idealmente haríamos un join con los datos onchain usando OnChainDataManager si la estrategia lo requiere
            # Para el MVP, asumiremos que están integrados o evaluaremos sin onchain si no están
            
            backtester = Backtester(strategy, initial_capital=initial_capital)
            with st.spinner("Calculando..."):
                results = backtester.run(df)
            
            # Guardar en BD
            run_obj = BacktestRun(
                run_id=results["run_id"],
                strategy_name=results["strategy_name"],
                config_snapshot=results["config_snapshot"],
                symbol=results["symbol"],
                timeframe=results["timeframe"],
                start_date=results["start_date"],
                end_date=results["end_date"],
                created_at=results["created_at"],
                cagr=results["cagr"],
                max_drawdown_pct=results["max_drawdown_pct"],
                win_rate=results["percent_profitable"],
                profit_factor=results["profit_factor"],
                total_trades=results["total_trades"],
                percent_profitable=results["percent_profitable"],
                average_trade_net_profit=results["average_trade_net_profit"]
            )
            db.add(run_obj)
            db.commit()
            
            st.success("Backtest completado y guardado.")
            
            # Métricas
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Trades", results["total_trades"])
            c2.metric("Win Rate %", f"{results['percent_profitable']:.2f}%")
            c3.metric("Profit Factor", f"{results['profit_factor']:.2f}")
            c4.metric("Max Drawdown %", f"{results['max_drawdown_pct']:.2f}%")
            
            # Gráfico de Equity
            equity_curve = results["equity_curve"]
            if not equity_curve.empty:
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=equity_curve.index, y=equity_curve['equity'], mode='lines', name='Equity'))
                fig.update_layout(title="Curva de Equity", xaxis_title="Fecha", yaxis_title="Capital")
                st.plotly_chart(fig, use_container_width=True)
                
            with st.expander("Ver Operaciones"):
                st.dataframe(results["trades"])
                
    except Exception as e:
        st.error(f"Error durante el backtest: {str(e)}")
