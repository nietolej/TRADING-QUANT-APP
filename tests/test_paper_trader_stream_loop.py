"""El bucle de mercado del bot usa WebSocket, cae a REST si se corta y vuelve al WebSocket al recuperarse."""
import threading
import time
from types import SimpleNamespace

import pytest

import execution_engine.paper_trader as pt
from execution_engine import market_stream as ms
from tests.test_market_stream import FakeBinance, wait_until


def make_bot():
    bot = pt.PaperTrader(
        strategy_yaml_path="config/strategies/ema_long.yaml", initial_balance=100.0, currency="USDT",
        use_testnet=False, custom_timeframe="1m", custom_symbol="BTC/USDT", bot_id="bot_ws", name="ws",
        custom_parameters={"FAST": 1, "LOW": 10, "SL": 0.5, "TP": 1.0},
    )
    bot._notify = lambda *a, **k: None
    bot.rest_calls = 0
    bot.klines_seen = []

    def ticker(**kw):
        bot.rest_calls += 1
        return {"bidPrice": "50.0", "askPrice": "50.5", "bidQty": "1", "askQty": "1"}

    def klines(**kw):
        bot.rest_calls += 1
        return [[1_700_000_000_000, "50", "51", "49", "50.2", "7"]]

    bot._client = SimpleNamespace(api_key="", client=SimpleNamespace(
        futures_orderbook_ticker=ticker, futures_klines=klines))
    bot._on_new_kline = lambda kline: bot.klines_seen.append(kline)
    return bot


def run_loop(bot):
    bot.is_running = True
    bot._run_generation += 1
    t = threading.Thread(target=bot._polling_loop, args=(bot._run_generation,), daemon=True)
    t.start()
    return t


@pytest.fixture
def fake_hub(monkeypatch):
    holder = {}

    def use(server):
        def factory(symbol, interval, use_testnet):
            return ms.SymbolStream(symbol, interval, use_testnet, urls=[("fake", f"ws://127.0.0.1:{server.port}")],
                                   stale_after_s=1.0)
        hub = ms.StreamHub(stream_factory=factory)
        monkeypatch.setattr(pt, "stream_hub", hub)
        holder["hub"] = hub
        return hub

    return use


def test_uses_websocket_without_touching_rest_and_stops_cleanly(fake_hub):
    server = FakeBinance()
    hub = fake_hub(server)
    bot = make_bot()
    thread = run_loop(bot)
    try:
        assert wait_until(lambda: len(bot.klines_seen) >= 3), "el bot debe evaluar velas recibidas por WebSocket"
        assert bot.rest_calls == 0, "con el WebSocket sano no se usa REST"
        assert bot.current_bid == 100.0 and bot.current_ask == 100.5
        assert bot.klines_seen[-1]["high"] == 101.0
        # nunca se evalúa más rápido que MIN_EVAL_INTERVAL_S
        assert len(bot.klines_seen) < 8 / bot.MIN_EVAL_INTERVAL_S
    finally:
        bot.is_running = False
        thread.join(timeout=5)
        server.stop()
    assert not thread.is_alive()
    assert hub.stream_count == 0, "al detener el bot se libera su suscripción"


def test_falls_back_to_rest_when_stream_dies_and_returns_when_it_recovers(fake_hub):
    server = FakeBinance()
    port = server.port
    fake_hub(server)
    bot = make_bot()
    bot.REST_POLL_S = 0.2
    thread = run_loop(bot)
    try:
        assert wait_until(lambda: len(bot.klines_seen) >= 2)
        assert bot.rest_calls == 0
        server.stop()
        assert wait_until(lambda: bot.rest_calls >= 2, timeout=8), "sin WebSocket debe usar el respaldo REST"
        rest_before = bot.rest_calls
        server = FakeBinance(port)
        seen_before = len(bot.klines_seen)
        assert wait_until(lambda: len(bot.klines_seen) > seen_before + 5, timeout=10)
        time.sleep(0.6)
        rest_now = bot.rest_calls
        time.sleep(0.8)
        assert bot.rest_calls == rest_now, "recuperado el WebSocket ya no debe consultar REST"
        assert rest_before >= 2
    finally:
        bot.is_running = False
        thread.join(timeout=5)
        server.stop()


def test_old_generation_thread_exits_after_restart(fake_hub):
    server = FakeBinance()
    fake_hub(server)
    bot = make_bot()
    thread = run_loop(bot)
    try:
        assert wait_until(lambda: len(bot.klines_seen) >= 1)
        bot._run_generation += 1        # equivale a stop()+start() rápido: is_running sigue en True
        thread.join(timeout=5)
        assert not thread.is_alive(), "el hilo de la generación anterior debe terminar solo"
    finally:
        bot.is_running = False
        server.stop()
