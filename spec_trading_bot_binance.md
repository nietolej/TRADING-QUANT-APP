# Especificación Técnica: Sistema de Trading Cuantitativo con Análisis On-Chain para Binance

**Versión:** 1.0
**Fecha:** Julio 2026
**Tipo de documento:** Especificación funcional y técnica para desarrollo (usable como prompt de construcción)

---

## 1. Resumen ejecutivo

Desarrollar una aplicación de trading cuantitativo que combine **análisis técnico convencional** (precio, medias móviles, osciladores) con **análisis on-chain** (capitalización de stablecoins, flujos de entrada/salida de exchanges) para generar, backtestear y —en fases posteriores— ejecutar estrategias automatizadas en **Binance**.

El sistema debe permitir:
- Definir estrategias mediante **condiciones de entrada y salida configurables**.
- Gestionar riesgo con **stop loss / take profit fijos y dinámicos**.
- Backtestear con métricas de rendimiento robustas y control de sobreajuste (overfitting).
- Escalar de backtesting → paper trading → trading en vivo sin rediseñar la arquitectura.

**Disclaimer que debe incluir la app:** el sistema es una herramienta de análisis y ejecución; no constituye asesoría financiera. El trading algorítmico conlleva riesgo de pérdida de capital.

---

## 2. Alcance del proyecto y fases

### Fase 1 — MVP: Análisis y Backtesting (prioridad actual)
- Ingesta de datos históricos de mercado (Binance) y on-chain (fuentes gratuitas).
- Motor de backtesting con framework de estrategias configurable.
- Dashboard (Streamlit) para configurar, correr y visualizar backtests.
- Cálculo de métricas de rendimiento y gestión de riesgo simulada (SL/TP fijo y dinámico).
- Persistencia local (SQLite) de datos históricos y resultados.

### Fase 2 — Paper Trading
- Conexión a Binance Testnet.
- Ejecución simulada en tiempo real con las estrategias validadas en Fase 1.
- Alertas por Telegram.
- Monitor en vivo en el dashboard.

### Fase 3 — Trading en vivo
- Conexión a Binance real (API keys con permisos de trading, sin permiso de retiro).
- Módulo de ejecución de órdenes con control de riesgo, reconciliación y *kill switch*.
- Logging exhaustivo y auditoría de operaciones.

### Fase 4 — Escalamiento (opcional, futuro)
- Migración de fuentes on-chain gratuitas a proveedores de pago (Glassnode, CryptoQuant, Nansen) para mayor granularidad y cobertura.
- Multi-estrategia y portafolio con asignación dinámica de capital.
- Migración de UI a FastAPI + React si se requiere más rendimiento/concurrencia.

> **Este documento detalla completamente la Fase 1 y deja la arquitectura preparada para las Fases 2-4.**

---

## 3. Stack tecnológico recomendado

| Componente | Tecnología | Justificación |
|---|---|---|
| Lenguaje principal | Python 3.11+ | Estándar de facto en trading cuantitativo; ecosistema maduro |
| Conexión a Binance | `ccxt` + `python-binance` | `ccxt` para datos históricos multi-exchange (flexibilidad futura), `python-binance` para funcionalidades específicas de Binance (WebSockets, testnet) |
| Análisis de datos | `pandas`, `numpy` | Estándar para series temporales financieras |
| Indicadores técnicos | `pandas-ta` o `ta-lib` | Librerías robustas y mantenidas de indicadores (SMA, EMA, RSI, ATR, MACD, etc.) |
| Backtesting | `vectorbt` (vectorizado, rápido para optimización) + motor propio ligero para lógica custom | `vectorbt` permite miles de combinaciones de parámetros en segundos; motor propio para reglas complejas que combinan on-chain + precio |
| Dashboard (Fase 1-2) | `Streamlit` + `Plotly` | Desarrollo rápido, nativo en Python, ideal para MVP |
| Backend API (Fase 3, si aplica) | `FastAPI` | Para separar lógica de ejecución del dashboard cuando se vaya a producción |
| Base de datos | `SQLite` (Fase 1) → `PostgreSQL/TimescaleDB` (Fase 2+) | SQLite es suficiente y simple para desarrollo local; TimescaleDB para series temporales a escala |
| Programación de tareas | `APScheduler` | Ejecución periódica de recolección de datos y evaluación de estrategias |
| Notificaciones | `python-telegram-bot` | Alertas de señales, operaciones y errores |
| Gestión de configuración | Archivos `YAML` por estrategia | Permite versionar y compartir configuraciones de estrategias fácilmente |
| Testing | `pytest` | Unit tests de lógica de estrategia e integración con testnet |
| Entorno | `venv` o `poetry` | Aislamiento de dependencias |

