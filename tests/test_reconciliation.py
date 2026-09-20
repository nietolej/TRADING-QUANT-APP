"""
Tests del módulo de conciliación (`reconciliation/`), enfocados en los estados de orden que
reporta Binance y en cómo `OrderReconciler` los clasifica — en particular los estados que
pueden confundirse con una falla del bot cuando en realidad son comportamiento esperado
(TP/SL cancelado, llenado parcial dentro de tolerancia) y viceversa (una cancelación real que
no debe pasar como "esperada").

`reconcile_order` no toca la base de datos: recibe un `AppOrderRecord` ya construido y solo
consulta Binance vía `self.client.client.futures_get_order`, así que esos tests lo mockean
directamente sin necesidad de una base de datos real.

`find_orphan_trades` y `reconcile_pending` sí leen/escriben en `app_order_ledger` y
`reconciliation_log`, así que usan la fixture `isolated_db`, que apunta `SessionLocal` a un
SQLite en memoria propio de cada test para no tocar `data/trading_quant.db`.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DB_URL", "sqlite:///:memory:")

import reconciliation.reconciler as reconciler_module
from reconciliation.models import AppOrderRecord, ReconciliationRecord
from reconciliation.reconciler import EFFECTIVE_STATUSES, OrderReconciler
from data_layer.storage import Base


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def reconciler():
    r = OrderReconciler(use_testnet=True, notify=False)
    r.client.client = MagicMock()
    return r


@pytest.fixture
def isolated_db(monkeypatch):
    """Aísla `SessionLocal` a un SQLite en memoria propio del test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine, tables=[AppOrderRecord.__table__, ReconciliationRecord.__table__])
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(reconciler_module, "SessionLocal", TestSessionLocal)
    return TestSessionLocal


def make_record(**overrides) -> AppOrderRecord:
    defaults = dict(
        app_order_ref=str(uuid.uuid4()),
        created_at=datetime.now(timezone.utc),
        symbol="BTCUSDT",
        side="BUY",
        action="OPEN",
        order_type="MARKET",
        requested_qty=0.001,
        requested_price=None,
        reference_price=60000.0,
        use_testnet=True,
        status="SENT_OK",
        binance_order_id="100",
        exchange_status="NEW",
    )
    defaults.update(overrides)
    return AppOrderRecord(**defaults)


def exchange_order(status: str, executed_qty: float = 0.001, avg_price: float = 60000.0) -> dict:
    return {"status": status, "executedQty": executed_qty, "avgPrice": avg_price}


# ── P0: clasificación de estados en reconcile_order ─────────────────────────

