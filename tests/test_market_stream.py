"""El flujo WebSocket entrega datos, se comparte entre bots, detecta cortes y se reconecta solo."""
import json
import threading
import time

import pytest
from websockets.sync.server import serve

from execution_engine import market_stream as ms


class FakeBinance:
    """Servidor WebSocket local que emite velas y bookTicker como lo haría Binance."""

    def __init__(self, port=0, garbage=False):
        self.messages = 0
        self.garbage = garbage
        self._stop = threading.Event()
        self.server = serve(self._handler, "127.0.0.1", port)
        self.port = self.server.socket.getsockname()[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _handler(self, ws):
        i = 0
        try:
            if self.garbage:
                ws.send("esto no es json")
                ws.send(json.dumps({"stream": "x", "data": {"e": "otroEvento"}}))
            while not self._stop.is_set():
                i += 1
                ws.send(json.dumps({"stream": "btcusdt@kline_1m", "data": {
                    "e": "kline", "k": {"t": 1_700_000_000_000, "o": "100", "h": "101", "l": "99",
                                        "c": str(100 + i), "v": "5", "x": False}}}))
                ws.send(json.dumps({"stream": "btcusdt@bookTicker", "data": {
                    "e": "bookTicker", "b": "100.0", "B": "3", "a": "100.5", "A": "4"}}))
                self.messages += 2
                time.sleep(0.05)
        except Exception:
            pass

    def stop(self):
        self._stop.set()
        self.server.shutdown()


def wait_until(predicate, timeout=6.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def fake():
    server = FakeBinance()
    yield server
    server.stop()


def make_stream(port, **kw):
    return ms.SymbolStream("BTC/USDT", "1m", True, urls=[("fake", f"ws://127.0.0.1:{port}")], **kw)


def test_receives_and_parses_kline_and_book(fake):
    stream = make_stream(fake.port)
    stream.start()
    try:
        sub = stream.subscribe()
        assert wait_until(sub.is_fresh), "no llegaron datos del WebSocket"
        kline, book = sub.latest()
        assert kline["timestamp"] == 1_700_000_000_000 and kline["high"] == 101.0 and kline["closed"] is False
        assert (book["bid"], book["ask"]) == (100.0, 100.5)
    finally:
        stream.stop()


def test_wait_for_update_and_kline_changed(fake):
    stream = make_stream(fake.port)
    stream.start()
    try:
        sub = stream.subscribe()
        assert wait_until(sub.is_fresh)
        assert sub.kline_changed() is True
        assert sub.wait_for_update(timeout=2.0) is True
        assert wait_until(sub.kline_changed), "la vela sigue cambiando"
    finally:
        stream.stop()


def test_goes_stale_when_server_dies_then_recovers_after_reconnect():
    server = FakeBinance()
    port = server.port
    stream = make_stream(port, stale_after_s=1.0)
    stream.start()
    sub = stream.subscribe()
    try:
        assert wait_until(sub.is_fresh)
        server.stop()
        assert wait_until(lambda: not sub.is_fresh(), timeout=5), "debe declararse obsoleto sin datos"
        server = FakeBinance(port)               # el servidor vuelve en el mismo puerto
        assert wait_until(sub.is_fresh, timeout=10), "debe reconectarse solo"
    finally:
        stream.stop()
        server.stop()


def test_hub_shares_one_stream_and_closes_it_when_unused(fake):
    made = []

    def factory(symbol, interval, use_testnet):
        stream = ms.SymbolStream(symbol, interval, use_testnet, urls=[("fake", f"ws://127.0.0.1:{fake.port}")])
        made.append(stream)
        return stream

    hub = ms.StreamHub(stream_factory=factory)
    a = hub.subscribe("BTC/USDT", "1m", True)
    b = hub.subscribe("BTCUSDT", "1m", True)        # mismo símbolo escrito distinto
    c = hub.subscribe("BTC/USDT", "5m", True)       # otro intervalo: otro flujo
    assert len(made) == 2 and hub.stream_count == 2
    assert wait_until(a.is_fresh) and wait_until(b.is_fresh)
    hub.release(a)
    assert hub.stream_count == 2, "aún queda un bot usando el flujo de 1m"
    hub.release(b)
    assert hub.stream_count == 1
    hub.release(c)
    assert hub.stream_count == 0


def test_endpoints_per_network():
    testnet = dict(ms.default_urls("BTC/USDT", "1m", True))
    assert list(testnet) == ["testnet"] and "stream.binancefuture.com/stream" in testnet["testnet"]
    assert "btcusdt@kline_1m" in testnet["testnet"] and "btcusdt@bookTicker" in testnet["testnet"]
    mainnet = dict(ms.default_urls("ETH/USDT", "1h", False))
    assert "fstream.binance.com/market/stream?streams=ethusdt@kline_1h" in mainnet["mainnet-market"]
    assert "fstream.binance.com/public/stream?streams=ethusdt@bookTicker" in mainnet["mainnet-public"]


def test_malformed_messages_do_not_kill_the_stream():
    server = FakeBinance(garbage=True)
    stream = make_stream(server.port)
    stream.start()
    try:
        sub = stream.subscribe()
        assert wait_until(sub.is_fresh), "los mensajes ilegibles o desconocidos no deben cortar el flujo"
    finally:
        stream.stop()
        server.stop()
