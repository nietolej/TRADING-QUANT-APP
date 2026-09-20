import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from data_layer.storage import SessionLocal
from execution_engine.binance_client import BinanceTestnetClient, format_binance_error
from notifications.telegram_bot import TelegramNotifier
from reconciliation.models import APP_ORDER_TAG_PREFIX, AppOrderRecord, ReconciliationRecord

logger = logging.getLogger("OrderReconciler")

# Estados terminales "exitosos" que consideramos ejecución real en el exchange
FILLED_STATUSES = {"FILLED", "PARTIALLY_FILLED"}
FAILED_STATUSES = {"REJECTED", "CANCELED", "EXPIRED"}

# Estados que cuentan como "orden efectiva" (ejecutada correctamente, sin discrepancias, o
# cancelada de forma esperada por ser la contraparte de un TP/SL que sí se disparó) en el
# Informe de Confiabilidad. Todo lo demás cuenta como fallida.
EFFECTIVE_STATUSES = {"MATCHED", "CANCELED_AS_EXPECTED"}

# Estados en los que una orden de entrada/salida cuenta como ejecutada tal como la pidió el bot. El deslizamiento
# (SLIPPAGE_EXCEEDED) es un dato informativo del costo de ejecución: no invalida por sí solo la orden.
EXECUTED_OK_STATUSES = {"MATCHED", "SLIPPAGE_EXCEEDED"}
# Estados de una orden condicional (SL/TP) que Binance reconoce y no dejó mal colocada.
PROTECTION_OK_STATUSES = {"MATCHED", "CANCELED_AS_EXPECTED"}
# Estados en los que una orden condicional sigue VIVA en Binance (puede dispararse todavía).
LIVE_EXCHANGE_STATUSES = {"NEW", "WORKING", "PARTIALLY_FILLED"}
# "Exactamente": cantidad ejecutada igual a la del bot salvo redondeo de precisión (0.01%).
EXACT_QTY_TOLERANCE_PCT = 0.01

# Estados de una orden en Binance a partir de los cuales ya no cambia.
TERMINAL_EXCHANGE_STATUSES = {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}


def _reliability_label(reliability_pct: Optional[float]) -> str:
    """Etiqueta de confiabilidad del bot según el % de órdenes efectivas."""
    if reliability_pct is None:
        return "Sin datos"
    if reliability_pct >= 95:
        return "Alta"
    if reliability_pct >= 80:
        return "Media"
    return "Baja"


def _position_side(action: Optional[str], side: Optional[str]) -> Optional[str]:
    """
    Traduce (acción de la app, lado enviado a Binance) a la dirección de la posición que
    representa, para mostrarla en el Reporte de Tests como LONG/SHORT en vez de BUY/SELL:
    - OPEN + BUY  = abre LONG   / OPEN + SELL = abre SHORT
    - CLOSE + SELL = cierra LONG (se vende lo comprado) / CLOSE + BUY = cierra SHORT
    """
    if not action or not side:
        return None
    action = action.upper()
    side = side.upper()
    if action == "OPEN":
        return "LONG" if side == "BUY" else "SHORT"
    if action == "CLOSE":
        return "LONG" if side == "SELL" else "SHORT"
    return None


