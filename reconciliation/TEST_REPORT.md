# Informe de Pruebas — Módulo de Conciliación (App ↔ Binance)

**Fecha de ejecución:** 2026-09-19 (UTC)
**Módulo:** `reconciliation/` (`reconciler.py`, `order_ledger.py`, `models.py`)
**Entorno de ejecución:** contenedor efímero de Claude Code (sandbox de desarrollo), sin
credenciales de Binance configuradas (`BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_SECRET_KEY`
ausentes) y con salida de red restringida por el proxy del entorno.
**Base de datos:** SQLite nueva (`data/trading_quant.db`), creada en este mismo entorno para
la prueba — sin historial previo de órdenes ni conciliaciones.

> **Nota de actualización (misma fecha):** a partir de la sección "Actualización — Test 3
> rediseñado + Reporte de Tests" (al final de este documento), Test 3 dejó de ser el botón
> genérico "Conciliar ahora" y pasó a ser una prueba propia
> (`OrderReconciler.run_execution_verification_test`) que crea, envía y verifica la ejecución
> real de una orden en Binance, con un Reporte de Tests dedicado en la UI. El resto de este
> informe (Test 1 y Test 2) sigue vigente sin cambios.

Este informe documenta la ejecución real de los tres flujos de prueba que expone el módulo de
conciliación: **Test 1** (ciclo completo inmediato), **Test 2** (monitoreo en vivo de un bot) y
**Test 3** (conciliación general / botón "Conciliar ahora", que incluye la detección de huérfanos).
Los tres se ejecutaron invocando directamente las mismas funciones que usa la UI
(`web_gui/pages/reconciliation_page.py`), para que el resultado sea el código de producción
real y no una simulación aparte.

---

## Test 1 — Ciclo Completo Inmediato

### Qué se probó
Que el módulo pueda ejecutar de punta a punta el mismo pipeline que usan los bots en producción:
1. Obtener el precio de referencia del símbolo.
2. Enviar una orden de entrada real MARKET a Binance Futures Testnet.
3. Colocar Take Profit y Stop Loss condicionales (el punto donde vivió el bug de `algoId` del
   18/09, según el comentario del propio código).
4. Cancelar las órdenes condicionales.
5. Cerrar la posición de prueba con una orden MARKET `reduceOnly`.
6. Conciliar de inmediato esas mismas órdenes contra Binance.

### Cómo se probó
Se instanció `OrderReconciler(use_testnet=True)` y se invocó directamente
`run_full_cycle_test(symbol="BTC/USDT", quantity=0.001)` — la misma llamada que dispara el botón
"▶ Ejecutar Test 1" de la página de Conciliación. Por diseño, este test está bloqueado para
correr solo contra Testnet, nunca contra Mainnet.

### Resultado obtenido
```json
{
  "success": false,
  "steps": [
    {
      "step": "Precio de referencia",
      "ok": false,
      "detail": "No se pudo obtener el precio actual del símbolo."
    }
  ],
  "reconciliation": []
}
```
El test se detuvo en el primer paso. Al investigar la causa raíz directamente sobre
`BinanceTestnetClient`, la excepción real (silenciada por `get_symbol_price`, que por diseño
devuelve `0.0` en vez de propagar el error) fue:

```
ProxyError(MaxRetryError("HTTPSConnectionPool(host='testnet.binancefuture.com', port=443):
Max retries exceeded ... Tunnel connection failed: 403 Forbidden"))
```

### Qué significa el resultado
No es un fallo del código de conciliación ni del pipeline de trading: es una **restricción de
red del entorno de pruebas** (el proxy saliente del sandbox bloquea con 403 el host
`testnet.binancefuture.com`). El test nunca llegó a intentar enviar una orden real. Como
hallazgo secundario de robustez: `get_symbol_price()` traga la excepción de red y devuelve
`0.0`, lo cual es correcto para no romper el flujo, pero hace que la UI solo muestre "No se pudo
obtener el precio actual del símbolo" sin indicar si la causa es de red, de credenciales o de
símbolo inválido — vale la pena registrar el detalle de la excepción en el step para facilitar
el diagnóstico en producción.

