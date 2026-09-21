"""Test 2: confiabilidad honesta, exposición de la cuenta (posición sin dueño / condicionales vivas) y sesiones reanudables."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import reconciliation.models as m
import reconciliation.reconciler as rec
import reconciliation.reports as reports
from data_layer.storage import Base


def _session_factory(*tables):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in tables])
    return sessionmaker(bind=engine)


# ── Confiabilidad ────────────────────────────────────────────────────────────

def test_perfect_but_small_sample_is_not_high():
    label, lower, notes = rec._assess_reliability(100.0, completed=14, effective=14, slippage_failed=0)
    assert label == "Media"
    assert lower == pytest.approx(78.5, abs=0.1)
    assert "Muestra pequeña" in notes[0]


def test_large_clean_sample_is_high():
    label, lower, notes = rec._assess_reliability(100.0, completed=40, effective=40, slippage_failed=0)
    assert (label, notes) == ("Alta", [])
    assert lower > 90


def test_slippage_over_tolerance_caps_high_reliability():
    label, _, notes = rec._assess_reliability(100.0, completed=40, effective=40, slippage_failed=2)
    assert label == "Media" and "deslizamiento" in notes[0]


def test_low_reliability_is_never_raised_and_no_data_has_no_notes():
    assert rec._assess_reliability(60.0, 40, 24, 0)[0] == "Baja"
    assert rec._assess_reliability(None, 0, 0, 0) == ("Sin datos", None, [])


# ── Informe: long/short cuentan entradas ─────────────────────────────────────

def test_long_short_count_entries_not_every_order(monkeypatch):
    Session = _session_factory(m.AppOrderRecord)
    monkeypatch.setattr(rec, "SessionLocal", Session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = Session()
    for i, (action, side) in enumerate([("OPEN", "BUY"), ("CLOSE", "SELL"), ("OPEN", "SELL"), ("CLOSE", "BUY")]):
        db.add(m.AppOrderRecord(
            app_order_ref=f"r{i}", created_at=now + timedelta(seconds=i), symbol="BTCUSDT", bot_id="b", side=side,
            action=action, order_type="MARKET", requested_qty=1.0, use_testnet=True, status="SENT_OK",
            binance_order_id=str(i),
        ))
    db.commit()
    db.close()

    r = rec.OrderReconciler.__new__(rec.OrderReconciler)
    r.use_testnet, r.slippage_tolerance_pct = True, 0.05

    def fake(record):
        detail = r._order_detail(record)
        detail["checks"]["executed_in_binance"] = True
        return {**detail, "match_status": "MATCHED", "severity": "INFO", "details": "x", "binance_status": "FILLED"}

    r.reconcile_order = fake
    report = r.build_session_report("s", "b", "Bot", "BTCUSDT", now - timedelta(minutes=1))
    assert (report["long_count"], report["short_count"]) == (1, 1)
    assert report["reliability_label"] != "Alta" and report["reliability_note"]


# ── Exposición de la cuenta ──────────────────────────────────────────────────

class FakeApi:
    def __init__(self, net, algo=(), orders=(), fail=False):
        self.net, self.algo, self.orders, self.fail = net, list(algo), list(orders), fail

    def futures_position_information(self, symbol):
        if self.fail:
            raise RuntimeError("sin red")
        return [{"positionAmt": str(self.net)}]

    def futures_get_open_algo_orders(self, symbol):
        return self.algo

    def futures_get_open_orders(self, symbol):
        return self.orders


def exposure_reconciler(monkeypatch, api, ledger_orders=()):
    Session = _session_factory(m.AppOrderRecord)
    monkeypatch.setattr(rec, "SessionLocal", Session)
    db = Session()
    for order_id, bot_id in ledger_orders:
        db.add(m.AppOrderRecord(
            app_order_ref=f"ref{order_id}", created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            symbol="BTCUSDT", bot_id=bot_id, side="SELL", action="TAKE_PROFIT", order_type="TAKE_PROFIT",
            requested_qty=0.0011, use_testnet=True, status="SENT_OK", binance_order_id=str(order_id),
        ))
    db.commit()
    db.close()
    r = rec.OrderReconciler.__new__(rec.OrderReconciler)
    r.use_testnet = True
    r.client = SimpleNamespace(client=api)
    return r


def algo(order_id, side="BUY"):
    return {"algoId": order_id, "side": side, "orderType": "TAKE_PROFIT", "quantity": "0.0011", "triggerPrice": "83838.4"}


def long_bot(quantity=0.0011):
    return {"bot_id": "long", "name": "LONG", "side": "long", "quantity": quantity}


def test_matching_account_is_ok(monkeypatch):
    r = exposure_reconciler(monkeypatch, FakeApi(0.0011, algo=[algo(7, "SELL")]), ledger_orders=[(7, "long")])
    result = r.check_account_exposure("BTC/USDT", [long_bot()])
    assert result["severity"] == "OK" and result["issues"] == []
    assert result["binance_net"] == result["expected_net"] == 0.0011


def test_position_without_owner_is_critical(monkeypatch):
    """El caso real: los bots están planos y la cuenta arrastra +0.0011."""
    r = exposure_reconciler(monkeypatch, FakeApi(0.0011))
    result = r.check_account_exposure("BTCUSDT", [])
    assert result["severity"] == "CRITICAL"
    assert result["difference"] == pytest.approx(0.0011)
    assert "sin dueño" in result["issues"][0]["text"]


def test_short_bot_counts_negative(monkeypatch):
    r = exposure_reconciler(monkeypatch, FakeApi(-0.0011))
    short = {"bot_id": "short", "name": "SHORT", "side": "short", "quantity": 0.0011}
    assert r.check_account_exposure("BTCUSDT", [short])["severity"] == "OK"
    assert r.check_account_exposure("BTCUSDT", [long_bot()])["severity"] == "CRITICAL"


def test_live_conditional_without_ledger_record_is_flagged(monkeypatch):
    r = exposure_reconciler(monkeypatch, FakeApi(0.0, algo=[algo(99)]))
    result = r.check_account_exposure("BTCUSDT", [])
    assert result["severity"] == "WARNING"
    assert result["conditionals"][0]["state"] == "SIN_REGISTRO"


def test_live_conditional_of_flat_bot_is_orphan(monkeypatch):
    r = exposure_reconciler(monkeypatch, FakeApi(0.0, algo=[algo(5)]), ledger_orders=[(5, "long")])
    result = r.check_account_exposure("BTCUSDT", [])
    assert result["severity"] == "WARNING" and result["conditionals"][0]["state"] == "HUERFANA"


def test_unknown_bot_positions_never_claims_a_mismatch(monkeypatch):
    """Daemon apagado: no se sabe qué esperan los bots, así que no se acusa de diferencia."""
    r = exposure_reconciler(monkeypatch, FakeApi(0.0011))
    result = r.check_account_exposure("BTCUSDT", None)
    assert result["severity"] == "UNKNOWN" and result["expected_net"] is None


def test_binance_failure_is_unknown_not_flat(monkeypatch):
    r = exposure_reconciler(monkeypatch, FakeApi(0.0, fail=True))
    result = r.check_account_exposure("BTCUSDT", [])
    assert result["severity"] == "UNKNOWN" and result["binance_net"] is None


# ── Sesiones interrumpidas: finalizar y reanudar ─────────────────────────────

def stale_report(monkeypatch):
    Session = _session_factory(m.ReconciliationReport)
    monkeypatch.setattr(reports, "SessionLocal", Session)
    long_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    db = Session()
    db.add(m.ReconciliationReport(
        session_id="s1", bot_id="b", bot_name="Bot", symbol="BTCUSDT", status="RUNNING",
        started_at=long_ago - timedelta(hours=2), updated_at=long_ago,
    ))
    db.commit()
    db.close()
    return long_ago


def report_dict(**extra):
    return {"session_id": "s1", "bot_id": "b", "orders": [], "cycles": [], **extra}


def test_stale_running_session_shows_interrupted_and_can_be_finalized(monkeypatch):
    long_ago = stale_report(monkeypatch)
    assert reports.get_session_report("s1")["status"] == "INTERRUMPIDA"
    assert reports.finalize_session_report("s1") is True
    finished = reports.get_session_report("s1")
    assert finished["status"] == "FINISHED" and finished["ended_at"] == long_ago
    assert reports.finalize_session_report("s1") is False   # ya no está en curso
    assert reports.finalize_session_report("nope") is False


def test_resuming_reopens_the_same_report(monkeypatch):
    stale_report(monkeypatch)
    exposure = {"severity": "CRITICAL", "issues": [{"severity": "CRITICAL", "text": "x"}]}
    assert reports.save_session_report(report_dict(exposure=exposure, exposure_severity="CRITICAL"))
    resumed = reports.get_session_report("s1")
    assert resumed["status"] == "RUNNING" and resumed["ended_at"] is None
    assert resumed["exposure"] == exposure and resumed["exposure_severity"] == "CRITICAL"
    assert len(reports.list_session_reports()) == 1
    # Detenerla de nuevo la deja FINISHED con fecha de fin.
    reports.save_session_report(report_dict(), finished=True)
    assert reports.get_session_report("s1")["ended_at"] is not None
