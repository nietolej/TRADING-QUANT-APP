"""
Persistencia de los informes de conciliación de sesión (Test 2) en `reconciliation_reports`.

El informe en sí lo calcula `OrderReconciler.build_session_report`; aquí solo se guarda
(insert/update por `session_id`) y se consulta, para que el historial sobreviva al cierre de
la página o del servidor.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from data_layer.storage import SessionLocal
from reconciliation.models import ReconciliationReport

logger = logging.getLogger("ReconciliationReports")

# Una sesión RUNNING que no se actualiza desde hace más que esto se muestra como
# INTERRUMPIDA: el test corre en un timer de la página, así que si la pestaña se cerró o el
# servidor se reinició nadie la marcó FINISHED.
STALE_AFTER = timedelta(minutes=5)

_SCALAR_FIELDS = (
    "total_orders", "created_count", "sent_count", "executed_count", "effective_count",
    "failed_count", "slippage_failed_count", "long_count", "short_count",
    "slippage_avg_pct", "slippage_max_pct", "slippage_p95_pct", "slippage_tolerance_pct",
    "reliability_pct", "reliability_label",
    "cycles_total", "cycles_completed", "cycles_effective", "cycles_failed", "cycles_in_progress",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def save_session_report(report: Dict[str, Any], finished: bool = False) -> bool:
    """Inserta o actualiza el informe de la sesión `report["session_id"]`. Devuelve si se pudo guardar."""
    db = SessionLocal()
    try:
        row = db.query(ReconciliationReport).filter(ReconciliationReport.session_id == report["session_id"]).first()
        now = _utcnow()
        if row is None:
            row = ReconciliationReport(
                session_id=report["session_id"],
                bot_id=report.get("bot_id"),
                bot_name=report.get("bot_name"),
                symbol=report.get("symbol"),
                use_testnet=report.get("use_testnet", True),
                started_at=report.get("started_at"),
            )
            db.add(row)
        for field in _SCALAR_FIELDS:
            setattr(row, field, report.get(field))
        row.orders_json = json.dumps(report.get("orders", []), default=str)
        row.cycles_json = json.dumps(report.get("cycles", []), default=str)
        row.updated_at = now
        row.status = "FINISHED" if finished else "RUNNING"
        if finished:
            row.ended_at = now
        db.commit()
        return True
    except Exception as e:
        logger.warning("No se pudo guardar el informe de conciliación: %s", e)
        db.rollback()
        return False
    finally:
        db.close()


def _display_status(row: ReconciliationReport) -> str:
    if row.status == "RUNNING" and row.updated_at and _utcnow() - row.updated_at > STALE_AFTER:
        return "INTERRUMPIDA"
    return row.status or "-"


def _row_to_dict(row: ReconciliationReport, include_orders: bool) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "session_id": row.session_id,
        "bot_id": row.bot_id,
        "bot_name": row.bot_name,
        "symbol": row.symbol,
        "use_testnet": row.use_testnet,
        "started_at": row.started_at,
        "updated_at": row.updated_at,
        "ended_at": row.ended_at,
        "status": _display_status(row),
    }
    for field in _SCALAR_FIELDS:
        data[field] = getattr(row, field)
    if include_orders:
        try:
            data["orders"] = json.loads(row.orders_json) if row.orders_json else []
        except ValueError:
            data["orders"] = []
        try:
            data["cycles"] = json.loads(row.cycles_json) if row.cycles_json else []
        except ValueError:
            data["cycles"] = []
    return data


def list_session_reports(limit: int = 50) -> List[Dict[str, Any]]:
    """Informes guardados, del más reciente al más antiguo (sin el detalle por orden)."""
    db = SessionLocal()
    try:
        rows = (
            db.query(ReconciliationReport)
            .order_by(ReconciliationReport.started_at.desc())
            .limit(limit)
            .all()
        )
        return [_row_to_dict(r, include_orders=False) for r in rows]
    finally:
        db.close()


def get_session_report(session_id: str) -> Optional[Dict[str, Any]]:
    """Informe completo (con el detalle por orden) de una sesión, o None si no existe."""
    db = SessionLocal()
    try:
        row = db.query(ReconciliationReport).filter(ReconciliationReport.session_id == session_id).first()
        return _row_to_dict(row, include_orders=True) if row else None
    finally:
        db.close()
