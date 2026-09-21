"""Modo de señal 'close': las señales de la estrategia solo se evalúan con velas cerradas."""
import pandas as pd
import pytest

import execution_engine.paper_trader as pt
from web_gui.pages.live_monitor_page import LiveMonitorPage


def make_bot(mode):
    bot = pt.PaperTrader(
        strategy_yaml_path="config/strategies/ema_long.yaml", initial_balance=100.0, currency="USDT",
        use_testnet=False, custom_timeframe="1m", custom_symbol="BTC/USDT", bot_id="b", name="b",
        custom_parameters={"FAST": 1, "LOW": 10, "SL": 5.0, "TP": 50.0},
    )
    bot.order_types["signal_mode"] = mode
    bot._notify = lambda *a, **k: None
    bot.events = []
    bot._open_position = lambda side, price, ts: bot.events.append(("open", side, price))
    bot._close_position = lambda price, ts, reason, **k: bot.events.append(("close", reason, price))
    return bot


def frame(closes, start="2026-09-20 12:00"):
    idx = pd.date_range(start, periods=len(closes), freq="1min", tz="UTC", name="timestamp")
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": 1.0}, index=idx)


# Entrada POSTERIOR a todas las velas de los datos de prueba (12:00–12:42): así solo actúa la vía de señales
# y no la salida por estado, que por diseño solo cuenta velas cerradas después de abrir la posición.
ENTRY_AFTER_CANDLES = pd.Timestamp("2026-09-20 12:59", tz="UTC")


def hold_long(bot, entry=ENTRY_AFTER_CANDLES):
    bot.position = pt.Position("long", 100.0, 1.0, entry)
    bot.position.sl_price, bot.position.tp_price = 1.0, 1_000.0


BASE = [100.0] * 39 + [101.0]          # 40 velas: ... 100, 101 (EMA10 ~100.1)


def test_intrabar_exits_on_a_transient_dip_inside_the_candle():
    bot = make_bot("intrabar")
    hold_long(bot)
    bot.klines_df = frame(BASE + [99.0])           # la vela en curso se hunde a 99 (cruce bajista en vivo)
    bot._evaluate_market()
    assert bot.events == [("close", "EXIT_SIGNAL", 99.0)]


def test_close_mode_ignores_the_dip_and_waits_for_the_candle_to_close():
    bot = make_bot("close")
    hold_long(bot)
    bot.klines_df = frame(BASE + [101.0])          # arranque: última vela cerrada = 101 (se marca como vista)
    bot._evaluate_market()
    bot.klines_df = frame(BASE + [99.0])           # dip intravela: la vela en curso se hunde
    bot._evaluate_market()
    assert bot.events == [], "un dip dentro de la vela NO debe disparar la salida"
    bot.klines_df = frame(BASE + [101.5])          # la vela se recupera y cierra arriba
    bot._evaluate_market()
    bot.klines_df = frame(BASE + [101.5, 101.6])   # nueva vela: la anterior (101.5) cerró sin cruce
    bot._evaluate_market()
    assert bot.events == []


def test_close_mode_exits_once_when_the_closed_candle_confirms_the_cross():
    bot = make_bot("close")
    hold_long(bot)
    bot.klines_df = frame(BASE + [101.0])
    bot._evaluate_market()                          # marca la última vela cerrada
    bot.klines_df = frame(BASE + [99.0, 99.0])      # la vela cerró en 99 (cruce bajista confirmado)
    bot._evaluate_market()
    assert bot.events == [("close", "EXIT_SIGNAL", 99.0)]
    hold_long(bot)
    bot.events.clear()
    bot._evaluate_market()                          # misma vela cerrada: no se vuelve a evaluar
    assert bot.events == []


def test_close_mode_does_not_act_on_a_signal_that_happened_before_start():
    bot = make_bot("close")
    hold_long(bot)
    bot.klines_df = frame(BASE + [99.0, 99.0])      # el cruce ya ocurrió antes de arrancar el bot
    bot._evaluate_market()
    assert bot.events == [], "la última vela cerrada se marca como vista al arrancar"


