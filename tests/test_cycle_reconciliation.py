"""
Conciliación por ciclo de UN bot: entrada exacta → SL/TP colocados → salida exacta → sin SL/TP huérfanos,
igual en la app y en Binance. Con dos bots en el mismo símbolo la salida de uno es la entrada del otro; cada
bot se evalúa solo con SUS órdenes.
"""
from reconciliation.reconciler import OrderReconciler


def order(action, side, qty=0.0009, exec_qty=None, status="MATCHED", b_status="FILLED", sent=True, **extra):
    o = {
        "action": action, "side": side, "position_side": "LONG", "created_at": "20/09 20:00:00",
        "requested_qty": qty, "executed_qty": exec_qty if exec_qty is not None else qty,
        "match_status": status, "severity": "INFO", "details": "", "binance_status": b_status,
        "sent_to_binance": sent, "binance_side": side, "binance_orig_qty": qty, "effective": False,
    }
    o.update(extra)
    return o


def protection(action, b_status="CANCELED", qty=0.0009, exec_qty=0.0, side="SELL"):
    status = "CANCELED_AS_EXPECTED" if b_status == "CANCELED" else "MATCHED"
    return order(action, side, qty, exec_qty=exec_qty, status=status, b_status=b_status)


def evaluate(orders):
    return OrderReconciler.__new__(OrderReconciler)._evaluate_cycles(orders)


def good_cycle():
    return [order("OPEN", "BUY"), protection("TAKE_PROFIT"), protection("STOP_LOSS"), order("CLOSE", "SELL")]


def test_full_cycle_is_effective():
    orders = good_cycle()
    (cycle,) = evaluate(orders)
    assert cycle["status"] == "COMPLETO" and cycle["effective"] and cycle["issues"] == []
    assert all(o["effective"] for o in orders)


def test_exit_by_take_profit_fill_is_effective_and_needs_no_close_order():
    orders = [order("OPEN", "BUY"), protection("TAKE_PROFIT", b_status="FILLED", exec_qty=0.0009),
              protection("STOP_LOSS")]
    (cycle,) = evaluate(orders)
    assert cycle["status"] == "COMPLETO" and cycle["effective"]


def test_slippage_alone_does_not_invalidate_the_cycle():
    orders = good_cycle()
    orders[0]["match_status"] = "SLIPPAGE_EXCEEDED"
    assert evaluate(orders)[0]["effective"]


def test_orphan_stop_loss_after_exit_fails_the_cycle():
    orders = good_cycle()
    orders[2] = protection("STOP_LOSS", b_status="NEW")
    (cycle,) = evaluate(orders)
    assert not cycle["effective"]
    assert orders[2]["match_status"] == "ORPHAN_PROTECTION" and not orders[2]["effective"]


def test_partial_exit_fill_fails_the_cycle():
    """El síntoma del 20/09: el cierre pedía 0.0012 y Binance solo ejecutó 0.0003 (posición neta compartida)."""
    orders = [order("OPEN", "SELL", 0.0012),
              protection("TAKE_PROFIT", qty=0.0012, side="BUY"), protection("STOP_LOSS", qty=0.0012, side="BUY"),
              order("CLOSE", "BUY", 0.0012, exec_qty=0.0003, status="QTY_MISMATCH")]
    (cycle,) = evaluate(orders)
    assert not cycle["effective"] and "0.0003" in cycle["issues"][0]


def test_rejected_exit_leaves_cycle_without_exit_when_followed_by_new_entry():
    orders = good_cycle()[:3] + [order("CLOSE", "SELL", exec_qty=0.0, status="SEND_FAILED", b_status=None, sent=False,
                                       details="APIError(code=-2022)")]
    orders += [order("OPEN", "BUY"), protection("TAKE_PROFIT", b_status="NEW"), protection("STOP_LOSS", b_status="NEW")]
    first, second = evaluate(orders)
    assert first["status"] == "SIN SALIDA" and not first["effective"] and "-2022" in first["issues"][0]
    assert second["status"] == "EN CURSO"


def test_entry_without_take_profit_fails():
    orders = [order("OPEN", "BUY"), protection("STOP_LOSS"), order("CLOSE", "SELL")]
    (cycle,) = evaluate(orders)
    assert not cycle["effective"] and any("Take Profit" in i for i in cycle["issues"])


def test_protection_with_wrong_quantity_fails():
    orders = good_cycle()
    orders[1]["requested_qty"] = orders[1]["binance_orig_qty"] = 0.0012
    assert not evaluate(orders)[0]["effective"]


def test_open_position_with_cancelled_protection_is_flagged_but_in_progress():
    orders = [order("OPEN", "BUY"), protection("TAKE_PROFIT", b_status="NEW"), protection("STOP_LOSS", b_status="CANCELED")]
    (cycle,) = evaluate(orders)
    assert cycle["status"] == "EN CURSO" and not cycle["effective"] and cycle["issues"]


def test_session_started_mid_position_is_partial_not_counted():
    (cycle,) = evaluate([order("CLOSE", "SELL")])
    assert cycle["status"] == "PARCIAL" and not cycle["effective"]


def test_stop_loss_fill_plus_market_close_is_a_duplicate_exit():
    """El incidente del 21/09 08:50: el SL ya había cerrado la posición y además se envió un cierre MARKET, que
    abrió un short. Cada orden por separado parecía correcta; solo la suma de salidas (0.0022 vs 0.0011) lo delata."""
    orders = [order("OPEN", "BUY", 0.0011), protection("TAKE_PROFIT", qty=0.0011),
              protection("STOP_LOSS", b_status="FILLED", qty=0.0011, exec_qty=0.0011),
              order("CLOSE", "SELL", 0.0011)]
    (cycle,) = evaluate(orders)
    assert not cycle["effective"]
    assert any("SALIDA DUPLICADA" in i for i in cycle["issues"])
    assert orders[3]["match_status"] == "DUPLICATE_EXIT" and not orders[3]["effective"]


def test_single_exit_is_not_flagged_as_duplicate():
    orders = [order("OPEN", "BUY", 0.0011), protection("TAKE_PROFIT", qty=0.0011),
              protection("STOP_LOSS", b_status="FILLED", qty=0.0011, exec_qty=0.0011)]
    (cycle,) = evaluate(orders)
    assert cycle["effective"] and not any("DUPLICADA" in i for i in cycle["issues"])