**Conclusión:** Test 1 no pudo validarse en este entorno por falta de conectividad saliente
hacia Binance. Debe re-ejecutarse en un entorno con acceso de red a Binance Testnet y
credenciales `BINANCE_TESTNET_API_KEY`/`BINANCE_TESTNET_SECRET_KEY` configuradas para obtener
un veredicto real sobre el pipeline de órdenes + SL/TP.

---

## Test 2 — Monitoreo en Vivo de un Bot

### Qué se probó
Que el módulo pueda listar los bots Testnet activos (`test2_bot_select`) y, una vez
seleccionado uno, ejecutar conciliaciones periódicas automáticas (`ui.timer` cada N segundos)
mientras el bot opera en vivo, alertando discrepancias sin intervención manual.

### Cómo se probó
Se invocó `daemon_client.get_all_bots()` — el mismo paso que ejecuta la página al cargar
(`_refresh_test2_bot_options`) para poblar el selector de bots — como precondición necesaria
antes de poder iniciar el monitoreo.

### Resultado obtenido
```
ModuleNotFoundError: No module named 'ta'
```
al importar la cadena `daemon_client → bot_manager → PaperTrader → BaseStrategy →
ConditionEvaluator → ta` (librería de indicadores técnicos). Este error ocurrió incluso después
de instalar `pandas`/`numpy` manualmente; la causa es que este entorno aislado no tiene
instalado el stack completo de dependencias de la aplicación (`requirements.txt`), y no hay
ningún bot corriendo en él (el daemon de ejecución no está activo aquí).

### Qué significa el resultado
Test 2 depende de infraestructura que **no existe en un entorno de pruebas aislado**: necesita
la aplicación completa desplegada (con todas sus dependencias) y al menos un bot en ejecución
sobre Binance Testnet para tener algo que monitorear. No se trata de un defecto del módulo de
conciliación, sino de una limitación del entorno donde se generó este informe.

**Conclusión:** Test 2 no pudo ejecutarse de forma significativa aquí. Para validarlo se
necesita correr contra la instancia real desplegada de la app (con `requirements.txt`
instalado y al menos un bot activo en Testnet), no en este sandbox de desarrollo.

---

## Test 3 — Conciliación General ("Conciliar ahora")

### Qué se probó
El flujo estándar de conciliación bajo demanda: revisar el ledger local de órdenes pendientes de
conciliar (`reconcile_pending`) y, por cada símbolo indicado, buscar órdenes "huérfanas"
ejecutadas en Binance con la etiqueta de la app (`QTAPP_`) que no tengan registro local
(`find_orphan_trades`).

### Cómo se probó
Se creó primero el esquema de base de datos (`app_order_ledger`, `reconciliation_log`) — ya que
el entorno partía de una base de datos vacía — y luego se invocó directamente
`OrderReconciler(use_testnet=True).run(symbols=["BTCUSDT", "ETHUSDT"], lookback_hours=24)`, la
misma llamada que dispara el botón "Conciliar ahora" de la UI.

### Resultado obtenido
```json
{
  "network": "Binance Futures Testnet (Demo)",
  "lookback_hours": 24,
  "symbols": ["BTCUSDT", "ETHUSDT"],
  "total_checked": 2,
  "summary": { "ERROR": 2 },
  "critical_count": 0,
  "results": [
    {
      "symbol": "BTCUSDT",
      "match_status": "ERROR",
      "severity": "WARNING",
      "details": "No se pudo consultar el historial de órdenes de Binance: API Secret required for private endpoints"
    },
    {
      "symbol": "ETHUSDT",
      "match_status": "ERROR",
      "severity": "WARNING",
      "details": "No se pudo consultar el historial de órdenes de Binance: API Secret required for private endpoints"
    }
  ]
}
```
`reconcile_pending` no encontró órdenes pendientes (ledger vacío, esperado en una base de datos
nueva) y `find_orphan_trades` falló al llamar a `futures_get_all_orders` (endpoint firmado/privado)
por no haber credenciales configuradas.

