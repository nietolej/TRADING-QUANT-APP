"""Con varias sesiones de Test 2 en paralelo, las órdenes ya terminadas en Binance no se consultan en cada ciclo."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import reconciliation.models as m
import reconciliation.reconciler as rec
from data_layer.storage import Base


def make_reconciler(monkeypatch, statuses):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[m.AppOrderRecord.__table__])
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(rec, "SessionLocal", Session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = Session()
    for i, ref in enumerate(statuses):
        db.add(m.AppOrderRecord(
            app_order_ref=ref, created_at=now, symbol="BTCUSDT", bot_id="bot1", side="BUY", action="OPEN",
            order_type="MARKET", requested_qty=1.0, reference_price=100.0, use_testnet=True, status="SENT_OK",
            binance_order_id=str(i + 1),
        ))
    db.commit()
    db.close()

    r = rec.OrderReconciler.__new__(rec.OrderReconciler)
    r.use_testnet = True
    r.slippage_tolerance_pct = 0.05
    r.calls = []

    def fake_reconcile(record):
        r.calls.append(record.app_order_ref)
        detail = r._order_detail(record)
        detail["checks"]["executed_in_binance"] = True
        status = statuses[record.app_order_ref]
        return {**detail, "match_status": "MATCHED", "severity": "INFO", "details": "x",
                "binance_status": status, "binance_exec_qty": 1.0, "binance_avg_price": 100.0}

    r.reconcile_order = fake_reconcile
    return r, now - timedelta(minutes=1)


def build(r, since, cache):
    return r.build_session_report("s", "bot1", "Bot", "BTCUSDT", since, outcome_cache=cache)


def test_terminal_orders_are_queried_once_and_open_ones_every_cycle(monkeypatch):
    r, since = make_reconciler(monkeypatch, {"a": "FILLED", "b": "NEW", "c": "CANCELED"})
    cache = {}
    first = build(r, since, cache)
    assert sorted(r.calls) == ["a", "b", "c"] and set(cache) == {"a", "c"}
    r.calls.clear()
    second = build(r, since, cache)
    assert r.calls == ["b"], "solo la orden aún abierta (SL/TP vivo) se vuelve a consultar"
    assert first["total_orders"] == second["total_orders"] == 3
    assert second["effective_count"] == 3


def test_without_cache_everything_is_queried_every_time(monkeypatch):
    r, since = make_reconciler(monkeypatch, {"a": "FILLED", "b": "NEW"})
    build(r, since, None)
    build(r, since, None)
    assert sorted(r.calls) == ["a", "a", "b", "b"]


def test_independent_caches_per_session(monkeypatch):
    r, since = make_reconciler(monkeypatch, {"a": "FILLED"})
    cache_one, cache_two = {}, {}
    build(r, since, cache_one)
    build(r, since, cache_two)
    assert r.calls == ["a", "a"] and "a" in cache_one and "a" in cache_two
