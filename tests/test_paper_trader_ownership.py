"""
Cada bot es dueño de SU posición aunque varios operen el mismo símbolo y la misma cuenta.

Binance solo tiene una posición NETA por símbolo. Casos que antes fallaban: un bot adoptaba la
posición de otro (y registraba un trade fantasma), cancelaba en masa el SL/TP de los demás y
reponía protección sobre posiciones ajenas o inexistentes.
"""
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import execution_engine.paper_trader as pt
from data_layer.storage import Base
from reconciliation.models import AppOrderRecord

# Los ids de Algo Orders (condicionales) son enormes; el ledger distingue así una orden algo de una estándar.
SL_ID, TP_ID = 10 ** 15 + 1, 10 ** 15 + 2
SL_REF = {"kind": "algo", "id": SL_ID}
TP_REF = {"kind": "algo", "id": TP_ID}


class FakeClient:
    api_key = "k"
    api_secret = "s"

    def __init__(self, net=0.0):
        self.net = net
        self.open = set()
        self.states = {}
        self.calls = []
        self.trades = []            # fills de la cuenta (futures_account_trades)
        self.client = SimpleNamespace(
            futures_position_information=lambda symbol=None: [
                {"positionAmt": str(self.net), "markPrice": "80000", "entryPrice": "80000"}
            ],
            futures_get_order=lambda **k: {"avgPrice": "80100.0"},
            futures_account_trades=lambda **k: list(self.trades),
        )

    @staticmethod
    def order_ref(order):
        if not order:
            return None
        return {"kind": "algo", "id": int(order["algoId"])}

    def get_open_order_refs(self, symbol):
        return set(self.open)

    def get_order_ref_state(self, symbol, ref):
        return self.states.get((ref["kind"], ref["id"]), {"state": "UNKNOWN"})

    def cancel_order_refs(self, symbol, refs):
        self.calls.append(("cancel", tuple((r["kind"], r["id"]) for r in refs)))
        self.open -= {(r["kind"], r["id"]) for r in refs}   # como Binance: lo cancelado deja de estar abierto
        return True, []

    def cancel_all_open_orders(self, symbol):  # no debe usarse jamás desde un bot
        self.calls.append(("cancel_all",))
        return True, None

    def place_futures_sl_tp(self, symbol, side, quantity, sl_price=None, tp_price=None, **kw):
        self.calls.append(("place", sl_price, tp_price))
        return {
            "sl_order": {"algoId": 9001} if sl_price else None,
            "tp_order": {"algoId": 9002} if tp_price else None,
            "errors": [],
        }

    def close_futures_position(self, *a, **k):
        self.close_args, self.close_kwargs = a, k
        self.calls.append(("close_order",))
        return {"orderId": 1, "avgPrice": "80000", "status": "FILLED"}, None


def make_bot(client, bot_id="bot_a", side="long", qty=0.0009, entry=80500.0):
    bot = pt.PaperTrader(
        strategy_yaml_path="config/strategies/ema_long.yaml", initial_balance=100.0, currency="USDT",
        use_testnet=True, custom_timeframe="1m", custom_symbol="BTC/USDT", bot_id=bot_id, name=bot_id,
        custom_parameters={"FAST": 1, "LOW": 10, "SL": 0.5, "TP": 1.0},
    )
    bot._client = client
    bot._save_state = lambda: None
    bot._notify = lambda *a, **k: None
    bot._trigger_critical_order_alert = lambda *a, **k: None
    bot._last_open_ts = time.time() - 600
    pos = pt.Position(side, entry, qty, datetime.now(timezone.utc) - timedelta(minutes=10))
    pos.sl_price, pos.tp_price = entry * 0.995, entry * 1.01
    pos.sl_ref, pos.tp_ref = dict(SL_REF), dict(TP_REF)
    bot.position = pos
    return bot


@pytest.fixture(autouse=True)
def _fresh_bot_registry():
    """Cada test parte de un registro de bots vacío: la posición de un bot de otro test no cuenta como 'ajena'."""
    pt.PaperTrader._ALL_BOTS.clear()
    yield
    pt.PaperTrader._ALL_BOTS.clear()


# ── Un bot no toca las órdenes de otro ─────────────────────────────────────────