### Qué significa el resultado
El código del flujo general se ejecutó **correctamente de principio a fin**: no hubo excepciones
sin manejar, la orquestación `run()` combinó bien los resultados de ledger + huérfanos por
símbolo, y ante la falta de credenciales devolvió un estado controlado (`ERROR`/`WARNING`, no
`CRITICAL`) con un mensaje claro en vez de reventar. Esto confirma que el manejo de errores de
`find_orphan_trades` funciona como está diseñado: cuando Binance rechaza la consulta, el
resultado queda marcado como `ERROR` con severidad `WARNING` en lugar de interpretarse como una
discrepancia real (`ORPHAN_ON_BINANCE`/`CRITICAL`), evitando falsas alarmas.

**Conclusión:** Test 3 quedó validado en su lógica de orquestación y manejo de errores. Para
obtener un veredicto sobre discrepancias reales (huérfanos, mismatches de cantidad/precio) hace
falta repetirlo con credenciales de Binance Testnet válidas y, opcionalmente, con órdenes ya
existentes en el ledger local.

---

## Resumen ejecutivo

| Test | Se ejecutó código real | Resultado | Causa | Requiere para validar |
|---|---|---|---|---|
| Test 1 — Ciclo completo | Sí (hasta paso 1 de 6) | Bloqueado | Red del sandbox no llega a `testnet.binancefuture.com` (403 del proxy) | Entorno con salida a Binance + credenciales Testnet |
| Test 2 — Monitoreo en vivo | No (falla en la precondición) | Bloqueado | Faltan dependencias del stack completo (`ta`, etc.) y no hay bots corriendo | App completa desplegada + bot activo en Testnet |
| Test 3 — Conciliación general | Sí (completo) | OK (manejo de errores correcto) | Sin credenciales, Binance rechaza el endpoint firmado; el módulo lo reporta como `WARNING`, no como discrepancia crítica | Credenciales Testnet válidas para ver resultados `MATCHED`/`ORPHAN_ON_BINANCE` reales |

**Hallazgo principal:** ninguno de los tres tests reveló un bug en la lógica de conciliación
propiamente dicha. Las limitaciones encontradas son todas del **entorno de sandbox** (sin
credenciales, sin red hacia Binance, sin dependencias completas ni bots activos), no del
código en `reconciliation/`. El único punto de mejora identificado es de observabilidad: en
`execution_engine/binance_client.py::get_symbol_price`, la excepción real (por ejemplo, un error
de red) se descarta y se devuelve `0.0`, lo que dificulta distinguir en la UI "sin precio porque
no hay red" de "sin precio porque el símbolo es inválido".

**Recomendación:** repetir Test 1 y Test 2 en la instancia real desplegada de la app (con
`BINANCE_TESTNET_API_KEY`/`BINANCE_TESTNET_SECRET_KEY` configuradas y, para Test 2, con al menos
un bot corriendo en Testnet) para obtener un veredicto funcional completo del pipeline de
órdenes y del monitoreo automático.

---

## Actualización — Test 3 rediseñado + Reporte de Tests

**Fecha:** 2026-09-19 (UTC), misma sesión.

### Qué se probó
A pedido, Test 3 dejó de ser el botón genérico "Conciliar ahora" y pasó a validar
explícitamente, orden por orden, las tres condiciones pedidas:
1. que la orden haya quedado **creada en el ledger de la app** (`app_order_ledger`),
2. que se haya **enviado** a Binance (status `SENT_OK` + `binance_order_id`), y
3. que Binance confirme que **se ejecutó realmente** (estado `FILLED` al reconciliar).

Además se agregó un **Reporte de Tests** (`OrderReconciler.build_test_report`, con acceso
propio "📊 Reporte de Tests" en la página de Conciliación) que muestra, para cada orden
generada por Test 1/2/3: símbolo, LONG/SHORT, acción, tipo, cantidad solicitada/ejecutada,
precio de referencia/ejecutado, **% de deslizamiento**, estado de conciliación, severidad y
detalle — con contadores agregados de órdenes efectivas, fallidas y fallidas específicamente
por deslizamiento de precio.

### Cómo se probó
1. **Nueva función `run_execution_verification_test`** (`reconciliation/reconciler.py`): abre
   una orden MARKET real en Testnet, reconcilia de inmediato (confirma creación+envío+ejecución
   y calcula el deslizamiento) y cierra la posición de prueba. Se invocó directamente contra
   este entorno (`OrderReconciler(use_testnet=True).run_execution_verification_test(...)`).