---

## 4. Arquitectura general

```
┌─────────────────────────────────────────────────────────────────┐
│                        DASHBOARD (Streamlit)                     │
│   Config estrategias | Resultados backtest | Monitor en vivo     │
└───────────────────────────┬───────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                      CAPA DE ORQUESTACIÓN                        │
│         (Scheduler, gestor de estrategias, gestor de riesgo)     │
└──────┬──────────────┬──────────────┬──────────────┬─────────────┘
       │              │              │              │
┌──────▼─────┐ ┌──────▼──────┐ ┌─────▼──────┐ ┌─────▼──────────┐
│  DATA LAYER │ │  STRATEGY   │ │ BACKTEST   │ │  EXECUTION      │
│  (mercado + │ │  ENGINE     │ │ ENGINE     │ │  ENGINE         │
│  on-chain)  │ │ (entry/exit │ │ (métricas, │ │  (Binance API,  │
│             │ │  + riesgo)  │ │  walk-fwd) │ │  Fase 2-3)      │
└──────┬─────┘ └─────────────┘ └────────────┘ └─────────┬───────┘
       │                                                 │
┌──────▼────────────────────────────────────────────────▼───────┐
│                    ALMACENAMIENTO (SQLite/Postgres)             │
│      OHLCV | métricas on-chain | trades | resultados backtest   │
└───────────────────────────────────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │  Notificaciones  │
                    │    (Telegram)    │
                    └─────────────────┘
```

---

## 5. Estructura de carpetas propuesta

```
trading-quant-app/
├── config/
│   ├── strategies/              # YAML por estrategia
│   ├── settings.yaml            # Config general (DB, API endpoints, etc.)
│   └── .env.example             # Plantilla de variables de entorno
├── data_layer/
│   ├── market_data.py           # Ingesta OHLCV Binance (ccxt)
│   ├── onchain_data.py          # Ingesta datos on-chain (multi-fuente)
│   ├── data_sources/
│   │   ├── coinmetrics.py
│   │   ├── defillama.py
│   │   ├── blockchain_explorers.py
│   │   └── base_provider.py     # Interfaz abstracta para nuevas fuentes
│   └── storage.py               # ORM / acceso a DB
├── strategy_engine/
│   ├── base_strategy.py         # Clase abstracta Strategy
│   ├── conditions.py            # Bloques de condiciones reutilizables
│   ├── indicators.py            # Wrappers de indicadores técnicos
│   ├── onchain_signals.py       # Señales derivadas de datos on-chain
│   └── risk_management.py       # SL/TP fijo y dinámico, position sizing
├── backtest_engine/
│   ├── backtester.py            # Motor principal
│   ├── metrics.py               # Sharpe, Sortino, drawdown, etc.
│   ├── walk_forward.py          # Validación walk-forward / out-of-sample
│   └── optimizer.py             # Grid search / optimización de parámetros
├── execution_engine/             # Fase 2-3
│   ├── binance_client.py
│   ├── order_manager.py
│   └── kill_switch.py
├── notifications/
│   └── telegram_bot.py
├── dashboard/
│   ├── app.py                   # Entry point Streamlit
│   ├── pages/
│   │   ├── 1_backtest.py
│   │   ├── 2_strategy_config.py
│   │   ├── 3_live_monitor.py
│   │   └── 4_onchain_analysis.py
├── tests/
│   ├── test_strategies.py
│   ├── test_risk_management.py
│   └── test_backtester.py
├── scripts/
│   └── ingest_historical_data.py
├── requirements.txt
└── README.md
```

