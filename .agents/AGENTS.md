# Reglas del Proyecto TRADING-QUANT-APP

Estas reglas definen los estándares de desarrollo y arquitectura para el agente de IA al trabajar en este proyecto.

## 1. Stack Tecnológico Principal
- **Interfaz Gráfica:** Usar exclusivamente `NiceGUI`. Utilizar las clases utilitarias de Tailwind (ej. `.classes('w-full items-center')`) integradas en NiceGUI para el estilo.
- **Visualización:** Usar `plotly` para todos los gráficos financieros y estadísticos, renderizándolos mediante `ui.plotly()`.
- **Manejo de Datos:** Usar `pandas` y `numpy` para la manipulación y transformación de datos.
- **Análisis y Backtesting:** Usar `vectorbt` y la librería `ta` para el cálculo de indicadores técnicos y simulaciones rápidas vectorizadas.
- **Machine Learning:** Usar `scikit-learn` para modelos predictivos (clasificación/regresión) y pipelines de preprocesamiento.

## 2. Arquitectura y Código Limpio
- **Separación de Responsabilidades:** Mantener la lógica de negocio (conexiones a APIs, cálculos pesados, modelos) estrictamente separada de la capa de interfaz. Los archivos en `web_gui/` o `dashboard/` deben limitarse a presentar los datos y manejar la interacción del usuario.
- **Tipado Estático:** Usar *Type Hints* (anotaciones de tipos) en la firma de todas las funciones y métodos nuevos para mejorar la legibilidad y detección de errores.
- **Logging vs Print:** Evitar el uso de `print()` para depuración en código de producción. Utilizar la librería estándar `logging` para registrar eventos, advertencias y errores (crítico para monitorear ejecuciones asíncronas).

## 3. Diseño de la Interfaz (NiceGUI)
- **Modularidad Visual:** Encapsular componentes UI complejos en funciones o clases separadas para que los archivos principales de las páginas no se vuelvan monolíticos.
- **UX en Operaciones Pesadas:** Al descargar datos históricos de exchanges o ejecutar backtests masivos, mostrar indicadores de carga (ej. `ui.spinner()` o barras de progreso) e implementar operaciones asíncronas (`async/await`) para no congelar la pantalla web.

## 4. Gestión de Datos y APIs
- **Seguridad:** Nunca colocar credenciales (API Keys, Secrets) directamente en el código fuente. Leerlas siempre a través de variables de entorno (usando `python-dotenv` y el archivo `.env`).
- **Eficiencia:** Cuando se descarguen datos de mercado históricos masivos (Binance, CCXT), implementar mecanismos de almacenamiento local temporal (archivos parquet o csv) o caché en memoria para no exceder los límites de la API de los exchanges.

## 5. Motor de Estrategias
- Toda nueva estrategia cuantitativa a desarrollar debe integrarse respetando la arquitectura existente en `strategy_engine/` (ej. heredando de clases base, si existen) y la estructura de configuración definida en `config/strategies/`, permitiendo que el motor de evaluación (`backtester.py`) la lea de forma estándar.