class TestReconcileOrderStatusClassification:
    def test_take_profit_canceled_is_expected(self, reconciler):
        record = make_record(action="TAKE_PROFIT")
        reconciler.client.client.futures_get_order.return_value = exchange_order("CANCELED")

        result = reconciler.reconcile_order(record)

        assert result["match_status"] == "CANCELED_AS_EXPECTED"
        assert result["severity"] == "INFO"
        assert result["match_status"] in EFFECTIVE_STATUSES

    def test_stop_loss_canceled_is_expected(self, reconciler):
        record = make_record(action="STOP_LOSS")
        reconciler.client.client.futures_get_order.return_value = exchange_order("CANCELED")

        result = reconciler.reconcile_order(record)

        assert result["match_status"] == "CANCELED_AS_EXPECTED"
        assert result["severity"] == "INFO"

    def test_open_order_canceled_is_a_real_failure(self, reconciler):
        """Una orden OPEN/CLOSE cancelada NO es el caso esperado de TP/SL: no debe reclasificarse."""
        record = make_record(action="OPEN")
        reconciler.client.client.futures_get_order.return_value = exchange_order("CANCELED")

        result = reconciler.reconcile_order(record)

        assert result["match_status"] == "STATUS_MISMATCH"
        assert result["severity"] == "CRITICAL"

    def test_close_order_canceled_is_a_real_failure(self, reconciler):
        record = make_record(action="CLOSE")
        reconciler.client.client.futures_get_order.return_value = exchange_order("CANCELED")

        result = reconciler.reconcile_order(record)

        assert result["match_status"] == "STATUS_MISMATCH"
        assert result["severity"] == "CRITICAL"

    @pytest.mark.parametrize("action", ["TAKE_PROFIT", "STOP_LOSS", "OPEN", "CLOSE"])
    def test_rejected_is_always_a_critical_failure(self, reconciler, action):
        """La excepción a STATUS_MISMATCH es solo por CANCELED en TP/SL, no por REJECTED."""
        record = make_record(action=action)
        reconciler.client.client.futures_get_order.return_value = exchange_order("REJECTED")

        result = reconciler.reconcile_order(record)

        assert result["match_status"] == "STATUS_MISMATCH"
        assert result["severity"] == "CRITICAL"

    @pytest.mark.parametrize("action", ["TAKE_PROFIT", "STOP_LOSS", "OPEN", "CLOSE"])
    def test_expired_is_always_a_critical_failure(self, reconciler, action):
        record = make_record(action=action)
        reconciler.client.client.futures_get_order.return_value = exchange_order("EXPIRED")

        result = reconciler.reconcile_order(record)

        assert result["match_status"] == "STATUS_MISMATCH"
        assert result["severity"] == "CRITICAL"

    def test_unexpected_status_is_a_warning_not_critical(self, reconciler):
        record = make_record()
        reconciler.client.client.futures_get_order.return_value = exchange_order("PENDING_CANCEL")

        result = reconciler.reconcile_order(record)

        assert result["match_status"] == "STATUS_MISMATCH"
        assert result["severity"] == "WARNING"

    def test_new_status_currently_falls_through_to_matched(self, reconciler):
        """
        Comportamiento ACTUAL documentado, no necesariamente el deseado: una orden todavía en
        estado NEW (aún no ejecutada) no está contemplada aparte y cae en el `return` final de
        `reconcile_order` como MATCHED/INFO, exactamente igual que una orden sí ejecutada. Es un
        falso positivo en el sentido opuesto al de TP/SL cancelado (orden pendiente reportada
        como confirmada). Este test protege el comportamiento actual; si se decide corregirlo,
        debe actualizarse junto con la corrección.
        """
        record = make_record()
        reconciler.client.client.futures_get_order.return_value = exchange_order("NEW", executed_qty=0.0, avg_price=0.0)

        result = reconciler.reconcile_order(record)

        assert result["match_status"] == "MATCHED"
        assert result["checks"]["executed_in_binance"] is False

    def test_missing_binance_order_id(self, reconciler):
        record = make_record(binance_order_id=None)

        result = reconciler.reconcile_order(record)

        assert result["match_status"] == "ERROR"
        assert result["severity"] == "WARNING"

    def test_order_missing_on_binance(self, reconciler):
        record = make_record()
        reconciler.client.client.futures_get_order.side_effect = Exception("Order does not exist.")

        result = reconciler.reconcile_order(record)

        assert result["match_status"] == "MISSING_ON_BINANCE"
        assert result["severity"] == "CRITICAL"


# ── P0: llenado parcial, cantidad y precio ──────────────────────────────────

class TestReconcileOrderQtyAndPrice:
    def test_partially_filled_within_qty_tolerance_matches(self, reconciler):
        record = make_record(requested_qty=1.0)
        reconciler.client.client.futures_get_order.return_value = exchange_order("PARTIALLY_FILLED", executed_qty=0.995)

        result = reconciler.reconcile_order(record)

        assert result["match_status"] == "MATCHED"

    def test_partially_filled_outside_qty_tolerance_is_mismatch(self, reconciler):
        record = make_record(requested_qty=1.0)
        reconciler.client.client.futures_get_order.return_value = exchange_order("PARTIALLY_FILLED", executed_qty=0.5)

        result = reconciler.reconcile_order(record)

        assert result["match_status"] == "QTY_MISMATCH"
        assert result["severity"] == "WARNING"

    def test_limit_order_price_outside_tolerance_is_mismatch(self, reconciler):
        record = make_record(order_type="LIMIT", requested_price=60000.0)
        reconciler.client.client.futures_get_order.return_value = exchange_order("FILLED", avg_price=61000.0)

        result = reconciler.reconcile_order(record)

        assert result["match_status"] == "PRICE_MISMATCH"
        assert result["severity"] == "WARNING"

    def test_market_order_ignores_price_tolerance_check(self, reconciler):
        """El chequeo de PRICE_MISMATCH es solo para LIMIT; una MARKET no debe dispararlo."""
        record = make_record(order_type="MARKET", requested_price=None, reference_price=60000.0)
        reconciler.client.client.futures_get_order.return_value = exchange_order("FILLED", avg_price=60000.0)

        result = reconciler.reconcile_order(record)

        assert result["match_status"] == "MATCHED"


# ── P0: deslizamiento (slippage) ────────────────────────────────────────────