---

## 6. Módulo de datos

### 6.1 Datos de mercado (precio/volumen)
- **Fuente:** Binance API pública (klines/OHLCV) vía `ccxt`.
- **Timeframes soportados:** 1m, 5m, 15m, 1h, 4h, 1d (configurable).
- **Pares soportados:** configurable por estrategia (mínimo BTC/USDT, ETH/USDT).
- **Almacenamiento:** tabla `ohlcv(symbol, timeframe, timestamp, open, high, low, close, volume)`.
- **Actualización:** job periódico (APScheduler) que actualiza velas nuevas sin duplicar histórico.

### 6.2 Datos on-chain (Fase 1 — fuentes gratuitas)

| Métrica | Fuente propuesta (gratuita) | Notas |
|---|---|---|
| Capitalización de mercado de USDT (y otras stablecoins) | **DefiLlama Stablecoins API** (gratis, sin key) | Cobertura multi-chain, actualización diaria, muy confiable para este propósito específico |
| Market cap / supply de BTC | **CoinGecko API** (free tier) o **Coin Metrics Community API** | Rate limits en free tier; cachear localmente |
| Flujos de entrada/salida de exchanges (BTC) | Aproximación vía **direcciones conocidas de exchanges** usando **Blockchain.com API** (BTC) | Los datos *exactos* de netflow de exchanges (como los de CryptoQuant/Glassnode) están mayormente detrás de paywall. Para el MVP se aproxima trackeando wallets públicas conocidas de Binance/otros exchanges. Esto se documenta como limitación conocida. |
| Flujos de entrada/salida de exchanges (USDT/ETH-based) | **Etherscan API** (free tier, requiere key gratuita) sobre direcciones conocidas | Igual limitación que el punto anterior |
| Métricas generales on-chain BTC (hash rate, nº transacciones, direcciones activas) | **Blockchain.com Charts API** | Gratuita, sin key |

**Recomendación explícita:** documentar en el código que las métricas de netflow vía direcciones conocidas son una **aproximación**, y dejar la interfaz (`base_provider.py`) abstracta para poder enchufar **CryptoQuant o Glassnode** en la Fase 4 sin reescribir el motor de estrategias — el motor debe consumir un formato estándar `onchain_metric(name, timestamp, value)` sin importar la fuente.

### 6.3 Interfaz estándar de datos on-chain
Todas las fuentes deben normalizarse a una tabla única:
```
onchain_metrics(metric_name, symbol, timestamp, value, source)
```
Esto permite que el motor de estrategias sea agnóstico a la fuente de datos.

### 6.4 Persistencia y reutilización de datos históricos (estilo NinjaTrader 8)

Al igual que el gestor de datos históricos de NinjaTrader 8, **todo dato descargado se guarda de forma permanente en la base de datos local** y nunca se vuelve a descargar innecesariamente. Requisitos:

- **Descarga incremental:** al pedir un backtest sobre un símbolo/timeframe/rango de fechas, el sistema primero revisa qué datos ya existen localmente (precio y on-chain) y **solo descarga lo que falta** (nuevas velas, huecos detectados).
- **Detección de integridad (`data_integrity.py`):** valida continuidad de las series — detecta velas faltantes en el histórico de precio y gaps en las métricas on-chain — y permite forzar un backfill manual o automático.
- **Catálogo de datos disponibles:** página del dashboard que lista, por símbolo/timeframe/métrica on-chain: rango de fechas disponible localmente, fuente, fecha de última actualización y estado de integridad (completo / con huecos). Esto le da al usuario visibilidad total de qué puede simular sin esperar descargas, igual que el "Historical Data Manager" de NinjaTrader.
- **Inmutabilidad de datos para simulación:** una vez guardado un rango histórico, no se sobrescribe salvo refresco explícito — esto garantiza que dos backtests corridos en momentos distintos sobre el mismo rango produzcan resultados idénticos (reproducibilidad).

