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
        notify: bool = True,
    ):
        self.use_testnet = use_testnet
        self.price_tolerance_pct = price_tolerance_pct
        self.qty_tolerance_pct = qty_tolerance_pct
        self.client = BinanceTestnetClient(use_testnet=use_testnet)
        self.notifier = TelegramNotifier() if notify else None

    # ── Verificación de órdenes conocidas por la app ────────────────────────────

    def _within_tolerance(self, expected: Optional[float], actual: Optional[float], tolerance_pct: float) -> bool:
        if expected is None or actual is None or expected == 0:
            return True
        return abs(actual - expected) / abs(expected) * 100.0 <= tolerance_pct

    def reconcile_order(self, record: AppOrderRecord) -> Dict[str, Any]:
        """Consulta Binance para UNA orden del ledger y determina si coincide con lo reportado al enviarla."""
        if not record.binance_order_id:
            return {
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

        if status in FAILED_STATUSES:
            return {
                "match_status": "STATUS_MISMATCH",
                "severity": "CRITICAL",
                "details": (
                    f"Orden {record.binance_order_id} ({record.symbol}) en estado terminal fallido "
                    f"'{status}' en Binance: la app la contaba como ejecutada."
                ),
            }

        if status not in FILLED_STATUSES and status != "NEW":
            return {
                "match_status": "STATUS_MISMATCH",
                "severity": "WARNING",
                "details": f"Orden {record.binance_order_id} ({record.symbol}) en estado inesperado '{status}'.",
            }

        if status in FILLED_STATUSES:
            if not self._within_tolerance(record.requested_qty, exec_qty, self.qty_tolerance_pct):
                return {
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
                    "match_status": "PRICE_MISMATCH",
                    "severity": "WARNING",
                    "details": (
                        f"Precio solicitado {record.requested_price} vs avgPrice ejecutado {avg_price} en "
                        f"Binance para la orden {record.binance_order_id} ({record.symbol})."
                    ),
                }

        return {
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

                results.append(
                    {
                        "symbol": record.symbol,
                        "app_order_ref": record.app_order_ref,
                        "binance_order_id": record.binance_order_id,
                        **outcome,
                    }
                )

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
            results.append(
                {
                    "symbol": binance_symbol,
                    "app_order_ref": None,
                    "binance_order_id": order_id,
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
                    )
                )
            db.commit()
        except Exception as e:
            logger.warning("No se pudo persistir el resultado de reconciliación: %s", e)
            db.rollback()
        finally:
            db.close()

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