2. **Cálculo de deslizamiento**: se añadió `reference_price` (precio de mercado tomado justo
   antes de enviar la orden, también para órdenes MARKET) al ledger (`AppOrderRecord`) y a cada
   fila del historial de conciliación (`ReconciliationRecord`), junto con `slippage_pct` y un
   nuevo estado `SLIPPAGE_EXCEEDED` (severidad `WARNING`/`CRITICAL` según la magnitud) cuando el
   deslizamiento supera la tolerancia configurada (0.5% por defecto).
3. **Migración de esquema**: como `app_order_ledger`/`reconciliation_log` ya podían existir de
   una versión anterior, se agregó una migración aditiva (`reconciliation/order_ledger.py:
   _ensure_columns`) que agrega las columnas nuevas vía `ALTER TABLE` si faltan. Se validó
   recreando manualmente el esquema "viejo" (sin las columnas nuevas) y confirmando que, al
   importar el módulo, las columnas aparecen correctamente en `PRAGMA table_info`.
4. **Validación del cálculo de slippage y del reporte**: dado que este entorno no tiene
   credenciales de Binance (mismo hallazgo que en la sección anterior de este informe), se
   simuló la respuesta de Binance (`futures_get_order`) para dos órdenes ya "enviadas" por la
   app — sin tocar la lógica de negocio real, solo la llamada de red — y se corrió la
   reconciliación y el reporte de punta a punta.

### Resultado obtenido
- **`run_execution_verification_test` en este entorno:** se detiene en el paso 1
  ("API Key o Secret no configuradas en .env"), consistente con el hallazgo ya documentado
  arriba: este sandbox no tiene credenciales de Binance. El código en sí corrió sin errores.
- **Validación simulada de slippage y reporte** (2 órdenes: una LONG BTCUSDT con 0.083% de
  deslizamiento, una SHORT ETHUSDT con 3.333% de deslizamiento):
```json
{
  "lookback_hours": 1.0,
  "total_checked": 2,
  "effective_count": 1,
  "failed_count": 1,
  "slippage_failed_count": 1,
  "orders": [
    {
      "symbol": "BTCUSDT", "position_side": "LONG", "action": "OPEN", "order_type": "MARKET",
      "requested_qty": 0.001, "executed_qty": 0.001,
      "reference_price": 60000.0, "avg_price": 60050.0, "slippage_pct": 0.0833,
      "match_status": "MATCHED", "severity": "INFO", "effective": true
    },
    {
      "symbol": "ETHUSDT", "position_side": "SHORT", "action": "OPEN", "order_type": "MARKET",
      "requested_qty": 0.01, "executed_qty": 0.01,
      "reference_price": 3000.0, "avg_price": 3100.0, "slippage_pct": 3.3333,
      "match_status": "SLIPPAGE_EXCEEDED", "severity": "CRITICAL", "effective": false
    }
  ]
}
```

### Qué significa el resultado
- La detección LONG/SHORT, el registro de precio/cantidad solicitados vs. ejecutados, el
  cálculo de `slippage_pct` y los contadores de efectivas/fallidas/fallidas-por-deslizamiento
  funcionan correctamente extremo a extremo (ledger → reconciliación → persistencia → reporte).
- La migración aditiva permite desplegar este cambio sobre una base de datos ya existente sin
  perder el historial previo de conciliaciones.
- `run_execution_verification_test` (Test 3) no pudo validarse con una orden real en este
  entorno por la misma razón que Test 1: faltan credenciales de Binance Testnet y, para el
  primer paso, conectividad de red hacia `testnet.binancefuture.com`.

**Conclusión:** la lógica de negocio (checks de creación/envío/ejecución, slippage, reporte)
quedó validada con datos simulados controlados. Para ver el Reporte de Tests con datos 100%
reales hace falta ejecutar Test 1 y/o Test 3 en la instancia desplegada con
`BINANCE_TESTNET_API_KEY`/`BINANCE_TESTNET_SECRET_KEY` configuradas.