def test_cancelling_protection_only_touches_own_orders():
    client = FakeClient(net=0.0018)
    bot = make_bot(client)
    bot._cancel_own_protection()
    assert client.calls == [("cancel", (("algo", SL_ID), ("algo", TP_ID)))]
    assert not any(c[0] == "cancel_all" for c in client.calls)


def test_stop_with_open_position_keeps_protection_and_never_cancels_all():
    client = FakeClient(net=0.0009)
    client.stop = lambda: None
    bot = make_bot(client)
    bot.is_running = True
    bot.stop()
    assert client.calls == [], "detener el bot con posición abierta no debe cancelar su SL/TP ni el de nadie"


def test_open_position_does_not_cancel_other_bots_orders(monkeypatch):
    """Abrir una posición ya no llama a cancel_all_open_orders (antes borraba el SL/TP de los demás)."""
    import inspect
    assert "cancel_all_open_orders" not in inspect.getsource(pt.PaperTrader._open_position)
    assert "cancel_all_open_orders" not in inspect.getsource(pt.PaperTrader._close_position)
    assert "cancel_all_open_orders" not in inspect.getsource(pt.PaperTrader.stop)


# ── Cierre por SL/TP ejecutado en el exchange ──────────────────────────────────

def test_filled_stop_loss_closes_own_position_at_real_price_without_sending_close_order():
    client = FakeClient(net=0.0009)
    client.open = {("algo", TP_ID)}                       # el TP sigue vivo; el SL ya no
    client.states[("algo", SL_ID)] = {"state": "FILLED", "avg_price": 80097.5, "executed_qty": 0.0009}
    bot = make_bot(client)
    bot._sync_own_position(net_amt=0.0009, mark_p=80100.0)
    assert bot.position is None
    trade = bot.trade_history[-1]
    assert trade["reason"] == "SL" and trade["exit_price"] == 80097.5
    assert ("close_order",) not in client.calls, "el exchange ya cerró: no se envía otra orden de cierre"
    assert client.calls == [("cancel", (("algo", SL_ID), ("algo", TP_ID)))], "solo limpia SUS órdenes sobrantes"


def test_other_bots_position_does_not_mask_or_hijack_this_bots_state():
    """Cuenta neta de 2 bots long (0.0018). Se ejecuta el SL de A: B queda intacto."""
    client_a, client_b = FakeClient(net=0.0018), FakeClient(net=0.0018)
    a, b = make_bot(client_a, "bot_a"), make_bot(client_b, "bot_b")
    b.position.sl_ref, b.position.tp_ref = {"kind": "algo", "id": 2001}, {"kind": "algo", "id": 2002}
    client_a.open, client_b.open = {("algo", TP_ID)}, {("algo", 2001), ("algo", 2002)}
    client_a.states[("algo", SL_ID)] = {"state": "FILLED", "avg_price": 80097.5}
    a._sync_own_position(0.0018, 80100.0)
    b._sync_own_position(0.0009, 80100.0)  # la cuenta ya solo tiene 0.0009 (lo de B)
    assert a.position is None
    assert b.position is not None, "B no debe verse afectado por el cierre de A"
    assert client_b.calls == []


def test_triggered_but_unfilled_leg_waits():
    client = FakeClient(net=0.0009)
    client.open = {("algo", TP_ID)}
    client.states[("algo", SL_ID)] = {"state": "TRIGGERED"}
    bot = make_bot(client)
    bot._sync_own_position(0.0009, 80000.0)
    assert bot.position is not None and client.calls == []


# ── Cierre externo (ambas patas canceladas) ────────────────────────────────────

def test_external_close_needs_two_flat_reads_and_never_uses_signal_price():
    client = FakeClient(net=0.0)
    client.states[("algo", SL_ID)] = {"state": "CANCELED"}
    client.states[("algo", TP_ID)] = {"state": "CANCELED"}
    bot = make_bot(client)
    bot._sync_own_position(0.0, 79900.0)
    assert bot.position is not None, "una sola lectura plana no basta (lecturas viejas de Testnet)"
    bot._sync_own_position(0.0, 79900.0)
    assert bot.position is None
    assert bot.trade_history[-1]["reason"] == "BINANCE_EXCHANGE_CLOSED"
    assert bot.trade_history[-1]["exit_price"] == 79900.0
    assert ("close_order",) not in client.calls


