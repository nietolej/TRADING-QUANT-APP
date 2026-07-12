import streamlit as st
import os

st.set_page_config(
    page_title="Trading Quant Dashboard",
    page_icon="📈",
    layout="wide",
)

st.title("Sistema de Trading Cuantitativo")
st.markdown("""
Bienvenido al dashboard. Por favor, selecciona una página en la barra lateral para continuar.

- **Backtest**: Ejecuta simulaciones de tus estrategias en datos históricos.
- **Configuración de Estrategias**: Visualiza y edita los parámetros de las estrategias en formato YAML.
- **Análisis On-Chain**: Explora las métricas on-chain en contraste con el precio.
- **Historial de Resultados**: Revisa, compara y exporta backtests previos (similar al Strategy Analyzer).
- **Catálogo de Datos**: Gestiona qué símbolos, timeframes y datos on-chain están descargados localmente.
- **Monitor en Vivo**: (Fase 2+) Visualiza el paper trading y estado de la conexión.

---
**Aviso Legal:** Esta herramienta es únicamente para análisis y no constituye asesoría financiera.
""")

# Inicializar Base de Datos en el arranque de la app si no existe
from data_layer.storage import init_db
init_db()