---

## 7. Framework de estrategias (condiciones configurables)

### 7.1 Filosofía de diseño
Cada estrategia se define como un **archivo YAML** con:
1. Metadatos (nombre, símbolo, timeframe).
2. **Condiciones de entrada** (lista de reglas combinables con AND/OR).
3. **Condiciones de salida** (independientes de SL/TP — ej. señal contraria, cruce inverso).
4. **Gestión de riesgo** (SL/TP fijo o dinámico, position sizing).
5. Parámetros de cada indicador/condición, todos editables desde el dashboard.

### 7.2 Ejemplo de configuración YAML

```yaml
strategy_name: "SMA_Cross_OnChain_Confirm"
symbol: "BTC/USDT"
timeframe: "4h"

entry_conditions:
  logic: "AND"
  rules:
    - type: "ma_cross"
      fast_period: 20
      slow_period: 50
      ma_type: "EMA"
      direction: "bullish"          # cruce alcista
    - type: "onchain_threshold"
      metric: "usdt_market_cap"
      condition: "increasing"
      lookback_days: 7
      min_change_pct: 1.5
    - type: "onchain_threshold"
      metric: "btc_exchange_netflow"
      condition: "below"
      value: 0                      # netflow negativo = salida de exchanges (acumulación)

exit_conditions:
  logic: "OR"
  rules:
    - type: "ma_cross"
      fast_period: 20
      slow_period: 50
      ma_type: "EMA"
      direction: "bearish"
    - type: "rsi_threshold"
      period: 14
      condition: "above"
      value: 75

risk_management:
  position_sizing:
    method: "fixed_fractional"      # o "fixed_amount", "kelly_fraction"
    risk_per_trade_pct: 1.0         # % del capital arriesgado por operación

  stop_loss:
    type: "dynamic"                 # "fixed" | "dynamic"
    fixed_pct: 2.0                  # usado si type = fixed
    dynamic_method: "atr"           # "atr" | "trailing_pct" | "chandelier"
    atr_period: 14
    atr_multiplier: 2.5

  take_profit:
    type: "dynamic"
    fixed_pct: 4.0
    dynamic_method: "risk_reward_ratio"
    risk_reward_ratio: 2.0
    partial_exits:                  # take profit escalonado (opcional)
      - level_pct: 2.0
        close_position_pct: 50
      - level_pct: 4.0
        close_position_pct: 50

  max_concurrent_positions: 1
  max_daily_loss_pct: 3.0           # kill switch diario
```

### 7.3 Bloques de condiciones a implementar (MVP)
**Basados en precio:**
- Cruce de medias móviles (SMA/EMA/WMA), configurable período rápido/lento.
- RSI (sobrecompra/sobreventa configurable).
- MACD (cruce de línea de señal).
- Bandas de Bollinger (ruptura/reversión).
- ATR (para volatilidad y sizing de SL).

**Basados en on-chain:**
- Umbral/variación de capitalización de mercado de USDT (proxy de liquidez entrando al mercado cripto).
- Netflow aproximado de exchanges (BTC/USDT) — positivo (entra a exchange, presión de venta) vs negativo (sale de exchange, acumulación).
- Combinaciones: ej. "solo entrar si hay cruce alcista de medias Y el netflow de exchanges es negativo Y el market cap de USDT está en aumento" (confirmación de liquidez).

