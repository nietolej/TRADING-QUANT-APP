from typing import List, Optional

from fastapi import APIRouter, Query

from data_layer.storage import SessionLocal
from reconciliation.models import AppOrderRecord, ReconciliationRecord
from reconciliation.reconciler import OrderReconciler

router = APIRouter()


@router.post("/run")
def run_reconciliation(
    symbols: List[str] = Query(default=["BTCUSDT", "ETHUSDT"]),
    hours: float = 24.0,
    real: bool = False,
):
    """Dispara una reconciliación bajo demanda contra Binance (Demo/Testnet salvo real=true)."""
    reconciler = OrderReconciler(use_testnet=not real)
    return reconciler.run(symbols=symbols, lookback_hours=hours)


@router.get("/history")
def get_reconciliation_history(limit: int = 100, symbol: Optional[str] = None):
    """Últimos resultados de reconciliación persistidos (tabla reconciliation_log)."""
    db = SessionLocal()
    try:
        q = db.query(ReconciliationRecord).order_by(ReconciliationRecord.run_at.desc())
        if symbol:
            q = q.filter(ReconciliationRecord.symbol == symbol.replace("/", "").upper())
        rows = q.limit(limit).all()
        return [
            {
                "id": r.id,
                "run_at": r.run_at,
                "symbol": r.symbol,
                "app_order_ref": r.app_order_ref,
                "binance_order_id": r.binance_order_id,
                "match_status": r.match_status,
                "severity": r.severity,
                "details": r.details,
            }
            for r in rows
        ]
    finally:
        db.close()


@router.get("/ledger")
def get_app_order_ledger(limit: int = 100, symbol: Optional[str] = None, unreconciled_only: bool = False):
    """Órdenes registradas por la app al enviarlas a Binance (tabla app_order_ledger)."""
    db = SessionLocal()
    try:
        q = db.query(AppOrderRecord).order_by(AppOrderRecord.created_at.desc())
        if symbol:
            q = q.filter(AppOrderRecord.symbol == symbol.replace("/", "").upper())
        if unreconciled_only:
            q = q.filter(AppOrderRecord.reconciled == False)  # noqa: E712
        rows = q.limit(limit).all()
        return [
            {
                "id": r.id,
                "app_order_ref": r.app_order_ref,
                "created_at": r.created_at,
                "symbol": r.symbol,
                "side": r.side,
                "action": r.action,
                "order_type": r.order_type,
                "requested_qty": r.requested_qty,
                "requested_price": r.requested_price,
                "use_testnet": r.use_testnet,
                "status": r.status,
                "binance_order_id": r.binance_order_id,
                "exchange_status": r.exchange_status,
                "executed_qty": r.executed_qty,
                "avg_price": r.avg_price,
                "reconciled": r.reconciled,
                "reconciliation_status": r.reconciliation_status,
            }
            for r in rows
        ]
    finally:
        db.close()
