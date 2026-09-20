"""La interfaz web ve, vía BotProxy, lo mismo que antes veía en el PaperTrader embebido: velas, bid/ask,
estadísticas y cierre manual — ahora servidos por el daemon."""
import threading
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import execution_engine.bot_daemon as daemon
from execution_engine import daemon_client as dc
from execution_engine.paper_trader import PaperTrader


class FakeBot:
    def __init__(self):
        idx = pd.date_range("2026-09-20 12:00", periods=5, freq="1min", tz="UTC", name="timestamp")  # como el del PaperTrader real
        self.klines_df = pd.DataFrame(
            {"open": [1.0, 2, 3, 4, 5], "high": [2.0, 3, 4, 5, 6], "low": [0.5, 1, 2, 3, 4],
             "close": [1.5, 2.5, 3.5, 4.5, 5.5], "volume": [10.0, 11, 12, 13, 14]}, index=idx)
        self.current_bid, self.current_ask = 5.4, 5.6
        self.current_bid_qty, self.current_ask_qty = 3.0, 4.0
        self.binance_position_info = {"amount": 0.001}
        self._lock = threading.RLock()
        self.closed_calls = 0

    def manual_close_position(self):
        self.closed_calls += 1
        return True, "Posición LONG cerrada."


@pytest.fixture
def api(monkeypatch):
    bot = FakeBot()
    manager = SimpleNamespace(get_bot=lambda bot_id: bot if bot_id == "b1" else None, save_state_to_disk=lambda: None)
    monkeypatch.setattr(daemon, "bot_manager", manager)
    return TestClient(daemon.app), bot


def test_market_endpoint_serves_candles_and_book(api):
    client, bot = api
    body = client.get("/api/bots/b1/market", params={"limit": 3}).json()
    assert len(body["klines"]) == 3
    ts, o, h, l, c, v = body["klines"][-1]
    assert (o, h, l, c, v) == (5.0, 6.0, 4.0, 5.5, 14.0)
    assert ts == int(bot.klines_df.index[-1].timestamp() * 1000)
    assert body["current_bid"] == 5.4 and body["current_ask"] == 5.6 and body["current_ask_qty"] == 4.0
    assert body["binance_position_info"] == {"amount": 0.001}


def test_unknown_bot_is_404(api):
    client, _ = api
    assert client.get("/api/bots/nope/market").status_code == 404
    assert client.post("/api/bots/nope/close_position").status_code == 404


def test_close_position_endpoint_delegates_to_the_bot(api):
    client, bot = api
    body = client.post("/api/bots/b1/close_position").json()
    assert body == {"success": True, "message": "Posición LONG cerrada."} and bot.closed_calls == 1


def make_proxy(monkeypatch, market, **data):
    client = dc.DaemonClient(base_url="http://127.0.0.1:59997")
    monkeypatch.setattr(client, "get_bot_market", lambda bot_id, limit=300: market)
    base = {"bot_id": "b1", "name": "Bot", "symbol": "BTC/USDT", "timeframe": "1m"}
    base.update(data)
    return dc.BotProxy(base, client)


def test_proxy_rebuilds_the_same_dataframe_the_chart_expects(api, monkeypatch):
    client, bot = api
    market = client.get("/api/bots/b1/market").json()
    proxy = make_proxy(monkeypatch, market)
    df = proxy.klines_df
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert str(df.index.tz) == "UTC" and len(df) == 5
    pd.testing.assert_frame_equal(df, bot.klines_df, check_freq=False, check_index_type=False)
    assert (proxy.current_bid, proxy.current_ask, proxy.current_bid_qty, proxy.current_ask_qty) == (5.4, 5.6, 3.0, 4.0)
    assert proxy.binance_position_info == {"amount": 0.001}


def test_proxy_market_is_fetched_once_per_proxy(monkeypatch):
    calls = []
    client = dc.DaemonClient(base_url="http://127.0.0.1:59997")
    monkeypatch.setattr(client, "get_bot_market", lambda bot_id, limit=300: calls.append(1) or {"klines": [], "current_bid": 1.0})
    proxy = dc.BotProxy({"bot_id": "b1"}, client)
    for _ in range(20):
        proxy.klines_df, proxy.current_ask, proxy.current_bid
    assert len(calls) == 1


def test_proxy_without_market_degrades_gracefully(monkeypatch):
    proxy = make_proxy(monkeypatch, None, position={"side": "long", "entry_price": 80000.0, "quantity": 0.001})
    assert proxy.klines_df.empty
    assert proxy.current_ask == 0.0
    assert proxy.current_bid == 80000.0, "sin datos en vivo se usa el precio de entrada como antes"


def test_proxy_setters_used_by_the_ui_do_not_touch_the_daemon(monkeypatch):
    proxy = make_proxy(monkeypatch, {"klines": [], "binance_position_info": {"amount": 1}})
    proxy.binance_position_info = None
    assert proxy.binance_position_info is None
    proxy.current_bid = 123.0
    assert proxy.current_bid == 123.0


def test_proxy_detailed_stats_and_strategy_match_the_embedded_bot(monkeypatch):
    trades = [{"pnl": 2.0}, {"pnl": -1.0}, {"pnl": 3.0}]
    proxy = make_proxy(monkeypatch, {}, trade_history=trades, initial_balance=100.0,
                       strategy_yaml_path="config/strategies/ema_long.yaml", custom_parameters={"FAST": 3})
    stats = proxy.get_detailed_stats()
    assert stats["total_trades"] == 3 and stats["wins"] == 2 and stats["profit_factor"] == 5.0
    assert proxy.strategy.parameters["FAST"] == 3 and proxy.strategy.config["strategy_name"] == "EMA LONG"
    real = PaperTrader(strategy_yaml_path="config/strategies/ema_long.yaml", initial_balance=100.0, currency="USDT",
                       use_testnet=False, custom_timeframe="1m", custom_symbol="BTC/USDT", bot_id="x", name="x")
    real.trade_history = trades
    assert real.get_detailed_stats() == stats


def test_manual_close_position_on_the_real_bot(monkeypatch):
    bot = PaperTrader(strategy_yaml_path="config/strategies/ema_long.yaml", initial_balance=100.0, currency="USDT",
                      use_testnet=False, custom_timeframe="1m", custom_symbol="BTC/USDT", bot_id="x", name="x")
    assert bot.manual_close_position() == (False, "El bot no tiene una posición abierta.")
    from execution_engine.paper_trader import Position
    from datetime import datetime, timezone
    bot.position = Position("long", 80000.0, 0.001, datetime.now(timezone.utc))
    bot.current_bid = 80100.0
    bot._notify = lambda *a, **k: None
    ok, message = bot.manual_close_position()
    assert ok and bot.position is None and bot.trade_history[-1]["reason"] == "MANUAL_BINANCE_CLOSE"
    assert bot.trade_history[-1]["exit_price"] == 80100.0
