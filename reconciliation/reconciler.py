import logging
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

# Estados que cuentan como "orden efectiva" (ejecutada correctamente, sin discrepancias) en
# el Reporte de Tests. Todo lo demás cuenta como fallida.
EFFECTIVE_STATUSES = {"MATCHED"}


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
            "slippage_pct": None,
            "checks": {
                "created_in_app": True,
                "sent_to_binance": bool(record.binance_order_id),
                "executed_in_binance": False,
            },
        }

    def reconcile_order(self, record: AppOrderRecord) -> Dict[str, Any]:
        """Consulta Binance para UNA orden del ledger y determina si coincide con lo reportado al enviarla."""
        detail = self._order_detail(record)

        if not record.binance_order_id:
            return {
                **detail,
                "match_status": "ERROR",
                "severity": "WARNING",
                "details": "El registro no tiene binance_order_id (la orden nunca llegó a crearse en Binance).",
            }

        try:
            order_id = int(record.binance_order_id)
        except (TypeError, ValueError):
            order_id = record.binance_order_id

        try:
            exchange_order = self.client.client.futures_get_order(symbol=record.symbol, orderId=order_id)
        except Exception as e:
            return {
                **detail,
                "match_status": "MISSING_ON_BINANCE",
                "severity": "CRITICAL",
                "details": (
                    f"La app registró el envío de la orden {record.binance_order_id} ({record.symbol}) pero "
                    f"Binance no la reconoce: {format_binance_error(e)}"
                ),
            }

        status = str(exchange_order.get("status", "")).upper()
        exec_qty = float(exchange_order.get("executedQty", 0.0) or 0.0)
        avg_price = float(exchange_order.get("avgPrice", 0.0) or 0.0)
        detail["executed_qty"] = exec_qty or detail["executed_qty"]
        detail["avg_price"] = avg_price or detail["avg_price"]

        if status in FAILED_STATUSES:
            return {
                **detail,
                "match_status": "STATUS_MISMATCH",
                "severity": "CRITICAL",
                "details": (
                    f"Orden {record.binance_order_id} ({record.symbol}) en estado terminal fallido "
                    f"'{status}' en Binance: la app la contaba como ejecutada."
                ),
            }

        if status not in FILLED_STATUSES and status != "NEW":
            return {
                **detail,
                "match_status": "STATUS_MISMATCH",
                "severity": "WARNING",
                "details": f"Orden {record.binance_order_id} ({record.symbol}) en estado inesperado '{status}'.",
            }

        if status in FILLED_STATUSES:
            # A partir de aquí la orden sí se creó en la app, sí se envió y sí se ejecutó en
            # Binance — las tres condiciones que valida esta reconciliación por orden.
            detail["checks"]["executed_in_binance"] = True

            if not self._within_tolerance(record.requested_qty, exec_qty, self.qty_tolerance_pct):
                return {
                    **detail,
                    "match_status": "QTY_MISMATCH",
                    "severity": "WARNING",
                    "details": (
                        f"Cantidad solicitada {record.requested_qty} vs ejecutada {exec_qty} en Binance "
                        f"para la orden {record.binance_order_id} ({record.symbol})."
                    ),
                }
            if record.order_type == "LIMIT" and avg_price and not self._within_tolerance(
                record.requested_price, avg_price, self.price_tolerance_pct
            ):
                return {
                    **detail,
                    "match_status": "PRICE_MISMATCH",
                    "severity": "WARNING",
                    "details": (
                        f"Precio solicitado {record.requested_price} vs avgPrice ejecutado {avg_price} en "
                        f"Binance para la orden {record.binance_order_id} ({record.symbol})."
                    ),
                }

            # Deslizamiento: compara el precio realmente ejecutado contra el precio de
            # referencia tomado justo antes de enviar la orden (cubre también MARKET, que no
            # tiene requested_price). Sin referencia disponible, no se puede evaluar slippage.
            reference = record.reference_price or record.requested_price
            if reference and avg_price:
                slippage_pct = abs(avg_price - reference) / abs(reference) * 100.0
                detail["slippage_pct"] = round(slippage_pct, 4)
                if slippage_pct > self.slippage_tolerance_pct:
                    return {
                        **detail,
                        "match_status": "SLIPPAGE_EXCEEDED",
                        "severity": "CRITICAL" if slippage_pct > self.slippage_tolerance_pct * 3 else "WARNING",
                        "details": (
                            f"Deslizamiento de {slippage_pct:.3f}% (tolerancia {self.slippage_tolerance_pct}%) "
                            f"en la orden {record.binance_order_id} ({record.symbol}): referencia {reference} "
                            f"vs ejecutado {avg_price}."
                        ),
                    }

        return {
            **detail,
            "match_status": "MATCHED",
            "severity": "INFO",
            "details": f"Orden {record.binance_order_id} ({record.symbol}) confirmada en Binance con estado {status}.",
        }

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
                pass

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

    # ── Modo Test 3: verificación creación → envío → ejecución en Binance ─────

    def run_execution_verification_test(
        self, symbol: str = "BTC/USDT", quantity: float = 0.001, side: str = "long"
    ) -> Dict[str, Any]:
        """
        Prueba puntual (sin SL/TP) de las tres condiciones que debe cumplir toda orden:
        1) que quede CREADA en el ledger local de la app (`app_order_ledger`, vía
           `_log_order_to_ledger` dentro de `place_futures_order`),
        2) que se haya ENVIADO a Binance (status SENT_OK + binance_order_id), y
        3) que Binance confirme que se EJECUTÓ realmente (estado FILLED al reconciliar).

        A diferencia de "Modo Test 1" (que además coloca y retira SL/TP), este test es el
        ciclo mínimo de una orden real: abre con una orden MARKET, la concilia de inmediato
        (lo que también calcula el deslizamiento contra el precio de referencia) y cierra la
        posición de prueba para no dejarla abierta. Solo corre en Testnet.
        """
        if not self.use_testnet:
            return {
                "success": False,
                "error": "El Modo Test 3 solo puede correr contra Testnet (nunca Mainnet) por seguridad.",
                "steps": [],
                "orders": [],
            }

        binance_symbol = symbol.replace("/", "").upper()
        steps: List[Dict[str, Any]] = []

        def _step(name: str, ok: bool, detail: str):
            steps.append({"step": name, "ok": ok, "detail": detail})
            return ok

        entry_order, entry_err = self.client.place_futures_order(
            symbol=symbol, side=side, quantity=quantity, order_type="MARKET", verify_execution=True
        )
        entry_ok = bool(entry_order) and not entry_err
        _step(
            "1. Orden creada en la app y enviada a Binance",
            entry_ok,
            entry_err or f"orderId={entry_order.get('orderId') if entry_order else None}",
        )
        if not entry_ok:
            return {"success": False, "symbol": binance_symbol, "steps": steps, "orders": []}

        # Reconciliar de inmediato confirma el paso 3 (ejecución real en Binance) y calcula
        # el deslizamiento de esta misma orden contra el precio de referencia registrado.
        recon_results = self.reconcile_pending(lookback_hours=0.05, limit=5)
        entry_recon = next((r for r in recon_results if r.get("binance_order_id") == str(entry_order.get("orderId"))), None)
        executed_ok = bool(entry_recon and entry_recon["checks"]["executed_in_binance"])
        _step(
            "2. Ejecución confirmada por Binance al reconciliar",
            executed_ok,
            (entry_recon or {}).get("details", "No se encontró el resultado de conciliación de la orden."),
        )

        # Limpieza: cerrar la posición de prueba para no dejarla abierta en Testnet.
        close_order, close_err = self.client.close_futures_position(
            symbol=symbol, side=side, quantity=quantity, order_type="MARKET", verify_execution=True
        )
        _step(
            "3. Cierre de la posición de prueba",
            bool(close_order) and not close_err,
            close_err or f"orderId={close_order.get('orderId') if close_order else None}",
        )
        close_recon = []
        if close_order:
            close_recon = self.reconcile_pending(lookback_hours=0.05, limit=5)

        orders = (recon_results or []) + (close_recon or [])
        overall_ok = all(s["ok"] for s in steps)
        return {
            "success": overall_ok,
            "symbol": binance_symbol,
            "steps": steps,
            "orders": orders,
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
        if reliability_pct is None:
            reliability_label = "Sin datos"
        elif reliability_pct >= 95:
            reliability_label = "Alta"
        elif reliability_pct >= 80:
            reliability_label = "Media"
        else:
            reliability_label = "Baja"

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
