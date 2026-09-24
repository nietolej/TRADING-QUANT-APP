"""El motor en vivo (PaperTrader) recalcula el SL dinámico (trailing_percent, break_even,
chandelier) al cierre de cada vela, igual que el backtest (ver backtest_engine/backtester.py y
RiskManager.update_trailing_sl). Antes el SL se fijaba una sola vez al abrir la posición y
jamás se volvía a mover en el motor en vivo, aunque el backtest sí lo desplazaba vela a vela."""
import pandas as pd
import pytest

import execution_engine.paper_trader as pt


@pytest.fixture(autouse=True)
def _fresh_bot_registry():
    pt.PaperTrader._ALL_BOTS.clear()
    yield
    pt.PaperTrader._ALL_BOTS.clear()


def _bot(sl_config, use_testnet=False):
    bot = pt.PaperTrader(
        strategy_yaml_path="config/strategies/ema_long.yaml", initial_balance=100.0, currency="USDT",
        use_testnet=use_testnet, custom_timeframe="1m", custom_symbol="BTC/USDT", bot_id="bot_x", name="bot_x",
        custom_parameters={"FAST": 1, "LOW": 10, "SL": 1.0, "TP": 90.0},
    )
    bot.strategy.risk_manager.sl_config = sl_config
    bot._save_state = lambda: None
    bot.notes = []
    bot._notify = lambda msg="", is_alert=False: bot.notes.append((msg, is_alert))
    return bot


def _bars(rows):
    """rows: lista de (open, high, low, close) -> DataFrame con índice de timestamps crecientes."""
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="1min", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def _open_long(bot, entry_price, sl_price):
    bot.position = pt.Position("long", entry_price, 1.0, bot.klines_df.index[0] if len(bot.klines_df) else 0)
    bot.position.sl_price = sl_price
    return bot.position


def test_trailing_percent_moves_up_with_new_high_and_never_down():
    bot = _bot({"type": "trailing_percent", "value": 2.0})
    pos = _open_long(bot, entry_price=100.0, sl_price=98.0)

    # La última fila de klines_df es siempre la vela EN CURSO (no cerrada) y no debe usarse
    # para el trailing: se le agrega una vela más tras la que se quiere probar como cerrada.
    # Vela cerrada 1: high=110 -> nuevo SL = 110 * 0.98 = 107.8 (sube)
    bot.klines_df = _bars([(100, 100, 100, 100), (105, 110, 104, 108), (108, 108, 107, 107)])
    bot._update_trailing_stop()
    assert pos.sl_price == pytest.approx(107.8)

    # Vela cerrada 2: high mas bajo que el pico anterior -> el trailing NUNCA retrocede
    bot.klines_df = _bars(
        [(100, 100, 100, 100), (105, 110, 104, 108), (108, 109, 105, 106), (106, 106, 105, 105)]
    )
    bot._update_trailing_stop()
    assert pos.sl_price == pytest.approx(107.8)


def test_trailing_only_updates_once_per_closed_candle():
    bot = _bot({"type": "trailing_percent", "value": 2.0})
    pos = _open_long(bot, entry_price=100.0, sl_price=98.0)
    bot.klines_df = _bars([(100, 100, 100, 100), (105, 110, 104, 108), (108, 108, 107, 107)])

    bot._update_trailing_stop()
    assert pos.sl_price == pytest.approx(107.8)

    # Se simula que _evaluate_market se ejecuta de nuevo con la MISMA última vela cerrada
    # (varios ticks intravela de la vela en curso): no debe recalcular ni notificar de nuevo.
    bot.notes.clear()
    bot._update_trailing_stop()
    assert pos.sl_price == pytest.approx(107.8)
    assert bot.notes == []


def test_break_even_moves_sl_to_entry_after_trigger():
    bot = _bot({"type": "break_even", "be_trigger_pct": 1.5, "trailing_after_be_pct": 2.0})
    pos = _open_long(bot, entry_price=100.0, sl_price=98.0)

    # High todavia no alcanza el 1.5% de gatillo: el SL no se mueve
    bot.klines_df = _bars(
        [(100, 100, 100, 100), (100, 101.0, 99.5, 100.5), (100.5, 100.5, 100.0, 100.2)]
    )
    bot._update_trailing_stop()
    assert pos.sl_price == pytest.approx(98.0)

    # High supera el gatillo (101.5): SL pasa a break-even (entry_price, ya que domina sobre el trailing)
    bot.klines_df = _bars(
        [(100, 100, 100, 100), (100, 101.0, 99.5, 100.5), (100.5, 102.0, 100.2, 101.5), (101.5, 101.5, 101.0, 101.2)]
    )
    bot._update_trailing_stop()
    assert pos.sl_price == pytest.approx(100.0)


def test_chandelier_uses_atr_and_only_tightens_in_favor():
    bot = _bot({"type": "chandelier", "atr_period": 3, "atr_multiplier": 2.0, "lookback": 3})
    pos = _open_long(bot, entry_price=100.0, sl_price=90.0)

    rows = [
        (100, 102, 98, 101),
        (101, 104, 100, 103),
        (103, 110, 101, 108),
        (108, 108, 107, 107),  # vela en curso (no cerrada): no debe usarse
    ]
    bot.klines_df = _bars(rows)
    bot._update_trailing_stop()
    assert pos.sl_price is not None
    assert pos.sl_price >= 90.0  # el chandelier solo ajusta a favor de la posición