class OrderReconciler:
    """
    Módulo separado de verificación/conciliación: confirma que cada orden que la app envió
    (registrada en el ledger local `app_order_ledger`) realmente se ejecutó en Binance
    (Testnet/Demo por defecto), y detecta "huérfanos" — órdenes ejecutadas en Binance con la
    etiqueta de esta app que nunca quedaron registradas del lado local (ej. por un crash justo
    después de enviar la orden, o un SL/TP disparado sin que el proceso lo capturara).

    No depende de PaperTrader ni de AlgoExecutionEngine: sólo del ledger (reconciliation.models)
    y del cliente de Binance, así que puede correr como proceso independiente (ver
    run_reconciliation.py) sin interferir con la operativa en curso.
    """

    def __init__(
        self,
        use_testnet: bool = True,
        price_tolerance_pct: float = 0.5,
        qty_tolerance_pct: float = 1.0,
        slippage_tolerance_pct: float = 0.05,
        notify: bool = True,
    ):
        self.use_testnet = use_testnet
        self.price_tolerance_pct = price_tolerance_pct
        self.qty_tolerance_pct = qty_tolerance_pct
        # Deslizamiento máximo tolerado entre el precio de referencia (tomado justo antes de
        # enviar la orden) y el precio realmente ejecutado en Binance. Por encima de este
        # umbral la orden se marca SLIPPAGE_EXCEEDED y deja de contar como "confiable" en el
        # Informe de Confiabilidad (criterio pedido: deslizamiento menor a 0.05%).
        self.slippage_tolerance_pct = slippage_tolerance_pct
        self.client = BinanceTestnetClient(use_testnet=use_testnet)
        self.notifier = TelegramNotifier() if notify else None

    # ── Verificación de órdenes conocidas por la app ────────────────────────────

    def _within_tolerance(self, expected: Optional[float], actual: Optional[float], tolerance_pct: float) -> bool:
        if expected is None or actual is None or expected == 0:
            return True
        return abs(actual - expected) / abs(expected) * 100.0 <= tolerance_pct

    def _order_detail(self, record: AppOrderRecord) -> Dict[str, Any]:
        """Detalle completo de la orden del ledger, para que el Reporte de Tests no dependa de volver a consultarla."""
        return {
            "symbol": record.symbol,
            "app_order_ref": record.app_order_ref,
            "binance_order_id": record.binance_order_id,
            "position_side": _position_side(record.action, record.side),
            "side": record.side,
            "action": record.action,
            "order_type": record.order_type,
            "requested_qty": record.requested_qty,
            "executed_qty": record.executed_qty,
            "requested_price": record.requested_price,
            "avg_price": record.avg_price,
            "reference_price": record.reference_price,
            # Lo que Binance dice de la orden (lado, cantidad, precio de activación): permite comparar app vs Binance.
            "binance_side": None,
            "binance_orig_qty": None,
            "binance_trigger_price": None,
            # Costo de deslizamiento firmado en % (positivo = en contra, negativo = a favor).
            "slippage_pct": None,
            "checks": {
                "created_in_app": True,
                "sent_to_binance": bool(record.binance_order_id),
                "executed_in_binance": False,
            },
        }

    def reconcile_order(self, record: AppOrderRecord) -> Dict[str, Any]:
        """Consulta Binance para UNA orden del ledger y determina si coincide con lo reportado al enviarla.

        Además de match_status/severity/details (y el detalle de la orden, ver _order_detail),
        el resultado siempre incluye binance_status/binance_exec_qty/binance_avg_price (None
        cuando no se pudo obtener nada de Binance) — pensado para que quien llame pueda armar
        una comparación app-vs-Binance campo a campo (ver "Test 3" en la página de
        Conciliación) sin tener que re-parsear `details`.
        """
        detail = self._order_detail(record)

        def _result(match_status: str, severity: str, details: str,
                    b_status: Optional[str] = None, b_qty: Optional[float] = None,
                    b_price: Optional[float] = None) -> Dict[str, Any]:
            return {
                **detail,
                "match_status": match_status,
                "severity": severity,
                "details": details,
                "binance_status": b_status,
                "binance_exec_qty": b_qty,
                "binance_avg_price": b_price,
            }

        if not record.binance_order_id:
            return _result(
                "ERROR", "WARNING",
                "El registro no tiene binance_order_id (la orden nunca llegó a crearse en Binance).",
            )

        try:
            order_id = int(record.binance_order_id)
        except (TypeError, ValueError):
            order_id = record.binance_order_id

        # Los SL/TP condicionales que coloca esta app (binance_client.place_futures_sl_tp)
        # se registran en el ledger con el `algoId` que devuelve Binance para ese tipo de
        # orden (ver comentario en place_futures_sl_tp y el mismo criterio ya usado en
        # cancel_all_open_orders/futures_get_open_algo_orders), NO con un `orderId` de
        # órdenes estándar. futures_get_order() siempre responde -2013 "Order does not
        # exist" para un algoId, así que antes esto marcaba TODO SL/TP como
        # MISSING_ON_BINANCE/CRITICAL aunque la orden estuviera perfectamente viva —
        # auditoría 2026-09-19: 12 de 25 "discrepancias" en Test 2 eran exactamente esto.
        is_conditional = record.action in ("STOP_LOSS", "TAKE_PROFIT")

        exchange_order = None
        lookup_errors = []
        is_algo_response = False

        if is_conditional:
            try:
                exchange_order = self.client.client.futures_get_algo_order(symbol=record.symbol, algoId=order_id)
                is_algo_response = True
            except Exception as e:
                lookup_errors.append(format_binance_error(e))

        if exchange_order is None:
            try:
                exchange_order = self.client.client.futures_get_order(symbol=record.symbol, orderId=order_id)
                is_algo_response = False
            except Exception as e:
                lookup_errors.append(format_binance_error(e))

        if exchange_order is None:
            return _result(
                "MISSING_ON_BINANCE", "CRITICAL",
                (
                    f"La app registró el envío de la orden {record.binance_order_id} ({record.symbol}) pero "
                    f"Binance no la reconoce en ningún endpoint ({'; '.join(lookup_errors)})."
                ),
            )

        detail["binance_side"] = str(exchange_order.get("side") or "").upper() or None
        detail["binance_orig_qty"] = float(exchange_order.get("origQty") or exchange_order.get("quantity") or 0.0) or None
        detail["binance_trigger_price"] = float(
            exchange_order.get("triggerPrice") or exchange_order.get("stopPrice") or 0.0
        ) or None

        if is_algo_response:
            # La respuesta de "Query Algo Order" no usa los mismos nombres de campo que una
            # orden estándar (status/executedQty/avgPrice) — se prueban las variantes
            # conocidas y, si no se puede identificar el estado, se acepta como MATCHED en
            # vez de arriesgar un falso CRÍTICO: lo único que de verdad importa aquí es que
            # Binance reconoce el algoId (si no lo reconociera, ya se habría devuelto
            # MISSING_ON_BINANCE arriba).
            raw_status = str(exchange_order.get("algoStatus") or exchange_order.get("status") or "").upper()
            exec_qty = float(exchange_order.get("executedQty") or exchange_order.get("executedAmt") or 0.0)
            avg_price = float(exchange_order.get("avgPrice") or exchange_order.get("avgFillPrice") or 0.0)
            # La respuesta de una Algo Order disparada (FINISHED) NO trae cantidad ni precio ejecutados:
            # están en la orden real que generó (actualOrderId). Sin consultarla, exec_qty quedaba en 0 y
            # todo SL/TP ejecutado se marcaba QTY_MISMATCH.
            actual_order_id = exchange_order.get("actualOrderId")
            if actual_order_id and raw_status == "FINISHED":
                try:
                    real = self.client.client.futures_get_order(symbol=record.symbol, orderId=int(actual_order_id))
                    exec_qty = float(real.get("executedQty") or exec_qty)
                    avg_price = float(real.get("avgPrice") or avg_price)
                except Exception as e:
                    logger.warning("No se pudo consultar la orden real %s del algo %s: %s", actual_order_id, record.binance_order_id, e)
            if not raw_status:
                return _result(
                    "MATCHED", "INFO",
                    (
                        f"Orden condicional {record.binance_order_id} ({record.symbol}) confirmada en Binance "
                        f"vía Algo Orders (sin campo de estado reconocible en la respuesta)."
                    ),
                    b_status="UNKNOWN", b_qty=exec_qty, b_price=avg_price or None,
                )
            # El vocabulario de estados de Algo Orders (WORKING/FINISHED/CANCELLED/...) no
            # coincide con el de órdenes estándar (NEW/FILLED/CANCELED/...) que usan
            # FAILED_STATUSES/FILLED_STATUSES más abajo — se traduce para reusar la misma
            # lógica en vez de duplicarla.
            ALGO_STATUS_MAP = {
                "WORKING": "NEW",
                "FINISHED": "FILLED",
                "CANCELLED": "CANCELED",
                "CANCELED": "CANCELED",
                "REJECTED": "REJECTED",
                "EXPIRED": "EXPIRED",
            }
            status = ALGO_STATUS_MAP.get(raw_status, raw_status)
        else:
            status = str(exchange_order.get("status", "")).upper()
            exec_qty = float(exchange_order.get("executedQty", 0.0) or 0.0)
            avg_price = float(exchange_order.get("avgPrice", 0.0) or 0.0)

        detail["executed_qty"] = exec_qty or detail["executed_qty"]
        detail["avg_price"] = avg_price or detail["avg_price"]

        # Un Take Profit/Stop Loss CANCELED no es una falla: Binance solo permite que UNA de
        # las dos órdenes condicionales de un mismo par se ejecute (cuando una se dispara, la
        # otra se cancela automáticamente), y además el propio ciclo de cierre de la app
        # (`cancel_all_open_orders`, ver run_full_cycle_test/run_execution_verification_test y
        # el cierre normal de posiciones) cancela explícitamente ambas antes de cerrar con una
        # orden MARKET. Sin este caso especial, TODO TP/SL quedaba marcado STATUS_MISMATCH
        # CRITICAL de forma sistemática, aunque el bot esté operando exactamente como se espera.
        if status == "CANCELED" and record.action in ("TAKE_PROFIT", "STOP_LOSS"):
            return _result(
                "CANCELED_AS_EXPECTED", "INFO",
                (
                    f"Orden {record.action} {record.binance_order_id} ({record.symbol}) cancelada en Binance — "
                    f"comportamiento esperado: se dispara como máximo una de las dos condicionales (TP/SL) del par, "
                    f"o ambas se cancelan al cerrar la posición manualmente."
                ),
                b_status=status, b_qty=exec_qty, b_price=avg_price or None,
            )

        if status in FAILED_STATUSES:
            return _result(
                "STATUS_MISMATCH", "CRITICAL",
                (
                    f"Orden {record.binance_order_id} ({record.symbol}) en estado terminal fallido "
                    f"'{status}' en Binance: la app la contaba como ejecutada."
                ),
                b_status=status, b_qty=exec_qty, b_price=avg_price or None,
            )

        if status not in FILLED_STATUSES and status != "NEW":
            return _result(
                "STATUS_MISMATCH", "WARNING",
                f"Orden {record.binance_order_id} ({record.symbol}) en estado inesperado '{status}'.",
                b_status=status, b_qty=exec_qty, b_price=avg_price or None,
            )

        if status in FILLED_STATUSES:
            # A partir de aquí la orden sí se creó en la app, sí se envió y sí se ejecutó en
            # Binance — las tres condiciones que valida esta reconciliación por orden.
            detail["checks"]["executed_in_binance"] = True

            if not self._within_tolerance(record.requested_qty, exec_qty, self.qty_tolerance_pct):
                return _result(
                    "QTY_MISMATCH", "WARNING",
                    (
                        f"Cantidad solicitada {record.requested_qty} vs ejecutada {exec_qty} en Binance "
                        f"para la orden {record.binance_order_id} ({record.symbol})."
                    ),
                    b_status=status, b_qty=exec_qty, b_price=avg_price or None,
                )
            if record.order_type == "LIMIT" and avg_price and not self._within_tolerance(
                record.requested_price, avg_price, self.price_tolerance_pct
            ):
                return _result(
                    "PRICE_MISMATCH", "WARNING",
                    (
                        f"Precio solicitado {record.requested_price} vs avgPrice ejecutado {avg_price} en "
                        f"Binance para la orden {record.binance_order_id} ({record.symbol})."
                    ),
                    b_status=status, b_qty=exec_qty, b_price=avg_price or None,
                )

            # Deslizamiento: compara el precio realmente ejecutado contra el precio de
            # referencia tomado justo antes de enviar la orden (cubre también MARKET, que no
            # tiene requested_price). Sin referencia disponible, no se puede evaluar slippage.
            # El deslizamiento es un COSTO firmado: positivo = ejecutado peor que la referencia
            # (compra más cara / venta más barata), negativo = a favor. Solo el que va en contra
            # cuenta contra la tolerancia; antes se medía en valor absoluto y una venta que salió
            # MEJOR de lo esperado se marcaba como SLIPPAGE_EXCEEDED.
            reference = record.reference_price or record.requested_price
            if reference and avg_price:
                direction = 1.0 if (record.side or "").upper() == "BUY" else -1.0
                slippage_pct = (avg_price - reference) / abs(reference) * 100.0 * direction
                detail["slippage_pct"] = round(slippage_pct, 4)
                if slippage_pct > self.slippage_tolerance_pct:
                    return _result(
                        "SLIPPAGE_EXCEEDED",
                        "CRITICAL" if slippage_pct > self.slippage_tolerance_pct * 3 else "WARNING",
                        (
                            f"Deslizamiento de {slippage_pct:.3f}% (tolerancia {self.slippage_tolerance_pct}%) "
                            f"en la orden {record.binance_order_id} ({record.symbol}): referencia {reference} "
                            f"vs ejecutado {avg_price}."
                        ),
                        b_status=status, b_qty=exec_qty, b_price=avg_price or None,
                    )

        return _result(
            "MATCHED", "INFO",
            f"Orden {record.binance_order_id} ({record.symbol}) confirmada en Binance con estado {status}.",
            b_status=status, b_qty=exec_qty, b_price=avg_price or None,
        )

    def reconcile_pending(self, lookback_hours: float = 48.0, limit: int = 500) -> List[Dict[str, Any]]:
        """Concilia todas las órdenes del ledger local aún no verificadas contra Binance."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        results: List[Dict[str, Any]] = []
        db = SessionLocal()
        try:
            pending = (
                db.query(AppOrderRecord)
                .filter(
                    AppOrderRecord.reconciled == False,  # noqa: E712
                    AppOrderRecord.use_testnet == self.use_testnet,
                    AppOrderRecord.created_at >= cutoff,
                )
                .order_by(AppOrderRecord.created_at.asc())
                .limit(limit)
                .all()
            )

            for record in pending:
                if record.status != "SENT_OK":
                    # Nunca llegó a Binance (rechazada antes de salir, guardarraíl, etc.):
                    # no hay nada que conciliar contra el exchange.
                    record.reconciled = True
                    record.reconciliation_status = "APP_SIDE_ONLY"
                    db.add(record)
                    continue

                outcome = self.reconcile_order(record)
                record.reconciled = True
                record.reconciliation_status = outcome["match_status"]
                db.add(record)

                results.append(outcome)

            db.commit()
        finally:
            db.close()

        self._persist_results(results)
        return results

    # ── Detección de huérfanos (ejecutado en Binance, ausente del ledger local) ─

    def find_orphan_trades(self, symbol: str, lookback_hours: float = 24.0) -> List[Dict[str, Any]]:
        """
        Recorre el historial real de órdenes de Binance para `symbol` y marca como huérfanas
        las que llevan la etiqueta de esta app (clientOrderId con prefijo QTAPP_) pero no
        tienen un registro correspondiente en el ledger local — típicamente SL/TP disparados
        sin que el proceso lo capturara, o un crash justo tras enviar la orden.
        """
        binance_symbol = symbol.replace("/", "").upper()
        if not self.client.client:
            return [{
                "symbol": binance_symbol,
                "match_status": "ERROR",
                "severity": "WARNING",
                "details": "Cliente de Binance no disponible (restricción geográfica o credenciales ausentes).",
            }]

        start_time_ms = int((datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).timestamp() * 1000)

        try:
            orders = self.client.client.futures_get_all_orders(
                symbol=binance_symbol, startTime=start_time_ms, limit=500
            )
        except Exception as e:
            return [{
                "symbol": binance_symbol,
                "match_status": "ERROR",
                "severity": "WARNING",
                "details": f"No se pudo consultar el historial de órdenes de Binance: {format_binance_error(e)}",
            }]

        app_tagged = [
            o for o in orders
            if str(o.get("clientOrderId", "")).startswith(APP_ORDER_TAG_PREFIX)
            and str(o.get("status", "")).upper() in FILLED_STATUSES
        ]
        if not app_tagged:
            return []

        db = SessionLocal()
        try:
            known_ids = {
                row[0]
                for row in db.query(AppOrderRecord.binance_order_id).filter(
                    AppOrderRecord.symbol == binance_symbol,
                    AppOrderRecord.binance_order_id.isnot(None),
                ).all()
            }
        finally:
            db.close()

        results: List[Dict[str, Any]] = []
        for o in app_tagged:
            order_id = str(o.get("orderId"))
            if order_id in known_ids:
                continue
            side = str(o.get("side") or "")
            results.append(
                {
                    "symbol": binance_symbol,
                    "app_order_ref": None,
                    "binance_order_id": order_id,
                    "position_side": _position_side("OPEN", side) if side else None,
                    "side": side or None,
                    "action": None,
                    "order_type": o.get("type"),
                    "requested_qty": None,
                    "executed_qty": float(o.get("executedQty")) if o.get("executedQty") else None,
                    "requested_price": None,
                    "avg_price": float(o.get("avgPrice")) if o.get("avgPrice") else None,
                    "reference_price": None,
                    "slippage_pct": None,
                    "checks": {
                        "created_in_app": False,
                        "sent_to_binance": True,
                        "executed_in_binance": True,
                    },
                    "match_status": "ORPHAN_ON_BINANCE",
                    "severity": "CRITICAL",
                    "details": (
                        f"Orden {order_id} ({binance_symbol}, {o.get('side')} {o.get('executedQty')} "
                        f"@ {o.get('avgPrice')}) ejecutada en Binance con etiqueta de la app pero sin "
                        f"registro en el ledger local."
                    ),
                }
            )

        self._persist_results(results)
        return results

    # ── Orquestación ─────────────────────────────────────────────────────────

    def _persist_results(self, results: List[Dict[str, Any]]) -> None:
        if not results:
            return
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            for r in results:
                db.add(
                    ReconciliationRecord(
                        run_at=now,
                        symbol=r.get("symbol"),
                        app_order_ref=r.get("app_order_ref"),
                        binance_order_id=r.get("binance_order_id"),
                        match_status=r.get("match_status"),
                        severity=r.get("severity"),
                        details=r.get("details"),
                        position_side=r.get("position_side"),
                        side=r.get("side"),
                        action=r.get("action"),
                        order_type=r.get("order_type"),
                        requested_qty=r.get("requested_qty"),
                        executed_qty=r.get("executed_qty"),
                        requested_price=r.get("requested_price"),
                        avg_price=r.get("avg_price"),
                        reference_price=r.get("reference_price"),
                        slippage_pct=r.get("slippage_pct"),
                        created_in_app=r.get("checks", {}).get("created_in_app"),
                        sent_to_binance=r.get("checks", {}).get("sent_to_binance"),
                        executed_in_binance=r.get("checks", {}).get("executed_in_binance"),
                    )
                )
            db.commit()
        except Exception as e:
            logger.warning("No se pudo persistir el resultado de reconciliación: %s", e)
            db.rollback()
        finally:
            db.close()

    # ── Modo Test 1: ciclo completo inmediato (entrada + SL/TP + cierre + reconciliación) ──

    def run_full_cycle_test(self, symbol: str = "BTC/USDT", quantity: float = 0.001) -> Dict[str, Any]:
        """
        Ejecuta un ciclo completo de orden real en Testnet usando EXACTAMENTE el mismo código
        de producción que usan los bots (BinanceTestnetClient.place_futures_order /
        place_futures_sl_tp / cancel_all_open_orders / close_futures_position), y luego concilia
        esas mismas órdenes contra Binance. No es una simulación aparte: si este test pasa,
        el pipeline real (incluida la corrección de algoId del 18/09) está probado de punta a
        punta, no solo "compila".

        Diseñado para "Modo Test 1" en la página de Conciliación: una prueba a demanda que no
        requiere esperar a que un bot en vivo abra posición.
        """
        if not self.use_testnet:
            return {
                "success": False,
                "error": "El Modo Test 1 solo puede correr contra Testnet (nunca Mainnet) por seguridad.",
                "steps": [],
            }

        binance_symbol = symbol.replace("/", "").upper()
        steps: List[Dict[str, Any]] = []

        def _step(name: str, ok: bool, detail: str):
            steps.append({"step": name, "ok": ok, "detail": detail})
            return ok

        # 1. Precio de referencia para calcular SL/TP a una distancia segura (2%)
        price = self.client.get_symbol_price(binance_symbol)
        if not price or price <= 0:
            _step("Precio de referencia", False, "No se pudo obtener el precio actual del símbolo.")
            return {"success": False, "steps": steps, "reconciliation": []}
        _step("Precio de referencia", True, f"{price}")

        # 2. Orden de entrada MARKET (idéntico a PaperTrader._open_position)
        entry_order, entry_err = self.client.place_futures_order(
            symbol=symbol, side="long", quantity=quantity, order_type="MARKET", verify_execution=True
        )
        if not _step("Entrada MARKET (BUY)", bool(entry_order) and not entry_err, entry_err or f"orderId={entry_order.get('orderId') if entry_order else None}"):
            return {"success": False, "steps": steps, "reconciliation": []}

        try:
            # 3. SL/TP condicional (idéntico a PaperTrader._open_position) — aquí es donde vivía
            # el bug de algoId del 18/09, así que este paso es el más importante del test.
            sl_price = round(price * 0.98, 2)
            tp_price = round(price * 1.02, 2)
            sl_tp_res = self.client.place_futures_sl_tp(
                symbol=symbol, side="long", quantity=quantity,
                sl_price=sl_price, tp_price=tp_price,
                sl_order_type="LIMIT", tp_order_type="LIMIT",
            )
            tp_ok = bool(sl_tp_res.get("tp_order"))
            sl_ok = bool(sl_tp_res.get("sl_order"))
            _step(
                "Take Profit condicional",
                tp_ok,
                f"algoId/orderId={sl_tp_res['tp_order'].get('orderId') or sl_tp_res['tp_order'].get('algoId')}" if tp_ok
                else "; ".join(e for e in sl_tp_res.get("errors", []) if e.startswith("TP")),
            )
            _step(
                "Stop Loss condicional",
                sl_ok,
                f"algoId/orderId={sl_tp_res['sl_order'].get('orderId') or sl_tp_res['sl_order'].get('algoId')}" if sl_ok
                else "; ".join(e for e in sl_tp_res.get("errors", []) if e.startswith("SL")),
            )

            # 4. Cancelar condicionales (idéntico al cleanup normal antes de un cierre)
            cancel_ok, cancel_err = self.client.cancel_all_open_orders(symbol)
            _step("Cancelar condicionales", cancel_ok, cancel_err or "OK")

            # 5. Cerrar la posición de prueba (idéntico a PaperTrader._close_position)
            close_order, close_err = self.client.close_futures_position(
                symbol=symbol, side="long", quantity=quantity, order_type="MARKET", verify_execution=True
            )
            _step("Cierre MARKET (SELL reduceOnly)", bool(close_order) and not close_err, close_err or f"orderId={close_order.get('orderId') if close_order else None}")
        except Exception as e:
            _step("Excepción durante el ciclo", False, str(e))
            # Intento de limpieza best-effort para no dejar la posición de prueba abierta
            try:
                self.client.cancel_all_open_orders(symbol)
                self.client.close_futures_position(symbol=symbol, side="long", quantity=quantity, order_type="MARKET", verify_execution=False)
            except Exception:
                logger.warning("No se pudo limpiar la posición de prueba de Testnet: puede haber quedado abierta", exc_info=True)

        # 6. Reconciliar de inmediato las órdenes que este mismo test acaba de generar,
        # probando también el módulo de reconciliación como parte del ciclo.
        recon_results = self.reconcile_pending(lookback_hours=0.05, limit=20)

        overall_ok = all(s["ok"] for s in steps)
        return {
            "success": overall_ok,
            "symbol": binance_symbol,
            "steps": steps,
            "reconciliation": recon_results,
        }

    # ── Orquestación ─────────────────────────────────────────────────────────

    def run(self, symbols: List[str], lookback_hours: float = 24.0) -> Dict[str, Any]:
        """Corre una reconciliación completa (pendientes + huérfanos por símbolo) y notifica discrepancias."""
        all_results = list(self.reconcile_pending(lookback_hours=lookback_hours))
        for symbol in symbols:
            all_results.extend(self.find_orphan_trades(symbol, lookback_hours=lookback_hours))

        summary: Dict[str, int] = {}
        critical: List[Dict[str, Any]] = []
        for r in all_results:
            status = r.get("match_status", "UNKNOWN")
            summary[status] = summary.get(status, 0) + 1
            if r.get("severity") == "CRITICAL":
                critical.append(r)

        if critical and self.notifier:
            lines = [f"{c['symbol']}: {c['match_status']} — {c['details']}" for c in critical[:10]]
            self.notifier.send_alert(
                f"Reconciliación Binance {'Demo/Testnet' if self.use_testnet else 'Real'}: "
                f"{len(critical)} discrepancia(s) crítica(s)",
                {"Detalle": " | ".join(lines)},
                is_critical=True,
            )

        return {
            "network": "Binance Futures Testnet (Demo)" if self.use_testnet else "Binance Real (Mainnet)",
            "lookback_hours": lookback_hours,
            "symbols": symbols,
            "total_checked": len(all_results),
            "summary": summary,
            "critical_count": len(critical),
            "results": all_results,
        }

    def _reconcile_ledger_order(self, binance_order_id: Any) -> Optional[Dict[str, Any]]:
        """
        Concilia contra Binance UNA orden concreta del ledger (buscada por su orderId) y
        persiste el resultado. A diferencia de reconcile_pending, no toca ninguna otra orden
        pendiente (de otros bots o de pruebas anteriores). None = no hay registro en el ledger.
        """
        db = SessionLocal()
        try:
            record = (
                db.query(AppOrderRecord)
                .filter(
                    AppOrderRecord.binance_order_id == str(binance_order_id),
                    AppOrderRecord.use_testnet == self.use_testnet,
                )
                .order_by(AppOrderRecord.created_at.desc())
                .first()
            )
            if record is None:
                return None
            outcome = self.reconcile_order(record)
            record.reconciled = True
            record.reconciliation_status = outcome["match_status"]
            db.add(record)
            db.commit()
        finally:
            db.close()
        self._persist_results([outcome])
        return outcome

    # ── Test 1: verificación de una orden (creación → envío → ejecución + deslizamiento) ─────

    def run_execution_verification_test(
        self, symbol: str = "BTC/USDT", quantity: float = 0.001, side: str = "long"
    ) -> Dict[str, Any]:
        """
        Prueba puntual (sin SL/TP) de todo lo que debe cumplir una orden de la app:
        1) que se ENVÍE a Binance,
        2) que quede REGISTRADA en el ledger local de la app (`app_order_ledger`, vía
           `_log_order_to_ledger` dentro de `place_futures_order`),
        3) que Binance confirme que se EJECUTÓ realmente (estado FILLED al conciliar) y
        4) que el deslizamiento entre el precio de referencia (tomado justo antes de enviarla)
           y el precio realmente ejecutado quede dentro de `slippage_tolerance_pct`.

        Abre con una orden MARKET, concilia esa orden concreta y cierra la posición de prueba
        para no dejarla abierta. Devuelve además un veredicto (`verdict`) que responde si la
        orden se ejecutó de acuerdo a lo que pidió el bot, con el motivo cuando no. Solo corre
        en Testnet.
        """
        if not self.use_testnet:
            return {
                "success": False,
                "error": "El Test 1 solo puede correr contra Testnet (nunca Mainnet) por seguridad.",
                "steps": [],
                "orders": [],
                "verdict": {"executed_as_bot": False, "reason": "Solo disponible en Testnet."},
            }

        binance_symbol = symbol.replace("/", "").upper()
        steps: List[Dict[str, Any]] = []

        def _step(name: str, ok: bool, detail: str) -> bool:
            steps.append({"step": name, "ok": ok, "detail": detail})
            return ok

        def _verdict(executed_as_bot: bool, reason: str) -> Dict[str, Any]:
            return {"executed_as_bot": executed_as_bot, "reason": reason}

        entry_order, entry_err = self.client.place_futures_order(
            symbol=symbol, side=side, quantity=quantity, order_type="MARKET", verify_execution=True
        )
        entry_ok = bool(entry_order) and not entry_err
        _step(
            "1. Enviada a Binance",
            entry_ok,
            entry_err or f"orderId={entry_order.get('orderId') if entry_order else None}",
        )
        if not entry_ok:
            return {
                "success": False, "symbol": binance_symbol, "steps": steps, "orders": [],
                "verdict": _verdict(False, entry_err or "La orden no se pudo enviar a Binance."),
            }

        entry_recon = self._reconcile_ledger_order(entry_order.get("orderId"))
        _step(
            "2. Registrada en la app (ledger)",
            entry_recon is not None,
            "La orden quedó registrada en el ledger local." if entry_recon is not None
            else "No se encontró la orden en el ledger de la app.",
        )

        executed_ok = bool(entry_recon and entry_recon["checks"]["executed_in_binance"])
        _step(
            "3. Ejecución confirmada por Binance",
            executed_ok,
            (entry_recon or {}).get("details", "No se pudo conciliar la orden porque no está en el ledger."),
        )

        slippage = (entry_recon or {}).get("slippage_pct")
        if slippage is None:
            slippage_ok = False
            slippage_detail = "No se pudo medir el deslizamiento (falta precio de referencia o de ejecución)."
        else:
            slippage_ok = slippage <= self.slippage_tolerance_pct
            slippage_detail = (
                f"Deslizamiento {slippage:.4f}% (tolerancia {self.slippage_tolerance_pct}%): "
                f"referencia {entry_recon.get('reference_price')} vs ejecutado {entry_recon.get('avg_price')}."
            )
        _step("4. Deslizamiento dentro de tolerancia", slippage_ok, slippage_detail)

        # Limpieza: cerrar la posición de prueba para no dejarla abierta en Testnet.
        close_order, close_err = self.client.close_futures_position(
            symbol=symbol, side=side, quantity=quantity, order_type="MARKET", verify_execution=True
        )
        _step(
            "5. Cierre de la posición de prueba",
            bool(close_order) and not close_err,
            close_err or f"orderId={close_order.get('orderId') if close_order else None}",
        )
        close_recon = self._reconcile_ledger_order(close_order.get("orderId")) if close_order else None

        orders = [o for o in (entry_recon, close_recon) if o]
        entry_matched = bool(entry_recon and entry_recon["match_status"] == "MATCHED")
        if entry_matched and slippage_ok:
            verdict = _verdict(True, "La orden se creó, se envió y se ejecutó tal como la pidió el bot, con deslizamiento dentro de tolerancia.")
        else:
            reasons = [s["detail"] for s in steps[:4] if not s["ok"]]
            if not reasons and entry_recon:
                reasons = [entry_recon.get("details", "")]
            verdict = _verdict(False, " | ".join(r for r in reasons if r) or "La ejecución no coincide con lo pedido por el bot.")

        return {
            "success": all(s["ok"] for s in steps) and verdict["executed_as_bot"],
            "symbol": binance_symbol,
            "steps": steps,
            "orders": orders,
            "verdict": verdict,
        }

    # ── Reporte de Tests / Confiabilidad del Bot (histórico persistido) ────────

    def build_test_report(self, lookback_hours: float = 24.0, limit: int = 300) -> Dict[str, Any]:
        """
        Arma el "Informe de Confiabilidad" a partir del historial persistido en
        `reconciliation_log` (generado por Test 1, Test 2 y Test 3, y por "Conciliar ahora"):

        - El embudo creación → envío → ejecución: cuántas órdenes quedaron creadas en el
          ledger de la app, cuántas se enviaron a Binance y cuántas Binance confirmó
          ejecutadas de verdad.
        - Cuántas de esas órdenes están "conciliadas" de forma confiable: mismo estado FILLED,
          cantidad dentro de tolerancia y deslizamiento de precio por debajo del umbral
          (`slippage_tolerance_pct`, 0.05% por defecto) — es decir, `match_status == MATCHED`.
        - El detalle completo por orden (activo, long/short, cantidad y precio solicitados vs.
          ejecutados, % de deslizamiento) y un puntaje global de confiabilidad del bot.

        Creación y envío se cuentan directamente sobre `app_order_ledger` (fuente de verdad,
        incluye también las órdenes que fallaron al enviarse y por eso nunca llegan a
        conciliarse contra Binance); ejecución, deslizamiento y confiabilidad salen de
        `reconciliation_log`, que solo existe para las órdenes efectivamente enviadas.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        db = SessionLocal()
        try:
            ledger_query = db.query(AppOrderRecord).filter(
                AppOrderRecord.use_testnet == self.use_testnet,
                AppOrderRecord.created_at >= cutoff,
            )
            created_in_app_count = ledger_query.count()
            sent_to_binance_count = ledger_query.filter(AppOrderRecord.status == "SENT_OK").count()

            records = (
                db.query(ReconciliationRecord)
                .filter(ReconciliationRecord.run_at >= cutoff)
                .order_by(ReconciliationRecord.run_at.desc())
                .limit(limit)
                .all()
            )
        finally:
            db.close()

        orders = []
        effective_count = 0
        slippage_failed_count = 0
        failed_count = 0
        executed_in_binance_count = 0
        long_count = 0
        short_count = 0
        for r in records:
            is_effective = r.match_status in EFFECTIVE_STATUSES
            is_slippage_failure = r.match_status == "SLIPPAGE_EXCEEDED"
            if is_effective:
                effective_count += 1
            else:
                failed_count += 1
            if is_slippage_failure:
                slippage_failed_count += 1
            if r.executed_in_binance:
                executed_in_binance_count += 1
            if r.position_side == "LONG":
                long_count += 1
            elif r.position_side == "SHORT":
                short_count += 1

            orders.append({
                "run_at": r.run_at.isoformat() if r.run_at else None,
                "symbol": r.symbol,
                "position_side": r.position_side,
                "side": r.side,
                "action": r.action,
                "order_type": r.order_type,
                "requested_qty": r.requested_qty,
                "executed_qty": r.executed_qty,
                "requested_price": r.requested_price,
                "avg_price": r.avg_price,
                "reference_price": r.reference_price,
                "slippage_pct": r.slippage_pct,
                "match_status": r.match_status,
                "severity": r.severity,
                "details": r.details,
                "binance_order_id": r.binance_order_id,
                "app_order_ref": r.app_order_ref,
                "created_in_app": r.created_in_app,
                "sent_to_binance": r.sent_to_binance,
                "executed_in_binance": r.executed_in_binance,
                "effective": is_effective,
            })

        total_checked = len(orders)
        reliability_pct = round((effective_count / total_checked) * 100.0, 2) if total_checked else None
        reliability_label = _reliability_label(reliability_pct)

        return {
            "lookback_hours": lookback_hours,
            "slippage_tolerance_pct": self.slippage_tolerance_pct,
            "total_checked": total_checked,
            "created_in_app_count": created_in_app_count,
            "sent_to_binance_count": sent_to_binance_count,
            "executed_in_binance_count": executed_in_binance_count,
            "effective_count": effective_count,
            "failed_count": failed_count,
            "slippage_failed_count": slippage_failed_count,
            "long_count": long_count,
            "short_count": short_count,
            "reliability_pct": reliability_pct,
            "reliability_label": reliability_label,
            "orders": orders,
        }

    # ── Conciliación por CICLO de un bot (entrada → SL/TP → salida) ─────────────

    @staticmethod
    def _qty_exact(expected: Optional[float], actual: Optional[float]) -> bool:
        if expected is None or actual is None or expected <= 0:
            return False
        return abs(actual - expected) / expected * 100.0 <= EXACT_QTY_TOLERANCE_PCT

    def _evaluate_cycles(self, orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Agrupa las órdenes de UN bot (en orden cronológico) en ciclos y decide, contra lo que Binance dice, cuáles
        son efectivas. Cada bot se evalúa por separado; en un símbolo compartido la salida de uno es la entrada
        del otro, así que lo único que cuenta son las órdenes de ESTE bot (por bot_id) y sus propios SL/TP.

        Un ciclo es efectivo cuando, igual en la app y en Binance:
          1. la ENTRADA se ejecutó (FILLED) por la cantidad que pidió el bot;
          2. se colocaron SU Stop Loss y SU Take Profit (lado contrario, misma cantidad que la entrada);
          3. la SALIDA (orden de cierre, o el SL/TP ejecutado) fue por esa misma cantidad;
          4. al salir NO quedó ningún SL/TP vivo en Binance (huérfano).
        Modifica cada orden con `effective`, `cycle` y, si falla, `cycle_issue`; devuelve los ciclos.
        """
        cycles: List[Dict[str, Any]] = []
        cur: Optional[Dict[str, Any]] = None
        for o in orders:
            action = o.get("action")
            if action == "OPEN" or cur is None:
                cur = {"id": len(cycles) + 1, "open": None, "sls": [], "tps": [], "closes": [], "orders": [],
                       "partial": action != "OPEN"}
                cycles.append(cur)
            o["cycle"] = cur["id"]
            cur["orders"].append(o)
            if action == "OPEN" and cur["open"] is None:
                cur["open"] = o
            elif action == "STOP_LOSS":
                cur["sls"].append(o)
            elif action == "TAKE_PROFIT":
                cur["tps"].append(o)
            elif action == "CLOSE":
                cur["closes"].append(o)

        for idx, c in enumerate(cycles):
            self._evaluate_cycle(c, is_last=idx == len(cycles) - 1)
        return [
            {k: c.get(k) for k in ("id", "status", "effective", "issues", "position_side", "entry_qty", "opened_at")}
            for c in cycles
        ]

    def _evaluate_cycle(self, c: Dict[str, Any], is_last: bool) -> None:
        issues: List[str] = []
        ok_statuses = EXECUTED_OK_STATUSES | PROTECTION_OK_STATUSES

        def fail(order: Optional[Dict[str, Any]], text: str, status: Optional[str] = None, severity: str = "CRITICAL"):
            issues.append(text)
            if order is not None:
                order["effective"] = False
                order["cycle_issue"] = text
                if status:
                    order["match_status"], order["severity"] = status, severity
                    order["details"] = text

        def sent(o: Dict[str, Any]) -> bool:
            return bool(o["sent_to_binance"]) and o["match_status"] != "SEND_FAILED"

        entry, sls, tps, closes = c["open"], c["sls"], c["tps"], c["closes"]
        protections = sls + tps
        exit_fill = next((o for o in protections if o.get("binance_status") == "FILLED"), None)
        sent_closes = [o for o in closes if sent(o)]
        exit_order = exit_fill or (sent_closes[-1] if sent_closes else None)
        first = entry or c["orders"][0]
        c["position_side"] = first.get("position_side")
        c["opened_at"] = first.get("created_at")
        c["entry_qty"] = entry.get("executed_qty") if entry else None

        # Cada orden parte de su papel resuelto por su propio estado; los chequeos de abajo la invalidan si falla.
        for o in c["orders"]:
            o["effective"] = o["match_status"] in ok_statuses

        if c["partial"]:
            # La sesión empezó con la posición ya abierta: sin la entrada no hay ciclo que evaluar.
            c["status"], c["effective"], c["issues"] = "PARCIAL", False, ["La sesión empezó con la posición abierta."]
            return

        # 1. Entrada ejecutada exactamente como la pidió el bot.
        entry_qty = None
        if not sent(entry):
            fail(entry, "La entrada nunca llegó a Binance.", "SEND_FAILED")
        elif entry["match_status"] not in EXECUTED_OK_STATUSES:
            fail(entry, f"La entrada no se ejecutó como la pidió el bot ({entry['match_status']}).")
        elif not self._qty_exact(entry["requested_qty"], entry["executed_qty"]):
            fail(entry, f"Entrada: cantidad pedida {entry['requested_qty']} ≠ ejecutada {entry['executed_qty']}.",
                 "QTY_MISMATCH", "WARNING")
        else:
            entry_qty = entry["executed_qty"]
        c["entry_qty"] = entry_qty or c["entry_qty"]
        protect_side = {"BUY": "SELL", "SELL": "BUY"}.get((entry.get("side") or "").upper())

        # 2. SL y TP colocados, e iguales en la app y en Binance.
        for label, legs in (("Stop Loss", sls), ("Take Profit", tps)):
            good = [o for o in legs if sent(o) and o["match_status"] in PROTECTION_OK_STATUSES]
            if not good:
                fail(legs[-1] if legs else None, f"No hay {label} colocado y reconocido por Binance en este ciclo.",
                     "MISSING_PROTECTION" if legs else None)
                continue
            for o in good:
                b_side, b_qty = o.get("binance_side"), o.get("binance_orig_qty")
                if protect_side and (o["side"] != protect_side or (b_side and b_side != protect_side)):
                    fail(o, f"{label}: lado {o['side']} (Binance: {b_side}), se esperaba {protect_side}.",
                         "PROTECTION_MISMATCH")
                elif entry_qty and not (self._qty_exact(entry_qty, o["requested_qty"])
                                        and (b_qty is None or self._qty_exact(o["requested_qty"], b_qty))):
                    fail(o, f"{label}: cantidad {o['requested_qty']} (Binance: {b_qty}) ≠ entrada {entry_qty}.",
                         "PROTECTION_MISMATCH")

        # 3. Salida por la misma cantidad que la entrada.
        closed = exit_order is not None
        if closed and exit_order in closes:
            if exit_order["match_status"] == "QTY_MISMATCH" or (
                exit_order["match_status"] in EXECUTED_OK_STATUSES and entry_qty
                and not self._qty_exact(entry_qty, exit_order["executed_qty"])
            ):
                fail(exit_order, f"Salida: Binance ejecutó {exit_order['executed_qty']} de los {entry_qty} de la "
                                 f"posición del bot.", "QTY_MISMATCH", "WARNING")
            elif exit_order["match_status"] not in EXECUTED_OK_STATUSES:
                fail(exit_order, f"La salida no se ejecutó ({exit_order['match_status']}).")
            elif protect_side and exit_order["side"] != protect_side:
                fail(exit_order, f"Salida por el lado {exit_order['side']}, se esperaba {protect_side}.",
                     "PROTECTION_MISMATCH")
        elif closed and entry_qty and not self._qty_exact(entry_qty, exit_order["executed_qty"]):
            fail(exit_order, f"El {exit_order['action']} ejecutó {exit_order['executed_qty']} de {entry_qty}.",
                 "QTY_MISMATCH", "WARNING")
        # Intentos de salida que no llegaron a Binance: quedan como fallidos aunque luego se reintente.
        for o in closes:
            if o is not exit_order and not sent(o):
                o["effective"] = False
        if not closed and not is_last:
            rejected = [o for o in closes if not sent(o)]
            why = f" La orden de salida fue rechazada: {rejected[-1]['details']}" if rejected else ""
            fail(None, "El ciclo terminó sin ninguna salida ejecutada (ni orden de cierre ni SL/TP)." + why)

        # 4. Sin SL/TP huérfanos al salir; con la posición abierta, la protección debe seguir viva.
        for o in protections:
            status = o.get("binance_status")
            if not sent(o) or status is None:
                continue
            if closed and status in LIVE_EXCHANGE_STATUSES:
                fail(o, f"SL/TP HUÉRFANO: la posición ya salió pero {o['action']} sigue vivo en Binance ({status}).",
                     "ORPHAN_PROTECTION")
            elif not closed and is_last and status in ("CANCELED", "EXPIRED", "REJECTED"):
                fail(o, f"{o['action']} {status} en Binance con la posición todavía abierta: sin protección.",
                     "PROTECTION_MISMATCH")

        c["issues"] = issues
        # Un ciclo sin salida solo cuenta como terminado si no es el último (la posición sigue abierta).
        c["status"] = "COMPLETO" if closed else ("EN CURSO" if is_last else "SIN SALIDA")
        c["effective"] = closed and not issues

    # ── Test 2: informe de conciliación de una sesión de monitoreo de un bot ─────

    def build_session_report(
        self,
        session_id: str,
        bot_id: str,
        bot_name: str,
        symbol: str,
        started_at: datetime,
        outcome_cache: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Concilia contra Binance TODAS las órdenes que `bot_id` envió desde `started_at` (naive
        UTC) — Entrada, SL, TP y Salida — y arma el informe de la sesión: embudo creación →
        envío → ejecución, deslizamiento (promedio / máximo / p95), órdenes conciliadas vs.
        fallidas, y la confiabilidad resultante (% de órdenes efectivas, ver EFFECTIVE_STATUSES).

        No modifica el ledger (no marca `reconciled`) ni escribe en `reconciliation_log`: se
        invoca en cada ciclo de la sesión y eso duplicaría filas. El informe completo lo
        persiste `reconciliation.reports.save_session_report`.
        """
        db = SessionLocal()
        try:
            records = (
                db.query(AppOrderRecord)
                .filter(
                    AppOrderRecord.bot_id == bot_id,
                    AppOrderRecord.use_testnet == self.use_testnet,
                    AppOrderRecord.created_at >= started_at,
                )
                .order_by(AppOrderRecord.created_at.asc())
                .all()
            )
            # Se desprenden de la sesión antes de cerrarla (expire_on_commit invalidaría sus
            # atributos tras db.close()).
            db.expunge_all()
        finally:
            db.close()

        orders: List[Dict[str, Any]] = []
        for record in records:
            if record.status != "SENT_OK":
                outcome = {
                    **self._order_detail(record),
                    "match_status": "SEND_FAILED",
                    "severity": "CRITICAL",
                    "details": record.error or "La orden nunca llegó a enviarse a Binance (rechazada antes de salir).",
                    "binance_status": None,
                    "binance_exec_qty": None,
                    "binance_avg_price": None,
                }
            elif outcome_cache is not None and record.app_order_ref in outcome_cache:
                outcome = outcome_cache[record.app_order_ref]
            else:
                outcome = self.reconcile_order(record)
                # Una orden que Binance ya dio por terminada no cambia más: no se vuelve a consultar en cada
                # ciclo. Con varias sesiones en paralelo evita cientos de consultas repetidas por minuto.
                if outcome_cache is not None and outcome.get("binance_status") in TERMINAL_EXCHANGE_STATUSES:
                    outcome_cache[record.app_order_ref] = outcome
            orders.append({
                "app_order_ref": record.app_order_ref,
                "created_at": record.created_at.strftime("%d/%m %H:%M:%S") if record.created_at else None,
                "symbol": outcome.get("symbol"),
                "action": record.action,
                "position_side": outcome.get("position_side"),
                "side": record.side,
                "order_type": record.order_type,
                "requested_qty": record.requested_qty,
                "executed_qty": outcome.get("executed_qty"),
                "requested_price": record.requested_price,
                "reference_price": outcome.get("reference_price"),
                "avg_price": outcome.get("avg_price"),
                "slippage_pct": outcome.get("slippage_pct"),
                "binance_status": outcome.get("binance_status"),
                "match_status": outcome["match_status"],
                "severity": outcome["severity"],
                "details": outcome["details"],
                "binance_order_id": outcome.get("binance_order_id"),
                "created_in_app": outcome["checks"]["created_in_app"],
                "sent_to_binance": outcome["checks"]["sent_to_binance"],
                "executed_in_binance": outcome["checks"]["executed_in_binance"],
                "binance_side": outcome.get("binance_side"),
                "binance_orig_qty": outcome.get("binance_orig_qty"),
                "effective": False,   # lo decide _evaluate_cycles según el papel de la orden en su ciclo
            })

        cycles = self._evaluate_cycles(orders)
        completed = [c for c in cycles if c["status"] in ("COMPLETO", "SIN SALIDA")]
        cycles_effective = sum(1 for c in completed if c["effective"])

        total = len(orders)
        effective = sum(1 for o in orders if o["effective"])
        slippages = sorted(o["slippage_pct"] for o in orders if o["slippage_pct"] is not None)
        if slippages:
            slippage_avg = round(sum(slippages) / len(slippages), 4)
            slippage_max = round(slippages[-1], 4)
            slippage_p95 = round(slippages[max(0, math.ceil(0.95 * len(slippages)) - 1)], 4)
        else:
            slippage_avg = slippage_max = slippage_p95 = None

        # Confiabilidad del bot = % de CICLOS completos (entrada → SL/TP → salida) que cumplieron todo, no de
        # órdenes sueltas: cada orden de un ciclo solo tiene sentido dentro de él.
        reliability_pct = round(cycles_effective / len(completed) * 100.0, 2) if completed else None
        return {
            "session_id": session_id,
            "bot_id": bot_id,
            "bot_name": bot_name,
            "symbol": symbol,
            "use_testnet": self.use_testnet,
            "started_at": started_at,
            "total_orders": total,
            "created_count": sum(1 for o in orders if o["created_in_app"]),
            "sent_count": sum(1 for o in orders if o["sent_to_binance"]),
            "executed_count": sum(1 for o in orders if o["executed_in_binance"]),
            "effective_count": effective,
            "failed_count": total - effective,
            "slippage_failed_count": sum(1 for o in orders if o["match_status"] == "SLIPPAGE_EXCEEDED"),
            "long_count": sum(1 for o in orders if o["position_side"] == "LONG"),
            "short_count": sum(1 for o in orders if o["position_side"] == "SHORT"),
            "slippage_avg_pct": slippage_avg,
            "slippage_max_pct": slippage_max,
            "slippage_p95_pct": slippage_p95,
            "slippage_tolerance_pct": self.slippage_tolerance_pct,
            "reliability_pct": reliability_pct,
            "reliability_label": _reliability_label(reliability_pct),
            "cycles_total": len(cycles),
            "cycles_completed": len(completed),
            "cycles_effective": cycles_effective,
            "cycles_failed": len(completed) - cycles_effective,
            "cycles_in_progress": sum(1 for c in cycles if c["status"] == "EN CURSO"),
            "cycles": cycles,
            "orders": orders,
        }