class TestReconcileOrderSlippage:
    def test_slippage_within_tolerance_matches(self, reconciler):
        record = make_record(reference_price=60000.0)
        reconciler.client.client.futures_get_order.return_value = exchange_order("FILLED", avg_price=60020.0)  # 0.033%

        result = reconciler.reconcile_order(record)

        assert result["match_status"] == "MATCHED"
        assert result["slippage_pct"] == pytest.approx(0.0333, abs=1e-3)

    def test_slippage_exceeded_is_warning_below_3x_tolerance(self, reconciler):
        record = make_record(reference_price=60000.0)
        # Tolerancia default 0.05%; 0.08% de deslizamiento (<3x) -> WARNING
        reconciler.client.client.futures_get_order.return_value = exchange_order("FILLED", avg_price=60048.0)

        result = reconciler.reconcile_order(record)

        assert result["match_status"] == "SLIPPAGE_EXCEEDED"
        assert result["severity"] == "WARNING"

    def test_slippage_exceeded_is_critical_above_3x_tolerance(self, reconciler):
        record = make_record(reference_price=60000.0)
        # 0.2% de deslizamiento (>3x la tolerancia default 0.05%) -> CRITICAL
        reconciler.client.client.futures_get_order.return_value = exchange_order("FILLED", avg_price=60120.0)

        result = reconciler.reconcile_order(record)

        assert result["match_status"] == "SLIPPAGE_EXCEEDED"
        assert result["severity"] == "CRITICAL"


# ── P1: detección de huérfanos ──────────────────────────────────────────────

class TestFindOrphanTrades:
    def test_no_client_available_returns_error(self, reconciler, isolated_db):
        reconciler.client.client = None

        results = reconciler.find_orphan_trades("BTC/USDT")

        assert len(results) == 1
        assert results[0]["match_status"] == "ERROR"
        assert results[0]["severity"] == "WARNING"

    def test_binance_exception_returns_error(self, reconciler, isolated_db):
        reconciler.client.client.futures_get_all_orders.side_effect = Exception("API Secret required")

        results = reconciler.find_orphan_trades("BTC/USDT")

        assert len(results) == 1
        assert results[0]["match_status"] == "ERROR"
        assert results[0]["severity"] == "WARNING"

    def test_order_without_app_tag_is_ignored(self, reconciler, isolated_db):
        reconciler.client.client.futures_get_all_orders.return_value = [
            {"clientOrderId": "manual_order_1", "status": "FILLED", "orderId": "1"}
        ]

        results = reconciler.find_orphan_trades("BTC/USDT")

        assert results == []

    def test_app_tagged_filled_order_without_ledger_record_is_orphan(self, reconciler, isolated_db):
        reconciler.client.client.futures_get_all_orders.return_value = [
            {
                "clientOrderId": "QTAPP_abc123",
                "status": "FILLED",
                "orderId": "555",
                "side": "BUY",
                "type": "MARKET",
                "executedQty": "0.001",
                "avgPrice": "60000",
            }
        ]

        results = reconciler.find_orphan_trades("BTC/USDT")

        assert len(results) == 1
        assert results[0]["match_status"] == "ORPHAN_ON_BINANCE"
        assert results[0]["severity"] == "CRITICAL"
        assert results[0]["binance_order_id"] == "555"

    def test_app_tagged_partially_filled_order_without_ledger_record_is_orphan(self, reconciler, isolated_db):
        reconciler.client.client.futures_get_all_orders.return_value = [
            {
                "clientOrderId": "QTAPP_abc456",
                "status": "PARTIALLY_FILLED",
                "orderId": "556",
                "side": "SELL",
                "type": "MARKET",
                "executedQty": "0.0005",
                "avgPrice": "60000",
            }
        ]

        results = reconciler.find_orphan_trades("BTC/USDT")

        assert len(results) == 1
        assert results[0]["match_status"] == "ORPHAN_ON_BINANCE"

    def test_app_tagged_order_already_known_in_ledger_is_not_orphan(self, reconciler, isolated_db):
        db = isolated_db()
        db.add(make_record(symbol="BTCUSDT", binance_order_id="555"))
        db.commit()
        db.close()

        reconciler.client.client.futures_get_all_orders.return_value = [
            {
                "clientOrderId": "QTAPP_abc123",
                "status": "FILLED",
                "orderId": "555",
                "side": "BUY",
                "type": "MARKET",
                "executedQty": "0.001",
                "avgPrice": "60000",
            }
        ]

        results = reconciler.find_orphan_trades("BTC/USDT")

        assert results == []