def test_lost_protection_with_live_position_is_replaced_only_for_missing_legs():
    client = FakeClient(net=0.0009)
    client.open = {("algo", TP_ID)}                      # el TP vive; el SL fue cancelado
    client.states[("algo", SL_ID)] = {"state": "CANCELED"}
    bot = make_bot(client)
    bot._ensure_exchange_sl_tp()
    assert client.calls == [("place", bot.position.sl_price, None)]
    assert bot.position.sl_ref == {"kind": "algo", "id": 9001}
    assert bot.position.tp_ref == TP_REF


def test_does_not_place_protection_when_account_has_no_backing_position():
    client = FakeClient(net=0.0)                        # cuenta plana: nada que proteger
    bot = make_bot(client)
    bot._ensure_exchange_sl_tp()
    assert client.calls == []


def test_does_not_place_protection_when_account_position_belongs_to_a_smaller_net():
    client = FakeClient(net=0.0004)                     # la neta no alcanza para la cantidad de este bot
    bot = make_bot(client)
    bot._ensure_exchange_sl_tp()
    assert client.calls == []


def test_does_not_replace_when_a_leg_state_is_uncertain():
    client = FakeClient(net=0.0009)
    client.open = set()
    client.states[("algo", SL_ID)] = {"state": "UNKNOWN"}
    bot = make_bot(client)
    bot._ensure_exchange_sl_tp()
    assert client.calls == []


# ── Reintento -2022 y cierres propios ───────────────────────────────────────────

def test_own_exchange_close_resolution():
    client = FakeClient()
    client.states[("algo", SL_ID)] = {"state": "FILLED", "avg_price": 80123.0}
    bot = make_bot(client)
    assert bot._resolve_own_exchange_close(bot.position) == ("FOUND", 80123.0)
    client.states = {("algo", SL_ID): {"state": "CANCELED"}, ("algo", TP_ID): {"state": "CANCELED"}}
    assert bot._resolve_own_exchange_close(bot.position) == ("NONE", None)
    client.states = {}
    assert bot._resolve_own_exchange_close(bot.position) == ("UNKNOWN", None)


def test_position_refs_survive_serialization_roundtrip():
    bot = make_bot(FakeClient())
    data = bot.to_dict()
    assert data["position"]["sl_ref"] == SL_REF and data["position"]["tp_ref"] == TP_REF
    clone = make_bot(FakeClient(), "bot_clone")
    clone.position = None
    clone.restore_from_dict(data)
    assert clone.position.sl_ref == SL_REF and clone.position.tp_ref == TP_REF


# ── Recuperación tras reinicio desde el ledger (en vez de "adoptar" la neta) ───

@pytest.fixture
def ledger(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[AppOrderRecord.__table__])
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(pt, "SessionLocal", Session)
    return Session


def _row(Session, bot_id, action, side, oid, minutes_ago, qty=0.0009, status="SENT_OK"):
    db = Session()
    db.add(AppOrderRecord(
        app_order_ref=f"{bot_id}-{action}-{oid}", created_at=datetime.utcnow() - timedelta(minutes=minutes_ago),
        symbol="BTCUSDT", bot_id=bot_id, side=side, action=action, order_type="MARKET",
        requested_qty=qty, executed_qty=qty if action in ("OPEN", "CLOSE") else None, use_testnet=True,
        status=status, binance_order_id=str(oid),
    ))
    db.commit()
    db.close()


def recover_bot(client, bot_id="bot_a"):
    bot = make_bot(client, bot_id)
    bot.position = None
    bot.klines_df = pd.DataFrame(
        {"open": 80000.0, "high": 80100.0, "low": 79900.0, "close": 80000.0, "volume": 1.0},
        index=pd.date_range("2026-09-20 12:00", periods=60, freq="1min", tz="UTC"),
    )
    return bot


def test_recovers_open_position_from_own_ledger_orders(ledger):
    _row(ledger, "bot_a", "OPEN", "BUY", 5001, 30)
    _row(ledger, "bot_a", "TAKE_PROFIT", "SELL", TP_ID, 30)
    _row(ledger, "bot_a", "STOP_LOSS", "SELL", SL_ID, 30)
    _row(ledger, "bot_b", "OPEN", "BUY", 5002, 5)            # posición de OTRO bot: se ignora
    client = FakeClient(net=0.0018)
    client.open = {("algo", SL_ID), ("algo", TP_ID)}
    bot = recover_bot(client)
    bot._recover_position_from_ledger()
    assert bot.position is not None
    assert (bot.position.side, bot.position.quantity, bot.position.entry_price) == ("long", 0.0009, 80100.0)
    assert bot.position.sl_ref == SL_REF and bot.position.tp_ref == TP_REF