**Requisito clave:** el sistema debe permitir **combinar libremente** condiciones de precio y on-chain con lógica AND/OR, y todos los valores numéricos (períodos, umbrales, porcentajes) deben ser **parámetros editables**, no hardcodeados.

### 7.4 Gestión de riesgo — Stop Loss / Take Profit

| Tipo | Método | Descripción |
|---|---|---|
| SL Fijo | % fijo desde entrada | Ej. 2% por debajo del precio de entrada |
| SL Dinámico — ATR | Múltiplo de ATR | `entrada - (ATR × multiplicador)` — se adapta a la volatilidad |
| SL Dinámico — Trailing | % o ATR trailing | Se mueve a favor del precio, nunca en contra |
| SL Dinámico — Chandelier Exit | Máximo N períodos - ATR × multiplicador | Técnica estándar de trailing stop robusto |
| TP Fijo | % fijo o ratio riesgo/beneficio | Ej. 2:1 sobre el riesgo del SL |
| TP Dinámico | Ratio riesgo/beneficio configurable + salidas parciales | Permite cerrar % de la posición en niveles escalonados |

**Position sizing** debe soportar al menos:
- Fixed fractional (arriesgar X% del capital por operación).
- Monto fijo en USDT.
- (Opcional, fase posterior) Kelly Criterion fraccionado.

---

## 8. Motor de backtesting

### 8.1 Requisitos funcionales
- Backtesting **vectorizado** (rápido, para explorar espacio de parámetros) y **event-driven** (para lógica compleja que combine múltiples fuentes de datos con dependencias temporales).
- Debe incorporar **comisiones de Binance** (spot: 0.1% estándar, configurable si hay BNB para descuento) y **slippage estimado** (configurable, ej. 0.05%).
- Soporte de **walk-forward analysis**: dividir el histórico en ventanas in-sample (optimización) y out-of-sample (validación), para evitar overfitting.
- Soporte de **optimización de parámetros** (grid search) con reporte de sensibilidad — advertir visualmente si un resultado "óptimo" es inestable ante pequeños cambios de parámetros (señal de overfitting).

### 8.2 Métricas de rendimiento obligatorias
- CAGR (retorno anualizado)
- Sharpe Ratio
- Sortino Ratio
- Calmar Ratio
- Máximo Drawdown (% y duración)
- Win Rate
- Profit Factor
- Expectancy por operación
- Número total de operaciones
- Exposición promedio (% del tiempo en mercado)
- Curva de equity (gráfico)
- Distribución de retornos por operación (histograma)
- Comparación vs Buy & Hold del mismo período

### 8.3 Validación robusta (anti-overfitting)
- División obligatoria del dataset: **train / validation / test** (ej. 60/20/20) o walk-forward rolling.
- Reporte de rendimiento **out-of-sample** siempre visible junto al in-sample — nunca mostrar solo el resultado optimizado.
- Test de robustez: variar cada parámetro ±20% y mostrar cómo cambia el resultado (heatmap de sensibilidad).

### 8.4 Persistencia y comparación de resultados (estilo NinjaTrader 8 — Strategy Analyzer)

Cada corrida de backtest **se guarda permanentemente**, no solo se muestra y se descarta — igual que el grid de resultados del Strategy Analyzer de NinjaTrader 8. Esquema de datos requerido:

- **`backtest_runs`**: `run_id`, `strategy_name`, `config_snapshot` (YAML completo usado en esa corrida, para reproducibilidad exacta), `symbol`, `timeframe`, `date_range_start`, `date_range_end`, `created_at`, y todas las métricas de la sección 8.2 como columnas indexadas (Sharpe, Sortino, drawdown, win rate, profit factor, CAGR, etc.) para poder ordenar y filtrar.
- **`backtest_trades`**: `run_id` (FK), `trade_id`, `entry_time`, `exit_time`, `side`, `entry_price`, `exit_price`, `quantity`, `pnl`, `pnl_pct`, `exit_reason` (TP / SL / señal de salida / fin del backtest), `duration`.
- **`backtest_equity_curve`**: `run_id` (FK), `timestamp`, `equity_value`, `drawdown_pct` — para graficar la curva de cualquier corrida guardada sin recalcular nada.

