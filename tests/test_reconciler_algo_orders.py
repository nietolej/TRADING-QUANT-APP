"""Un SL/TP disparado (Algo Order FINISHED) se concilia con los datos de la orden real que generó."""
from types import SimpleNamespace

import reconciliation.reconciler as rec


def _reconciler(algo, real_order):
    r = rec.OrderReconciler.__new__(rec.OrderReconciler)
    r.use_testnet = True
    r.price_tolerance_pct = 0.5
    r.qty_tolerance_pct = 1.0
    r.slippage_tolerance_pct = 0.05

    def get_order(**kwargs):
        if kwargs["orderId"] != 555:
            raise RuntimeError("Order does not exist")
        return real_order

    r.client = SimpleNamespace(client=SimpleNamespace(
        futures_get_algo_order=lambda **k: algo,
        futures_get_order=get_order,
    ))
    return r


def _record(action="STOP_LOSS"):
    return SimpleNamespace(
        app_order_ref="x", binance_order_id="1000000211846956", symbol="BTCUSDT", action=action, side="SELL",
        order_type="STOP", requested_qty=0.0009, executed_qty=None, requested_price=80460.0, avg_price=None,
        reference_price=None,
    )


def test_triggered_stop_loss_matches_using_real_order_fill():
    algo = {"algoStatus": "FINISHED", "actualOrderId": "555", "actualPrice": "80460.0"}
    real = {"status": "FILLED", "executedQty": "0.0009", "avgPrice": "80460.0"}
    out = _reconciler(algo, real).reconcile_order(_record())
    assert out["match_status"] == "MATCHED", out["details"]
    assert out["binance_exec_qty"] == 0.0009 and out["binance_avg_price"] == 80460.0


def test_triggered_algo_still_flags_a_genuine_quantity_mismatch():
    algo = {"algoStatus": "FINISHED", "actualOrderId": "555"}
    real = {"status": "PARTIALLY_FILLED", "executedQty": "0.0002", "avgPrice": "80460.0"}
    out = _reconciler(algo, real).reconcile_order(_record())
    assert out["match_status"] == "QTY_MISMATCH"