def test_does_not_recover_when_a_close_followed_the_entry(ledger):
    _row(ledger, "bot_a", "OPEN", "BUY", 5001, 30)
    _row(ledger, "bot_a", "CLOSE", "SELL", 5003, 20)
    bot = recover_bot(FakeClient(net=0.0009))
    bot._recover_position_from_ledger()
    assert bot.position is None


def test_does_not_recover_when_exchange_already_filled_a_leg(ledger):
    _row(ledger, "bot_a", "OPEN", "BUY", 5001, 30)
    _row(ledger, "bot_a", "STOP_LOSS", "SELL", SL_ID, 30)
    client = FakeClient(net=0.0009)
    client.states[("algo", SL_ID)] = {"state": "FILLED", "avg_price": 80000.0}
    bot = recover_bot(client)
    bot._recover_position_from_ledger()
    assert bot.position is None


def test_does_not_recover_without_backing_account_position(ledger):
    _row(ledger, "bot_a", "OPEN", "BUY", 5001, 30)
    bot = recover_bot(FakeClient(net=0.0))
    bot._recover_position_from_ledger()
    assert bot.position is None


def test_never_recovers_other_bots_orders(ledger):
    _row(ledger, "bot_b", "OPEN", "BUY", 5002, 5)
    bot = recover_bot(FakeClient(net=0.0009), "bot_a")
    bot._recover_position_from_ledger()
    assert bot.position is None


# ── Posiciones fantasma heredadas de un estado anterior (sin referencias SL/TP) ─

def test_ghost_position_without_refs_is_cleared_when_account_is_flat(ledger):
    """El estado guardado de Bot 2 traía una posición 'adoptada' de otro bot, sin órdenes propias."""
    client = FakeClient(net=0.0)
    bot = make_bot(client)
    bot.position.sl_ref = bot.position.tp_ref = None      # y el ledger no tiene nada de este bot
    bot._sync_own_position(0.0, 79950.0)
    assert bot.position is not None, "una sola lectura plana no basta"
    bot._sync_own_position(0.0, 79950.0)
    assert bot.position is None
    assert bot.trade_history[-1]["reason"] == "BINANCE_EXCHANGE_CLOSED"
    assert ("close_order",) not in client.calls


def test_position_without_refs_and_live_account_is_not_closed_and_never_gets_foreign_protection(ledger):
    client = FakeClient(net=0.0009)
    bot = make_bot(client)
    bot.position.sl_ref = bot.position.tp_ref = None
    bot._sync_own_position(0.0009, 80000.0)
    assert bot.position is not None
    # Repone su propia protección (la cuenta tiene posición que la respalda)
    assert client.calls == [("place", bot.position.sl_price, bot.position.tp_price)]


# ── Salida propia sobre una posición neta compartida ───────────────────────────

def _alerts(bot):
    bot.alerts = []
    bot._trigger_critical_order_alert = lambda title, details=None: bot.alerts.append(title)


def test_exit_order_is_sent_without_reduce_only_and_cancels_only_own_protection():
    """Con otro bot en el símbolo, reduceOnly se recorta contra la posición NETA: la salida va por la cantidad exacta."""
    client = FakeClient(net=0.0003)
    client.open = {("algo", SL_ID), ("algo", TP_ID)}
    client.states[("algo", SL_ID)] = client.states[("algo", TP_ID)] = {"state": "OPEN"}
    bot = make_bot(client)
    bot._close_position(80600.0, datetime.now(timezone.utc), reason="EXIT_SIGNAL")
    assert bot.position is None
    assert client.close_kwargs["reduce_only"] is False
    assert client.calls == [("close_order",), ("cancel", (("algo", SL_ID), ("algo", TP_ID)))]


