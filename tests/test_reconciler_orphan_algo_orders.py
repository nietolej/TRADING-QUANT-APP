"""Un SL/TP disparado (Algo Order FINISHED) sin registro en el ledger local debe detectarse
como huérfano, aunque su clientOrderId real (asignado por Binance al disparar) no lleve la
etiqueta QTAPP_ de la app -- solo el algoId original la identifica."""
from types import SimpleNamespace

import reconciliation.reconciler as rec


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, known_ids):
        self._known_ids = known_ids
        self.added = []
        self.committed = False

    def query(self, *a, **k):
        return _FakeQuery([(i,) for i in self._known_ids])

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


def _reconciler(orders, algo_orders, real_order, known_ids, monkeypatch):
    r = rec.OrderReconciler.__new__(rec.OrderReconciler)
    r.use_testnet = True

    def get_order(**kwargs):
        return real_order

    r.client = SimpleNamespace(client=SimpleNamespace(
        futures_get_all_orders=lambda **k: orders,
        futures_get_all_algo_orders=lambda **k: algo_orders,
        futures_get_order=get_order,
    ))

    session = _FakeSession(known_ids)
    monkeypatch.setattr(rec, "SessionLocal", lambda: session)
    return r, session


def test_triggered_sl_never_logged_locally_is_flagged_orphan(monkeypatch):
    algo_orders = [{
        "algoId": "1000000999999999",
        "clientAlgoId": "QTAPP_abc123",
        "algoStatus": "FINISHED",
        "side": "SELL",
        "orderType": "STOP_MARKET",
        "actualOrderId": "777",
    }]
    real_order = {"executedQty": "0.001", "avgPrice": "61000.0"}

    r, session = _reconciler(orders=[], algo_orders=algo_orders, real_order=real_order, known_ids=set(), monkeypatch=monkeypatch)
    results = r.find_orphan_trades("BTC/USDT", lookback_hours=24.0)

    assert len(results) == 1
    out = results[0]
    assert out["match_status"] == "ORPHAN_ON_BINANCE"
    assert out["binance_order_id"] == "1000000999999999"
    assert out["executed_qty"] == 0.001 and out["avg_price"] == 61000.0
    assert session.added, "el huérfano debe persistirse en reconciliation_log"


def test_known_algo_id_in_ledger_is_not_flagged(monkeypatch):
    algo_orders = [{
        "algoId": "1000000999999999",
        "clientAlgoId": "QTAPP_abc123",
        "algoStatus": "FINISHED",
        "side": "SELL",
        "orderType": "STOP_MARKET",
        "actualOrderId": "777",
    }]
    real_order = {"executedQty": "0.001", "avgPrice": "61000.0"}

    r, session = _reconciler(
        orders=[], algo_orders=algo_orders, real_order=real_order,
        known_ids={"1000000999999999"}, monkeypatch=monkeypatch,
    )
    results = r.find_orphan_trades("BTC/USDT", lookback_hours=24.0)

    assert results == []
    assert not session.added


def test_working_algo_order_is_not_a_trade_and_is_ignored(monkeypatch):
    algo_orders = [{
        "algoId": "1000000999999999",
        "clientAlgoId": "QTAPP_abc123",
        "algoStatus": "WORKING",
        "side": "SELL",
        "orderType": "STOP_MARKET",
    }]
    r, session = _reconciler(orders=[], algo_orders=algo_orders, real_order={}, known_ids=set(), monkeypatch=monkeypatch)
    results = r.find_orphan_trades("BTC/USDT", lookback_hours=24.0)

    assert results == []
    assert not session.added