Funcionalidad del dashboard:
- Página **"Historial de Resultados"**: grilla con todas las corridas guardadas (comparable al grid de Strategy Analyzer), filtrable y ordenable por cualquier métrica.
- **Selección múltiple** para comparar curvas de equity de varias estrategias/configuraciones superpuestas en un mismo gráfico.
- **Reabrir** cualquier corrida guardada y ver su detalle completo (curva, lista de operaciones, configuración YAML exacta usada) sin volver a ejecutar el backtest.
- **Exportar** cualquier corrida a CSV/Excel.
- Posibilidad de **anotar/etiquetar** corridas (ej. "candidata a Fase 2", "descartada por overfitting") para llevar un registro de qué estrategias ya se evaluaron.

### 8.5 Replay visual de operaciones (opcional, mejora de UX)

Similar al gráfico del Strategy Analyzer de NinjaTrader: mostrar las velas de precio con marcadores de entrada/salida superpuestos, y una serie secundaria con la métrica on-chain relevante alineada en el tiempo — para inspeccionar visualmente por qué se disparó cada señal y facilitar el diagnóstico de una estrategia.

---

## 9. Módulo de ejecución (Fase 2-3)

- **Fase 2 (paper trading):** conexión a Binance Testnet, misma lógica de estrategia que en backtest, ejecución simulada con datos reales en tiempo real (WebSocket).
- **Fase 3 (trading real):**
  - API keys con **permiso de trading únicamente** (sin permiso de retiro), almacenadas en variables de entorno (`.env`), nunca hardcodeadas ni en el repositorio.
  - Reconciliación periódica entre el estado interno (posiciones esperadas) y el estado real de la cuenta en Binance.
  - **Kill switch:** detener todo trading automático si se supera `max_daily_loss_pct` o ante errores repetidos de conexión/ejecución.
  - Logging exhaustivo de cada orden enviada, confirmada, o rechazada.
  - Manejo de reconexión ante caídas de WebSocket.

---

## 10. Dashboard (Streamlit)

### Páginas mínimas del MVP:
1. **Backtest:** seleccionar estrategia (YAML), rango de fechas, símbolo/timeframe → correr backtest → visualizar curva de equity, métricas, tabla de operaciones, heatmap de sensibilidad de parámetros.
2. **Configuración de estrategias:** editor visual (formularios) que genera/edita el YAML sin tocar código — añadir/quitar condiciones, ajustar parámetros de SL/TP.
3. **Análisis on-chain:** visualización de series de tiempo de market cap de USDT, netflows aproximados, con overlay sobre el precio de BTC.
4. **Historial de Resultados** (ver sección 8.4): grid de todas las corridas de backtest guardadas, comparación de curvas de equity entre estrategias, reapertura de corridas pasadas, exportación a CSV.
5. **Catálogo de Datos Disponibles** (ver sección 6.4): qué símbolos/timeframes/métricas on-chain están descargados localmente, rango de fechas, estado de integridad y última actualización.
6. **Monitor en vivo** (Fase 2+): posiciones abiertas, P&L en tiempo real, histórico de señales generadas.

---

## 11. Notificaciones (Telegram)

Eventos que deben notificarse:
- Señal de entrada generada (aunque no se ejecute, en modo solo-análisis).
- Apertura y cierre de posición (con motivo: TP, SL, señal de salida).
- Activación del kill switch.
- Errores críticos (pérdida de conexión a Binance, fallo en ingesta de datos).

---

## 12. Seguridad