def test_exit_signal_does_not_send_a_close_when_own_take_profit_already_filled():
    """Sin reduceOnly una salida sobre una posición ya cerrada abriría una contraria: se consulta antes su TP/SL."""
    client = FakeClient(net=0.0)
    client.open = {("algo", SL_ID)}
    client.states[("algo", TP_ID)] = {"state": "FILLED", "avg_price": 81300.0, "executed_qty": 0.0009}
    client.states[("algo", SL_ID)] = {"state": "OPEN"}
    bot = make_bot(client)
    bot._close_position(80600.0, datetime.now(timezone.utc), reason="EXIT_SIGNAL")
    assert bot.position is None
    assert ("close_order",) not in client.calls
    assert bot.trade_history[-1]["reason"] == "TP" and bot.trade_history[-1]["exit_price"] == 81300.0
    assert ("algo", SL_ID) not in client.open, "la pata sobrante (SL) se cancela"


def test_exit_is_postponed_while_own_protection_state_is_unknown():
    client = FakeClient(net=0.0009)          # sin estados conocidos -> UNKNOWN
    bot = make_bot(client)
    bot._close_position(80600.0, datetime.now(timezone.utc), reason="EXIT_SIGNAL")
    assert bot.position is not None and ("close_order",) not in client.calls


def test_zero_net_position_does_not_close_this_bot_when_another_bot_shares_the_symbol():
    client = FakeClient(net=0.0)             # p. ej. largo de A (0.0009) y corto de B (0.0009): neta 0
    client.open = {("algo", SL_ID), ("algo", TP_ID)}
    client.states[("algo", SL_ID)] = client.states[("algo", TP_ID)] = {"state": "OPEN"}
    a = make_bot(client, "bot_a")
    other = make_bot(FakeClient(), "bot_b", side="short")
    a.is_running = other.is_running = True
    pt.PaperTrader._ACTIVE_BOTS.update({"bot_a": a, "bot_b": other})
    try:
        for _ in range(4):
            a._sync_own_position(net_amt=0.0, mark_p=80000.0)
        assert a.position is not None, "la posición neta 0 no prueba que la de ESTE bot no exista si otro comparte el símbolo"
    finally:
        pt.PaperTrader._ACTIVE_BOTS.pop("bot_a", None)
        pt.PaperTrader._ACTIVE_BOTS.pop("bot_b", None)


def test_orphan_protection_left_alive_after_exit_raises_a_critical_alert():
    client = FakeClient(net=0.0009)
    client.open = {("algo", SL_ID), ("algo", TP_ID)}
    client.cancel_order_refs = lambda symbol, refs: (True, [])      # Binance "cancela" pero la orden sigue viva
    bot = make_bot(client)
    _alerts(bot)
    bot._cancel_own_protection()
    assert any("HUÉRFANO" in a for a in bot.alerts)


def test_no_alert_when_own_protection_is_gone_after_exit():
    client = FakeClient(net=0.0009)
    client.open = {("algo", SL_ID), ("algo", TP_ID)}
    bot = make_bot(client)
    _alerts(bot)
    bot._cancel_own_protection()
    assert bot.alerts == []


# ── Salida doble (incidente 21/09 08:50: SL ejecutado + cierre MARKET = short fantasma) ──────────────────────

def _fast(bot):
    bot.NET_READ_CONFIRM_DELAY_S = 0.0
    return bot


def test_uncertain_own_state_never_forces_a_close_by_repeated_ticks():
    """_close_position se llama en cada tick de precio: contar intentos agotaba la espera en ~2 s y se cerraba a ciegas."""
    client = FakeClient(net=0.0009)          # la posición existe; el estado de sus SL/TP es desconocido
    bot = _fast(make_bot(client))
    for _ in range(10):
        bot._close_position(80600.0, datetime.now(timezone.utc), reason="SL")
    assert bot.position is not None and ("close_order",) not in client.calls


def test_stop_loss_already_filled_with_uncertain_state_does_not_send_a_second_exit():
    """El SL ya cerró la posición (neta 0) pero su estado no se pudo leer: vencida la espera NO se envía el cierre."""
    client = FakeClient(net=0.0)
    client.trades = [
        {"time": 1, "side": "BUY", "qty": "0.0009", "price": "80500"},
        {"time": 2, "side": "SELL", "qty": "0.0009", "price": "80100"},
    ]
    bot = _fast(make_bot(client))
    bot._close_uncertain_since = time.monotonic() - 3600
    bot._close_position(80100.0, datetime.now(timezone.utc), reason="SL")
    assert ("close_order",) not in client.calls, "enviar un cierre sobre una posición ya cerrada abre una contraria"
    assert bot.position is None
    assert bot.trade_history[-1]["exit_price"] == 80100.0