def test_close_mode_entry_only_on_closed_candle():
    bot = make_bot("close")
    down = [100.0] * 39 + [99.0]                    # EMA1 (99) bajo EMA10 (~99.9)
    bot.klines_df = frame(down + [99.0])
    bot._evaluate_market()                          # arranque
    bot.klines_df = frame(down + [101.0])           # pico intravela por encima de EMA10: cruce alcista en vivo
    bot._evaluate_market()
    assert bot.events == [], "un pico intravela no abre posición en modo 'close'"
    bot.klines_df = frame(down + [101.0, 101.0])    # la vela cerró en 101: cruce alcista confirmado
    bot._evaluate_market()
    assert bot.events == [("open", "long", 101.0)]


def test_intrabar_entry_fires_on_the_live_spike():
    bot = make_bot("intrabar")
    down = [100.0] * 39 + [99.0]
    bot.klines_df = frame(down + [101.0])
    bot._evaluate_market()
    assert bot.events == [("open", "long", 101.0)]


def test_stop_loss_is_still_watched_live_in_close_mode():
    bot = make_bot("close")
    hold_long(bot)
    bot.position.sl_price = 99.5
    bot.klines_df = frame(BASE + [101.0])
    bot._evaluate_market()
    bot.klines_df = frame(BASE + [99.0])            # toca el SL en vivo
    bot._evaluate_market()
    assert bot.events == [("close", "SL", 99.0)]


def test_signal_mode_defaults_persists_and_survives_restore():
    bot = make_bot("intrabar")
    assert bot.signal_mode == "intrabar"
    bot.update_configuration(order_types={"signal_mode": "close"})
    assert bot.signal_mode == "close"
    data = bot.to_dict()
    assert data["order_types"]["signal_mode"] == "close"
    clone = make_bot("intrabar")
    clone.restore_from_dict(data)
    assert clone.signal_mode == "close"
    legacy = make_bot("intrabar")
    legacy.restore_from_dict({**data, "order_types": {"entry": "MARKET", "exit": "MARKET"}})   # estado viejo sin la clave
    assert legacy.signal_mode == "close"
    legacy.order_types["signal_mode"] = "basura"
    assert legacy.signal_mode == "close"


def test_chart_hover_flags_an_intrabar_exit_and_shows_reason_and_close():
    df = frame([81100.0, 81150.0, 81190.0, 81204.4, 81210.0], "2026-09-20 18:24")
    trade = {"side": "long", "entry_time": "2026-09-20 18:25:00+00:00", "exit_time": "2026-09-20 18:26:30+00:00",
             "entry_price": 81188.2, "exit_price": 81142.4, "reason": "EXIT_SIGNAL", "pnl": -0.1142}
    hover = LiveMonitorPage._trade_hover(trade, "exit", 81142.4, df)
    assert "VENTA @ 81,142.40" in hover and "Razón: señal" in hover and "PnL: -0.1142" in hover
    assert "cerró en 81,190.00" in hover and "Salida intravela" in hover
    entry = LiveMonitorPage._trade_hover(trade, "entry", 81188.2, df)
    assert "COMPRA @ 81,188.20" in entry and "Razón" not in entry and "Salida intravela" not in entry
    sl_exit = dict(trade, reason="SL")
    assert "Salida intravela" not in LiveMonitorPage._trade_hover(sl_exit, "exit", 81142.4, df)
    assert LiveMonitorPage._reason_label("BINANCE_EXCHANGE_CLOSED") == "cierre del exchange"


def test_state_exit_still_closes_an_older_position_below_the_average_in_close_mode():
    """La salida por estado (EMA1 < EMA10 al cierre de una vela posterior a la entrada) sigue vigente."""
    bot = make_bot("close")
    hold_long(bot, entry=pd.Timestamp("2026-09-20 11:00", tz="UTC"))
    bot.klines_df = frame(BASE + [99.0, 99.0])
    bot._evaluate_market()
    assert bot.events == [("close", "EXIT_SIGNAL", 99.0)]
