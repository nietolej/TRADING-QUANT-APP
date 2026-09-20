from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text

from data_layer.storage import Base

# Prefijo usado en el `newClientOrderId` de toda orden enviada a Binance por esta app.
# Permite distinguir en el historial de Binance qué órdenes se originaron aquí (y no en
# otro bot, en la web de Binance, o en otra integración que use las mismas credenciales).
APP_ORDER_TAG_PREFIX = "QTAPP_"


class AppOrderRecord(Base):
    """
    Ledger local, independiente del estado interno de PaperTrader/AlgoExecutionEngine, de
    cada intento de orden que la app envía a Binance. Es la fuente de verdad del lado "app"
    contra la que el reconciliador compara el historial real de Binance: si una orden se
    envía pero no queda aquí, es invisible para la reconciliación.
    """
    __tablename__ = "app_order_ledger"

    id = Column(Integer, primary_key=True, index=True)
    app_order_ref = Column(String, unique=True, index=True)
    created_at = Column(DateTime, index=True)

    symbol = Column(String, index=True)
    side = Column(String)          # BUY / SELL (tal como se envía a Binance)
    action = Column(String)        # OPEN / CLOSE / TAKE_PROFIT / STOP_LOSS
    order_type = Column(String)    # MARKET / LIMIT / STOP / TAKE_PROFIT / ...
    requested_qty = Column(Float)
    requested_price = Column(Float, nullable=True)
    # Precio de mercado tomado justo antes de enviar la orden (incluso para MARKET, que no
    # lleva `requested_price`) — es la referencia contra la que el reconciliador mide el
    # deslizamiento (slippage) real de la ejecución.
    reference_price = Column(Float, nullable=True)
    use_testnet = Column(Boolean, default=True)

    # Resultado del ENVÍO (no de la ejecución final, que valida el reconciliador consultando Binance)
    status = Column(String)  # SENT_OK | SEND_FAILED
    binance_order_id = Column(String, nullable=True, index=True)
    client_order_id = Column(String, nullable=True, index=True)
    exchange_status = Column(String, nullable=True)  # status inicial reportado por Binance al enviar
    executed_qty = Column(Float, nullable=True)
    avg_price = Column(Float, nullable=True)
    error = Column(Text, nullable=True)

    reconciled = Column(Boolean, default=False, index=True)
    reconciliation_status = Column(String, nullable=True)


class ReconciliationRecord(Base):
    """Resultado persistido de cada corrida de reconciliación (una fila por orden/discrepancia evaluada)."""
    __tablename__ = "reconciliation_log"

    id = Column(Integer, primary_key=True, index=True)
    run_at = Column(DateTime, index=True)
    symbol = Column(String, index=True)

    app_order_ref = Column(String, nullable=True, index=True)
    binance_order_id = Column(String, nullable=True, index=True)

    # MATCHED | CANCELED_AS_EXPECTED | PRICE_MISMATCH | QTY_MISMATCH | STATUS_MISMATCH |
    # MISSING_ON_BINANCE | ORPHAN_ON_BINANCE | SLIPPAGE_EXCEEDED | ERROR
    match_status = Column(String, index=True)
    severity = Column(String)  # INFO | WARNING | CRITICAL
    details = Column(Text, nullable=True)

    # Detalle completo de la orden evaluada, copiado del ledger al momento de conciliar, para
    # que el reporte de tests no dependa de que el registro del ledger siga existiendo/intacto.
    position_side = Column(String, nullable=True)  # LONG | SHORT
    side = Column(String, nullable=True)            # BUY | SELL (tal como se envió a Binance)
    action = Column(String, nullable=True)          # OPEN | CLOSE | TAKE_PROFIT | STOP_LOSS
    order_type = Column(String, nullable=True)
    requested_qty = Column(Float, nullable=True)
    executed_qty = Column(Float, nullable=True)
    requested_price = Column(Float, nullable=True)
    avg_price = Column(Float, nullable=True)
    reference_price = Column(Float, nullable=True)
    slippage_pct = Column(Float, nullable=True)

    # Las tres condiciones que el Informe de Confiabilidad reporta para cada orden: que haya
    # quedado creada en el ledger de la app, que se haya enviado a Binance y que Binance
    # confirme que se ejecutó realmente (ver OrderReconciler._order_detail).
    created_in_app = Column(Boolean, nullable=True)
    sent_to_binance = Column(Boolean, nullable=True)
    executed_in_binance = Column(Boolean, nullable=True)