def test_uncertain_state_after_timeout_closes_when_the_position_really_exists():
    client = FakeClient(net=0.0009)
    bot = _fast(make_bot(client))
    bot._close_uncertain_since = time.monotonic() - 3600
    bot._close_position(80600.0, datetime.now(timezone.utc), reason="EXIT_SIGNAL")
    assert ("close_order",) in client.calls and bot.position is None


def test_close_is_deferred_when_the_exchange_position_cannot_be_read():
    """Sin SL/TP propio vivo que pruebe la posición, y sin poder leer la neta, no se envía nada."""
    client = FakeClient(net=0.0009)
    client.client.futures_position_information = lambda symbol=None: (_ for _ in ()).throw(RuntimeError("red"))
    client.states[("algo", SL_ID)] = client.states[("algo", TP_ID)] = {"state": "CANCELED"}
    bot = _fast(make_bot(client))
    bot._close_position(80600.0, datetime.now(timezone.utc), reason="EXIT_SIGNAL")
    assert bot.position is not None and ("close_order",) not in client.calls


def test_close_quantity_is_capped_to_what_the_exchange_holds_so_it_never_flips():
    """Sin evidencia propia de que la posición siga viva (SL/TP ya no están), manda la posición real de Binance."""
    client = FakeClient(net=0.0003)          # Binance solo tiene 0.0003 de los 0.0009 del bot
    client.states[("algo", SL_ID)] = client.states[("algo", TP_ID)] = {"state": "CANCELED"}
    bot = _fast(make_bot(client))
    bot._close_position(80600.0, datetime.now(timezone.utc), reason="EXIT_SIGNAL")
    assert client.close_args[2] == pytest.approx(0.0003)


# ── Bots espejo en el mismo símbolo: uno sale mientras el otro entra (Test 2, 21/09) ─────────────────────────

def _own_legs_alive(client):
    client.open = {("algo", SL_ID), ("algo", TP_ID)}
    client.states[("algo", SL_ID)] = client.states[("algo", TP_ID)] = {"state": "OPEN"}


def test_exit_is_sent_when_net_equals_only_the_other_bots_position_but_own_protection_is_alive():
    """
    Largo de A (+0.0009) y corto de B (-0.0009) con una posición ajena de -0.0009 en la cuenta: neta -0.0009 = "solo B".
    La neta hacía parecer cerrada la posición de A y su salida se omitía, dejándola abierta en Binance. Su SL/TP sigue
    vivo, así que la posición existe y A debe enviar su propia orden de cierre.
    """
    client = FakeClient(net=-0.0009)
    _own_legs_alive(client)
    a = make_bot(client, "bot_a")
    b = make_bot(FakeClient(), "bot_b", side="short")
    a._close_position(80600.0, datetime.now(timezone.utc), reason="EXIT_SIGNAL")
    assert ("close_order",) in client.calls and a.position is None
    assert client.close_args[2] == pytest.approx(0.0009), "cierra SU cantidad exacta, no la neta"
    assert a.trade_history[-1]["reason"] == "EXIT_SIGNAL"
    assert b.position is not None


def test_exit_does_not_wait_for_the_net_position_when_own_protection_is_alive():
    """Con el SL/TP propio vivo no se lee la neta (evita el 'no se pudo confirmar' cuando el otro bot opera a la vez)."""
    client = FakeClient(net=0.0)
    _own_legs_alive(client)
    client.client.futures_position_information = lambda symbol=None: (_ for _ in ()).throw(RuntimeError("lectura inestable"))
    bot = make_bot(client)
    bot._close_position(80600.0, datetime.now(timezone.utc), reason="EXIT_SIGNAL")
    assert ("close_order",) in client.calls and bot.position is None