def test_static_sl_types_are_never_touched_by_trailing():
    for sl_config in (
        {"type": "fixed", "value": 2.0},
        {"type": "atr", "atr_period": 14, "atr_multiplier": 2.0},
        {"type": "swing", "lookback": 10},
        {"type": "none"},
    ):
        bot = _bot(sl_config)
        pos = _open_long(bot, entry_price=100.0, sl_price=98.0)
        bot.klines_df = _bars([(100, 100, 100, 100), (105, 130, 104, 120)])
        bot._update_trailing_stop()
        assert pos.sl_price == pytest.approx(98.0)


def test_no_position_or_no_sl_price_is_a_noop():
    bot = _bot({"type": "trailing_percent", "value": 2.0})
    bot.klines_df = _bars([(100, 100, 100, 100), (105, 110, 104, 108)])
    bot.position = None
    bot._update_trailing_stop()  # sin posición: no debe lanzar

    pos = _open_long(bot, entry_price=100.0, sl_price=None)
    bot._update_trailing_stop()  # sin SL configurado: no debe lanzar ni inventar uno
    assert pos.sl_price is None


class _FakeClient:
    def __init__(self):
        self.api_key = "fake"
        self.cancel_calls = []
        self.place_calls = []
        self.cancel_ok = True
        self.place_result = None

    def cancel_order_refs(self, symbol, refs):
        self.cancel_calls.append((symbol, refs))
        return self.cancel_ok, [] if self.cancel_ok else ["boom"]

    def place_futures_sl_tp(self, symbol, side, quantity, sl_price=None, tp_price=None,
                             sl_order_type="MARKET", tp_order_type="MARKET", reduce_only=True):
        self.place_calls.append((symbol, side, quantity, sl_price, tp_price))
        return self.place_result if self.place_result is not None else {
            "sl_order": {"orderId": 999}, "tp_order": None, "errors": []
        }

    @staticmethod
    def order_ref(order):
        if not order:
            return None
        return {"kind": "order", "id": int(order["orderId"])}


def test_trailing_replaces_live_sl_order_on_binance():
    bot = _bot({"type": "trailing_percent", "value": 2.0}, use_testnet=True)
    bot._client = _FakeClient()
    bot.order_types = {"stop_loss": "MARKET"}
    pos = _open_long(bot, entry_price=100.0, sl_price=98.0)
    pos.sl_ref = {"kind": "order", "id": 111}

    bot.klines_df = _bars([(100, 100, 100, 100), (105, 110, 104, 108), (108, 108, 107, 107)])
    bot._update_trailing_stop()

    assert pos.sl_price == pytest.approx(107.8)
    assert bot._client.cancel_calls == [("BTC/USDT", [{"kind": "order", "id": 111}])]
    assert len(bot._client.place_calls) == 1
    assert bot._client.place_calls[0][3] == pytest.approx(107.8)
    assert pos.sl_ref == {"kind": "order", "id": 999}


def test_trailing_keeps_old_sl_when_cancel_fails():
    bot = _bot({"type": "trailing_percent", "value": 2.0}, use_testnet=True)
    fake = _FakeClient()
    fake.cancel_ok = False
    bot._client = fake
    bot.order_types = {"stop_loss": "MARKET"}
    pos = _open_long(bot, entry_price=100.0, sl_price=98.0)
    pos.sl_ref = {"kind": "order", "id": 111}

    bot.klines_df = _bars([(100, 100, 100, 100), (105, 110, 104, 108), (108, 108, 107, 107)])
    bot._update_trailing_stop()

    # No se pudo cancelar la orden vieja: no se toca nada (ni el nivel interno ni el ref)
    assert pos.sl_price == pytest.approx(98.0)
    assert pos.sl_ref == {"kind": "order", "id": 111}
    assert fake.place_calls == []


def test_trailing_advances_internal_level_even_if_new_order_placement_fails():
    bot = _bot({"type": "trailing_percent", "value": 2.0}, use_testnet=True)
    fake = _FakeClient()
    fake.place_result = {"sl_order": None, "tp_order": None, "errors": ["network error"]}
    bot._client = fake
    bot.order_types = {"stop_loss": "MARKET"}
    pos = _open_long(bot, entry_price=100.0, sl_price=98.0)
    pos.sl_ref = {"kind": "order", "id": 111}

    bot.klines_df = _bars([(100, 100, 100, 100), (105, 110, 104, 108), (108, 108, 107, 107)])
    bot._update_trailing_stop()

    # La orden vieja SÍ se canceló: el nivel interno debe avanzar para que la reconciliación
    # periódica (_ensure_exchange_sl_tp) reponga el SL ya en el nivel correcto.
    assert pos.sl_price == pytest.approx(107.8)
    assert pos.sl_ref is None
