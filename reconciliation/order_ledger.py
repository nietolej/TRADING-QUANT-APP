import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Float, Integer

from data_layer.storage import Base, SessionLocal, engine
from reconciliation.models import AppOrderRecord, ReconciliationRecord, ReconciliationReport

logger = logging.getLogger("OrderLedger")

# Las tablas del ledger viven en el mismo archivo SQLite que el resto de la app (via el
# engine compartido de data_layer.storage), pero se crean desde este módulo separado para
# no acoplar data_layer/storage.py al dominio de reconciliación.
Base.metadata.create_all(
    bind=engine,
    tables=[AppOrderRecord.__table__, ReconciliationRecord.__table__, ReconciliationReport.__table__],
)


def _ensure_columns() -> None:
    """
    Migración aditiva mínima para SQLite: agrega columnas nuevas del modelo (ej. las de
    detalle/slippage añadidas para el Reporte de Tests) a tablas que ya existían de una
    versión anterior de la app, donde `create_all` no las crea porque la tabla ya está
    presente. No-op para cualquier otro motor de base de datos.
    """
    if engine.dialect.name != "sqlite":
        return
    try:
        with engine.connect() as conn:
            for table in (AppOrderRecord.__table__, ReconciliationRecord.__table__, ReconciliationReport.__table__):
                existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table.name})").fetchall()}
                for column in table.columns:
                    if column.name in existing:
                        continue
                    if isinstance(column.type, Float):
                        col_type = "FLOAT"
                    elif isinstance(column.type, Boolean):
                        col_type = "BOOLEAN"
                    elif isinstance(column.type, Integer):
                        col_type = "INTEGER"
                    else:
                        col_type = "TEXT"
                    conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}")
                    logger.info("Columna '%s' agregada a '%s' (migración aditiva).", column.name, table.name)
            conn.commit()
    except Exception as e:
        logger.warning("No se pudieron verificar/agregar columnas nuevas del ledger de reconciliación: %s", e)


_ensure_columns()


def log_app_order(
    symbol: str,
    side: str,
    action: str,
    order_type: str,
    requested_qty: float,
    requested_price: Optional[float],
    use_testnet: bool,
    status: str,
    binance_order_id: Optional[str] = None,
    client_order_id: Optional[str] = None,
    exchange_status: Optional[str] = None,
    executed_qty: Optional[float] = None,
    avg_price: Optional[float] = None,
    reference_price: Optional[float] = None,
    error: Optional[str] = None,
    bot_id: Optional[str] = None,
) -> Optional[str]:
    """
    Registra en el ledger local un intento de orden enviado a Binance (exitoso o fallido).
    Es la única llamada que necesita hacer el código de ejecución (binance_client.py) para
    que una orden quede disponible para reconciliación; por eso NUNCA debe propagar
    excepciones hacia el flujo de trading que la invoca.
    """
    app_order_ref = str(uuid.uuid4())
    db = SessionLocal()
    try:
        record = AppOrderRecord(
            app_order_ref=app_order_ref,
            created_at=datetime.now(timezone.utc),
            symbol=symbol.replace("/", "").upper(),
            bot_id=bot_id,
            side=side.upper(),
            action=action.upper(),
            order_type=order_type.upper(),
            requested_qty=float(requested_qty) if requested_qty is not None else None,
            requested_price=float(requested_price) if requested_price else None,
            reference_price=float(reference_price) if reference_price else None,
            use_testnet=bool(use_testnet),
            status=status,
            binance_order_id=str(binance_order_id) if binance_order_id is not None else None,
            client_order_id=client_order_id,
            exchange_status=exchange_status,
            executed_qty=float(executed_qty) if executed_qty else None,
            avg_price=float(avg_price) if avg_price else None,
            error=error,
            reconciled=False,
            reconciliation_status=None,
        )
        db.add(record)
        db.commit()
        return app_order_ref
    except Exception as e:
        logger.warning("No se pudo registrar orden en el ledger de reconciliación: %s", e)
        try:
            db.rollback()
        except Exception:
            pass
        return None
    finally:
        db.close()