- API keys de Binance y de proveedores de datos: **solo en variables de entorno**, nunca en código ni en YAML de estrategias.
- En Fase 3, usar API keys con **whitelist de IP** y **sin permisos de retiro**, como mínimo indispensable.
- Modo testnet obligatorio antes de habilitar trading real (flag explícito `LIVE_TRADING_ENABLED=false` por defecto).
- Logs no deben imprimir claves ni secretos.

---

## 13. Testing

- Unit tests de cada condición de entrada/salida (`conditions.py`) con datos sintéticos controlados.
- Unit tests de cálculo de SL/TP fijo y dinámico.
- Test de integración: correr un backtest completo sobre un dataset fijo y verificar que las métricas coincidan con valores esperados (test de regresión).
- Test de conexión a Binance Testnet antes de cualquier despliegue a Fase 3.

---

## 14. Roadmap sugerido de desarrollo

| Sprint | Entregable |
|---|---|
| 1 | Data layer: ingesta y almacenamiento de OHLCV (Binance) + estructura de DB |
| 2 | Data layer: ingesta de on-chain (DefiLlama, CoinGecko, Blockchain.com) + normalización |
| 3 | Strategy engine: clase base, condiciones de precio (MA, RSI, MACD) |
| 4 | Strategy engine: condiciones on-chain + combinador de reglas AND/OR |
| 5 | Risk management: SL/TP fijo y dinámico, position sizing |
| 6 | Backtest engine: motor principal + métricas de rendimiento |
| 7 | Backtest engine: walk-forward + optimización + reporte de sensibilidad |
| 8 | Dashboard: página de backtest + configuración de estrategias |
| 9 | Dashboard: página de análisis on-chain |
| 10 | Testing exhaustivo + documentación + cierre de MVP (Fase 1) |
| 11+ | Fase 2: Testnet, paper trading, notificaciones Telegram |
| 14+ | Fase 3: trading en vivo, kill switch, monitoreo |

---

## 15. Criterios de aceptación del MVP (Fase 1)

- [ ] Se puede definir una estrategia completa (entrada, salida, SL/TP) mediante un archivo YAML sin tocar código.
- [ ] El dashboard permite editar todos los parámetros de una estrategia visualmente.
- [ ] El backtest incorpora al menos una condición de precio y una condición on-chain combinadas.
- [ ] El sistema reporta métricas out-of-sample, no solo optimizadas.
- [ ] El sistema soporta SL/TP fijo y al menos un método dinámico (ATR) simultáneamente comparables.
- [ ] Los datos on-chain (market cap USDT, netflow aproximado) se actualizan e integran correctamente al dataset de backtest.
- [ ] Los datos históricos de precio y on-chain se persisten permanentemente y no se vuelven a descargar salvo refresco explícito (descarga incremental funcionando).
- [ ] Cada corrida de backtest queda guardada de forma permanente (config, métricas, trades, curva de equity) y es reabrible desde el dashboard sin recalcular.
- [ ] El dashboard permite comparar al menos 2 corridas de backtest guardadas superponiendo sus curvas de equity.
- [ ] Cobertura de tests unitarios ≥ 70% en `strategy_engine` y `backtest_engine`.

---

## 16. Dependencias iniciales (`requirements.txt` sugerido)

```
ccxt
python-binance
pandas
numpy
pandas-ta
vectorbt
streamlit
plotly
sqlalchemy
apscheduler
python-telegram-bot
pyyaml
python-dotenv
pytest
requests
```

---

## 17. Limitaciones conocidas (documentar en README)

- Los datos de netflow de exchanges vía direcciones conocidas son una **aproximación**, no datos oficiales de exchange como los de CryptoQuant/Glassnode/Nansen (que requieren suscripción de pago). Se documenta como mejora futura (Fase 4).
- Las APIs gratuitas (CoinGecko, Etherscan) tienen rate limits — el sistema debe cachear agresivamente y manejar reintentos con backoff exponencial.
- El backtesting histórico no garantiza resultados futuros; el sistema debe mostrar advertencias de riesgo visibles en el dashboard.