# ── P1: reconcile_pending ────────────────────────────────────────────────────

class TestReconcilePending:
    def test_send_failed_record_marked_app_side_only_without_calling_binance(self, reconciler, isolated_db):
        db = isolated_db()
        db.add(make_record(status="SEND_FAILED", binance_order_id=None))
        db.commit()
        db.close()

        results = reconciler.reconcile_pending()

        assert results == []
        reconciler.client.client.futures_get_order.assert_not_called()

        db = isolated_db()
        stored = db.query(AppOrderRecord).one()
        assert stored.reconciled is True
        assert stored.reconciliation_status == "APP_SIDE_ONLY"
        db.close()

    def test_sent_ok_record_is_reconciled_and_persisted(self, reconciler, isolated_db):
        db = isolated_db()
        db.add(make_record(status="SENT_OK", binance_order_id="777"))
        db.commit()
        db.close()
        reconciler.client.client.futures_get_order.return_value = exchange_order("FILLED")

        results = reconciler.reconcile_pending()

        assert len(results) == 1
        assert results[0]["match_status"] == "MATCHED"

        db = isolated_db()
        stored = db.query(AppOrderRecord).one()
        assert stored.reconciled is True
        assert stored.reconciliation_status == "MATCHED"
        assert db.query(ReconciliationRecord).count() == 1
        db.close()

    def test_record_outside_lookback_window_is_not_processed(self, reconciler, isolated_db):
        db = isolated_db()
        old_record = make_record(
            status="SENT_OK",
            binance_order_id="888",
            created_at=datetime.now(timezone.utc) - timedelta(hours=100),
        )
        db.add(old_record)
        db.commit()
        db.close()

        results = reconciler.reconcile_pending(lookback_hours=48.0)

        assert results == []
        reconciler.client.client.futures_get_order.assert_not_called()

    def test_record_from_other_network_is_not_processed(self, reconciler, isolated_db):
        db = isolated_db()
        db.add(make_record(status="SENT_OK", binance_order_id="999", use_testnet=False))
        db.commit()
        db.close()

        results = reconciler.reconcile_pending()  # reconciler usa use_testnet=True

        assert results == []
        reconciler.client.client.futures_get_order.assert_not_called()


# ── P2: informe de confiabilidad (build_test_report) ────────────────────────

class TestBuildTestReport:
    def _add_reconciliation_record(self, db, **overrides):
        defaults = dict(
            run_at=datetime.now(timezone.utc),
            symbol="BTCUSDT",
            match_status="MATCHED",
            severity="INFO",
            executed_in_binance=True,
            position_side="LONG",
        )
        defaults.update(overrides)
        db.add(ReconciliationRecord(**defaults))

    def test_created_in_app_counts_send_failed_but_sent_to_binance_does_not(self, reconciler, isolated_db):
        db = isolated_db()
        db.add(make_record(status="SEND_FAILED", binance_order_id=None))
        db.add(make_record(status="SENT_OK", binance_order_id="1"))
        db.commit()
        db.close()

        report = reconciler.build_test_report()

        assert report["created_in_app_count"] == 2
        assert report["sent_to_binance_count"] == 1

    def test_canceled_as_expected_counts_as_effective(self, reconciler, isolated_db):
        db = isolated_db()
        self._add_reconciliation_record(db, match_status="CANCELED_AS_EXPECTED", severity="INFO")
        db.commit()
        db.close()

        report = reconciler.build_test_report()

        assert report["effective_count"] == 1
        assert report["failed_count"] == 0
        assert report["reliability_pct"] == 100.0
        assert report["reliability_label"] == "Alta"

    @pytest.mark.parametrize(
        "effective, total, expected_label",
        [
            (96, 100, "Alta"),
            (85, 100, "Media"),
            (50, 100, "Baja"),
        ],
    )
    def test_reliability_label_thresholds(self, reconciler, isolated_db, effective, total, expected_label):
        db = isolated_db()
        for i in range(total):
            status = "MATCHED" if i < effective else "STATUS_MISMATCH"
            self._add_reconciliation_record(db, match_status=status, binance_order_id=str(i))
        db.commit()
        db.close()

        report = reconciler.build_test_report(limit=total)

        assert report["reliability_label"] == expected_label

    def test_no_records_reports_sin_datos(self, reconciler, isolated_db):
        report = reconciler.build_test_report()

        assert report["total_checked"] == 0
        assert report["reliability_pct"] is None
        assert report["reliability_label"] == "Sin datos"