def test_exit_signal_with_own_take_profit_filled_still_sends_no_second_exit_with_mirror_bot():
    """La salida por señal sigue sin duplicarse cuando el TP propio ya cerró la posición, aunque el otro bot opere."""
    client = FakeClient(net=-0.0009)
    client.open = {("algo", SL_ID)}
    client.states[("algo", TP_ID)] = {"state": "FILLED", "avg_price": 81300.0, "executed_qty": 0.0009}
    client.states[("algo", SL_ID)] = {"state": "OPEN"}
    a = make_bot(client, "bot_a")
    make_bot(FakeClient(), "bot_b", side="short")
    a._close_position(80600.0, datetime.now(timezone.utc), reason="EXIT_SIGNAL")
    assert ("close_order",) not in client.calls
    assert a.trade_history[-1]["reason"] == "TP"


def test_close_is_sent_when_an_opposite_bot_offsets_the_net_position():
    """Largo de A (0.0009) y corto de B (0.0009): neta 0. La posición de A existe y su cierre es legítimo."""
    client = FakeClient(net=0.0)
    client.open = {("algo", SL_ID), ("algo", TP_ID)}
    client.states[("algo", SL_ID)] = client.states[("algo", TP_ID)] = {"state": "OPEN"}
    a = _fast(make_bot(client, "bot_a"))
    b = make_bot(FakeClient(), "bot_b", side="short")
    a._close_position(80600.0, datetime.now(timezone.utc), reason="EXIT_SIGNAL")
    assert ("close_order",) in client.calls and a.position is None
    assert b.position is not None


def test_close_is_not_sent_when_only_the_other_bots_position_remains():
    """Neta = solo la corta de B: la larga de A ya no está en Binance, cerrarla abriría otra corta."""
    client = FakeClient(net=-0.0009)
    client.trades = [
        {"time": 1, "side": "BUY", "qty": "0.0009", "price": "80500"},
        {"time": 2, "side": "SELL", "qty": "0.0009", "price": "80100"},
    ]
    a = _fast(make_bot(client, "bot_a"))
    b = make_bot(FakeClient(), "bot_b", side="short")
    a._close_uncertain_since = time.monotonic() - 3600
    a._close_position(80100.0, datetime.now(timezone.utc), reason="SL")
    assert ("close_order",) not in client.calls and a.position is None
    assert b.position is not None


# ── _net_backs_quantity no confunde "comparte símbolo" (solo corriendo) con "aporta a la neta"
# (cualquiera con posición registrada) — incidente 2026-09-22: un bot detenido que compartía
# símbolo quedaba fuera de la fórmula y su exposición real enmascaraba o inventaba un desajuste ──

def test_net_backs_quantity_counts_a_stopped_sharing_bot():
    """B comparte símbolo pero está detenido (is_running=False por defecto): su corta real sigue
    descontándose de la neta antes de juzgar si la larga de A está respaldada."""
    client = FakeClient(net=0.0)  # la larga de A (0.0009) y la corta de B (0.0009) se cancelan en la neta
    a = make_bot(client, "bot_a", side="long", qty=0.0009)
    b = make_bot(FakeClient(), "bot_b", side="short", qty=0.0009)
    assert b.is_running is False
    assert a._net_backs_quantity("long", 0.0009) is True


def test_net_backs_quantity_rejects_when_stopped_sharing_bot_explains_the_net():
    """Sin ninguna corta de B, esa misma neta (0) NO respalda una larga de A: la diferencia es justo
    la exposición de B, y con B ausente del cálculo (bug original) esto habría dado un falso positivo."""
    client = FakeClient(net=0.0)
    a = make_bot(client, "bot_a", side="long", qty=0.0009)
    assert a._net_backs_quantity("long", 0.0009) is False


def test_place_missing_protection_uses_stopped_sharing_bot_exposure():
    """Reponer el SL/TP de A ya no se bloquea porque B (mismo símbolo, detenido) también aporta a la
    neta: antes, `_shares_symbol()` (solo bots corriendo) decidía la fórmula mientras el cálculo usaba
    `_others_signed_position()` (todos), y un B detenido quedaba invisible para la fórmula elegida."""
    client = FakeClient(net=0.0)
    a = make_bot(client, "bot_a", side="long", qty=0.0009)
    b = make_bot(FakeClient(), "bot_b", side="short", qty=0.0009)  # referencia viva: el registro es WeakValueDictionary
    assert b.position is not None
    a.position.sl_ref = a.position.tp_ref = None
    a._place_missing_protection(a.position, ["sl", "tp"])
    assert ("place", a.position.sl_price, a.position.tp_price) in client.calls
