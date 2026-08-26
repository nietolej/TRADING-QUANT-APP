# 📘 TRADING QUANT APP — Manual de Usuario Oficial
**Terminal Cuantitativo Profesional de Análisis, Backtesting y Optimización de Estrategias**

---

## 📑 Tabla de Contenidos
1. [Introducción y Arquitectura General](#1-introducción-y-arquitectura-general)
2. [Estructura de Navegación (Left Drawer)](#2-estructura-de-navegación-left-drawer)
3. [Módulo 1: Strategy Builder (Constructor de Estrategias)](#3-módulo-1-strategy-builder-constructor-de-estrategias)
4. [Módulo 2: Estrategias Guardadas (Catálogo)](#4-módulo-2-estrategias-guardadas-catálogo)
5. [Módulo 3: Strategy Analyzer (Laboratorio de Backtesting)](#5-módulo-3-strategy-analyzer-laboratorio-de-backtesting)
6. [Módulo 4: Optimizador de Estrategias (Grid Search)](#6-módulo-4-optimizador-de-estrategias-grid-search)
7. [Módulo 5: Analizador Cuantitativo de Robustez & Mesetas](#7-módulo-5-analizador-cuantitativo-de-robustez--mesetas)
8. [Módulo 6: Historial de Backtests & Portafolios](#8-módulo-6-historial-de-backtests--portafolios)
9. [Módulo 7: Datos Almacenados (Market Data Manager)](#9-módulo-7-datos-almacenados-market-data-manager)
10. [Módulo 8: Machine Learning & Filtro MLE](#10-módulo-8-machine-learning--filtro-mle)
11. [Módulo 9: Live Monitor (Monitoreo en Vivo)](#11-módulo-9-live-monitor-monitoreo-en-vivo)
12. [Preguntas Frecuentes y Mejores Prácticas Cuantitativas](#12-preguntas-frecuentes-y-mejores-prácticas-cuantitativas)

---

## 1. Introducción y Arquitectura General

**TRADING QUANT APP** es una plataforma de análisis y desarrollo de algoritmos de trading cuantitativo de alto rendimiento. Combina una interfaz gráfica interactiva moderna construida con **NiceGUI (Tailwind CSS / Quasar)** con un motor de simulación ultra-rápido vectorizado (**VectorBT**) y un motor iterativo vela por vela para trailing stops y órdenes complejas.

### 🌟 Principios Fundamentales
- **Separación Lógica / UI**: Los cálculos pesados y backtests se ejecutan de forma asíncrona y multihilo en segundo plano sin congelar la interfaz.
- **Sin Credenciales Expuestas**: Todas las claves API se gestionan mediante variables de entorno (`.env`).
- **Almacenamiento Local Eficiente**: Almacena datos históricos en SQLite/Parquet para no exceder los límites de las APIs de los exchanges.
- **Control de Fricción Realista**: Modela comisiones por operación y deslizamiento (*slippage*) configurable.

---

## 2. Estructura de Navegación (Left Drawer)

El menú vertical lateral izquierdo te permite alternar instantáneamente entre los diferentes módulos sin perder el estado de tu sesión de trabajo:

```text
├── PRINCIPAL
│   ├── Strategy Builder        (Diseño visual de reglas, indicadores y SL/TP)
│   ├── Estrategias Guardadas   (Catálogo YAML de estrategias creadas)
│   ├── Strategy Analyzer       (Laboratorio de backtesting, gráficos y trades)
│   ├── Optimizador (Grid)      (Búsqueda en cuadrícula y análisis de robustez)
│   └── Historial Backtests     (Registro de backtests y combinación de portafolios)
└── AVANZADAS
    ├── Datos Almacenados       (Gestor y visor de datos de mercado OHLCV)
    ├── Machine Learning        (Modelos predictivos y características técnicas)
    ├── Filtro MLE              (Termómetro de régimen de volatilidad y liquidez)
    └── Live Monitor            (Monitoreo en tiempo real de señales de mercado)
```

---

## 3. Módulo 1: Strategy Builder (Constructor de Estrategias)

El **Strategy Builder** te permite diseñar estrategias cuantitativas complejas sin escribir código de bajo nivel.

### 🛠️ Pasos para crear una estrategia:
1. **Metadatos Básicos**:
   - **Nombre de la Estrategia**: Identificador único (ej. `Ema_Cross_BTC`).
   - **Dirección Operativa**: `Long` (alcista), `Short` (bajista) o `Both` (ambas direcciones).
   - **Temporalidad Base**: Selecciona `1d` (diario por defecto), `1h`, `15m`, etc.
2. **Definición de Parámetros**:
   - Agrega parámetros dinámicos con nombres y valores por defecto (ej. `FAST=1`, `LOW=35`, `RSI_PERIOD=14`). Estos parámetros podrán ser optimizados posteriormente en el **Grid Search**.
3. **Reglas de Entrada (Entry Conditions)**:
   - Configura la lógica combinatoria (`AND` / `OR`).
   - Elige indicadores técnicos (`EMA`, `SMA`, `RSI`, `MACD`, `Bollinger`, `ATR`, `SuperTrend`) y operadores (`crosses_above`, `crosses_below`, `>`, `<`, `==`).
4. **Reglas de Salida (Exit Conditions)**:
   - Condiciones técnicas para cerrar la posición de mercado.
5. **Gestión de Riesgo Avanzada (Stop Loss & Take Profit)**:
   - **Tipos de Stop Loss**:
     - `Fijo (%)`: Porcentaje fijo desde el precio de entrada.
     - `Trailing Stop (%)`: Sigue al precio dinámicamente asegurando ganancias.
     - `Break-Even`: Mueve el SL al precio de entrada tras alcanzar un % de ganancia predefinido y luego activa trailing.
     - `ATR Volatility`: Basado en múltiplos de volatilidad ($K \times \text{ATR}$).
     - `Chandelier Exit`: Nivel $High_N - K \times \text{ATR}$.
     - `Swing Low / High`: Nivel basado en los mínimos/máximos de las últimas $N$ velas.
   - **Tipos de Take Profit**:
     - `Fijo (%)`: Cierre total al alcanzar un objetivo porcentual.
     - `Risk-Reward Ratio`: Múltiplo exacto del riesgo asumido ($RR \times \text{Riesgo SL}$).
     - `ATR Target`: Objetivo basado en la volatilidad ($M \times \text{ATR}$).
     - `Parcial (Multi-TP)`: Cierres escalonados (50% en TP1, 50% en TP2).
6. **Guardar Estrategia**:
   - Haz clic en **Guardar Estrategia**. Se serializará como un archivo `.yaml` estándar dentro de `config/strategies/`.

---

## 4. Módulo 2: Estrategias Guardadas (Catálogo)

Permite visualizar todas las estrategias almacenadas en el sistema.

- **Acciones Disponibles**:
  - **Editar**: Abre la estrategia en el *Strategy Builder* para modificar sus reglas o parámetros.
  - **Analizar**: Envía la estrategia directamente al *Strategy Analyzer* para ejecutar un backtest inmediato.
  - **Eliminar**: Borra el archivo YAML del catálogo con confirmación de seguridad.

---

## 5. Módulo 3: Strategy Analyzer (Laboratorio de Backtesting)

El laboratorio principal para poner a prueba cualquier estrategia con datos históricos reales.

### ⚙️ Configuración del Backtest:
- **Activo / Par**: Selecciona entre `BTC/USDT`, `ETH/USDT`, `SOL/USDT`, etc.
- **Temporalidad**: `1d` (diario por defecto), `4h`, `1h`, `15m`, etc.
- **Rango de Fechas**: Fechas de inicio y fin del análisis.
- **Capital Inicial y Tipo de Activo**:
  - `BTC (BASE)`: La cuenta inicia con unidades de la criptomoneda base (ej. `1.0 BTC`).
  - `USDT (CITA)`: La cuenta inicia con stablecoin (ej. `10,000 USDT`).
- **Modo de Tamaño de Posición (Sizing)**:
  - `Interés Compuesto (100% Capital)`: Reinvierte el 100% de la equidad acumulada en cada trade.
  - `Monto Fijo por Operación`: Asigna un monto constante (en BTC o USDT) por trade.
  - `Riesgo Fijo (1% por trade)`: Calcula el tamaño de posición según la distancia al Stop Loss para arriesgar exactamente el 1% de la cuenta.
- **Fricción**: Configura la **Comisión (%)** del exchange (ej. 0.1%) y el **Slippage (%)** (ej. 0.05%).

### 🛡️ Filtro de Curva de Capital (Equity Curve Filter):
Permite simular el sistema **Virtual vs Real**:
- **Filtro de Drawdown**: Si la estrategia virtual entra en un Drawdown superior al umbral configurado (ej. 30%), el sistema **detiene las operaciones reales** para proteger el capital y continúa operando en virtual hasta que la curva de capital se recupera.
- **Filtro de Pérdidas Consecutivas**: Pausa las operaciones reales tras $N$ pérdidas consecutivas.

### 📊 Gráficos y Métricas Cuantitativas:
1. **Gráfico de Velas Japonesas**: Muestra las velas OHLCV con las señales de entrada (flechas verdes) y salida (flechas rojas/azules), SL dinámico y TP.
2. **Curva de Equidad Comparativa**: Curva de capital de la Estrategia Real vs Estrategia Virtual vs Buy & Hold (Hold pasivo de Bitcoin).
3. **Underwater Drawdown**: Gráfico de profundidad y duración de caídas temporales de capital.
4. **Métricas Clave**: Ratio de Sharpe, Sortino, Calmar, CAGR (%), Max Drawdown (%), Win Rate (%), Profit Factor, Esperanza Matemática.
5. **Tabla de Operaciones (Trade Log)**: Detalle trade por trade con fecha de entrada/salida, precio, PnL %, PnL absoluto y motivo de salida (SL, TP, Señal técnica).

---

## 6. Módulo 4: Optimizador de Estrategias (Grid Search)

Módulo dedicado a la exploración masiva de combinaciones de parámetros para encontrar la configuración matemática óptima.

### 🚀 Cómo utilizar el Optimizador:
1. Selecciona la **Estrategia**, el **Activo**, la **Temporalidad** y el **Período**.
2. **Definir Rangos de Parámetros**:
   - Para cada parámetro, ingresa su **Mínimo**, **Máximo** y **Paso (Step)**.
   - El sistema calcula y previsualiza en tiempo real los valores a probar y el **total de combinaciones**.
3. **Métrica Objetivo**: Elige el criterio de ordenamiento:
   - `Coeficiente de Sharpe` (Retorno ajustado al riesgo).
   - `CAGR (%)` (Tasa de crecimiento anual compuesto).
   - `PnL Neto` (Beneficio monetario total).
   - `Profit Factor` (Ganancias brutas / Pérdidas brutas).
   - `Menor Max Drawdown` (Minimizar caídas de capital).
   - `Win Rate (%)` (Porcentaje de aciertos).
4. Haz clic en **🚀 INICIAR OPTIMIZADOR**.
   - Se ejecuta en segundo plano mediante un pool multihilo seguro.
   - La barra de progreso te indica el % de avance y el tiempo estimado.

---

## 7. Módulo 5: Analizador Cuantitativo de Robustez & Mesetas

Este módulo se activa automáticamente al finalizar la optimización para responder la pregunta más crítica del trading algorítmico: **¿Esta estrategia está sobreajustada (Overfitted) o funcionará en el mercado real?**

### 🧠 Componentes del Análisis:

#### A. 🛡️ Configuración Más Robusta (Meseta) vs Pico Aislado (#1)
- **El Peligro del Pico (#1)**: El mejor resultado numérico suele ser un punto aislado donde una pequeña variación de mercado causa pérdidas catastróficas.
- **La Meseta Robusta (Recomendada)**: El algoritmo analiza los vecinos ($\pm 1$ paso) de cada punto en el espacio multidimensional. Selecciona el punto central cuya **media de vecinos es más alta y cuya desviación estándar ($\sigma$) es mínima**.
- **Botón `Aplicar Configuración Robusta`**: Carga directamente los parámetros confiables en el *Strategy Analyzer*.

#### B. 📊 Sensibilidad e Importancia de Parámetros (% ANOVA)
- Descompone la varianza total de los resultados para determinar qué parámetro tiene mayor impacto en el rendimiento.
- **Ejemplo**: Si `SL` tiene **82.7%** de influencia y `FAST` tiene **0.0%**, sabrás que el éxito de la estrategia depende del control de riesgo y no de la rapidez de la media móvil.

#### C. 📈 Curva de Tendencia y Banda de Dispersión ($\mu \pm 1\sigma$)
- Permite seleccionar cualquier parámetro y ver la curva del **PnL Medio ($\mu$)** junto con una **banda sombreada ($\pm 1\sigma$)**.
- **Interpretación**:
  - *Banda estrecha*: Alta consistencia y bajo riesgo.
  - *Banda ancha*: Alta incertidumbre y fuerte dependencia de combinaciones cruzadas.

#### D. 📦 Diagrama de Cajas y Bigotes (Box Plot)
- Muestra los cuartiles ($Q_1, Q_2, Q_3$), medianas y casos atípicos (*outliers*) para cada valor probado del parámetro.

#### E. 🎯 Índice Global de Robustez (Score 0 - 100)
- **80 - 100 (🟢 Alta Robustez)**: Meseta amplia y estable, baja dispersión, excelente tolerancia a cambios de régimen.
- **60 - 79 (🟡 Robustez Moderada)**: Meseta estable, pero se debe operar estrictamente en la zona recomendada.
- **< 40 (🔴 Frágil / Riesgo de Overfitting)**: Resultados basados en picos aislados; alta probabilidad de fallar fuera de muestra.

---

## 8. Módulo 6: Historial de Backtests & Portafolios

- **Historial Completo**: Cada backtest guardado almacena sus parámetros, activo, temporalidad, curva de equidad y métricas completas en la base de datos local.
- **Simulador de Portafolios**: Permite seleccionar 2 o más estrategias guardadas y simular una **estrategia de portafolio combinada**, calculando la descorrelación y la reducción del drawdown conjunto.

---

## 9. Módulo 7: Datos Almacenados (Market Data Manager)

- Permite descargar y almacenar velas históricas desde Binance / CCXT para cualquier par de criptomonedas y temporalidad.
- Optimiza el uso de la API y garantiza que los backtests y optimizaciones masivas se ejecuten a máxima velocidad desde el almacenamiento local.

---

## 10. Módulo 8: Machine Learning & Filtro MLE

- **Machine Learning**: Entrenamiento de clasificadores y regresores para filtrar señales falsas y predecir la dirección del siguiente bloque de velas.
- **Termómetro MLE (Maximum Likelihood Estimation)**: Monitorea el régimen del mercado (Tendencia fuerte vs Rango / Ruido) para activar o desactivar estrategias según las condiciones macro.

---

## 11. Módulo 9: Live Monitor (Monitoreo en Vivo)

- Conecta las estrategias creadas a los feeds de datos en tiempo real mediante WebSockets.
- Genera alertas visuales y sonoras cada vez que se cumplen las condiciones de entrada o salida configuradas en el *Strategy Builder*.

---

## 12. Preguntas Frecuentes y Mejores Prácticas Cuantitativas

### ❓ ¿Por qué mi estrategia tiene un Win Rate bajo (ej. 25%) pero es altamente rentable?
En estrategias de seguimiento de tendencia (*Trend Following*), la asimetría positiva es la clave: las pérdidas son pequeñas y controladas (gracias al Stop Loss), mientras que las operaciones ganadoras dejan correr la tendencia capturando beneficios de $3\times$, $5\times$ o $10\times$ el riesgo inicial.

### ❓ ¿Debo usar siempre el resultado #1 del Optimizador?
**No**. Siempre revisa el **Analizador de Robustez**. Si el resultado #1 es un pico aislado y sus vecinos sufren pérdidas, esa configuración tiene alto riesgo de sobreajuste. Utiliza siempre la **Configuración Más Robusta (Meseta)** recomendada por la plataforma.

### ❓ ¿Qué temporalidad se recomienda para empezar?
Por defecto, la plataforma utiliza la temporalidad **Diaria (`1d`)** en todos sus módulos, ya que ofrece señales con menor ruido de mercado, menores costos por comisiones (*fees*) y tendencias más sólidas.

---
*TRADING QUANT APP — Desarrollado para Traders e Investigadores Cuantitativos.*
