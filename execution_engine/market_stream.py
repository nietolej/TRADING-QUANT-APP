"""
Datos de mercado en tiempo real por WebSocket (Binance Futures).

Antes cada bot consultaba por REST, cada 2 s, el ticker y la última vela: con ~0.5 s por llamada
(medido) el precio que veía la estrategia llevaba ~3 s de retraso, y N bots repetían las mismas
peticiones. Ahora Binance EMPUJA cada cambio del libro (bookTicker) y de la vela en curso (kline):

- una sola conexión por (red, símbolo, intervalo), compartida por todos los bots que la necesitan;
- reconexión automática con espera creciente;
- si los datos dejan de llegar (`is_fresh()` en falso) el bot vuelve solo al polling REST, así que un
  corte del WebSocket nunca deja al bot ciego.

Endpoints (verificados en vivo): Testnet sirve todo por `stream.binancefuture.com/stream`; Mainnet
separó los streams — `kline` va por `/market/stream` y `bookTicker` por `/public/stream` (el endpoint
antiguo `/stream` ya no emite velas, en silencio).
"""
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from websockets.sync.client import connect

logger = logging.getLogger("MarketStream")

# Sin datos durante más de esto => el stream se considera caído y el bot usa REST.
STALE_AFTER_S = 6.0
RECONNECT_BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 15.0)


def default_urls(symbol: str, interval: str, use_testnet: bool) -> List[Tuple[str, str]]:
    """[(nombre, url)] de las conexiones necesarias para (símbolo, intervalo) en la red indicada."""
    s = symbol.replace("/", "").lower()
    kline, book = f"{s}@kline_{interval}", f"{s}@bookTicker"
    if use_testnet:
        return [("testnet", f"wss://stream.binancefuture.com/stream?streams={kline}/{book}")]
    return [
        ("mainnet-market", f"wss://fstream.binance.com/market/stream?streams={kline}"),
        ("mainnet-public", f"wss://fstream.binance.com/public/stream?streams={book}"),
    ]


class Subscription:
    """Vista de un suscriptor (un bot) sobre un flujo compartido."""

    def __init__(self, stream: "SymbolStream"):
        self._stream = stream
        self._event = threading.Event()
        self._last_seq = -1
        self.closed = False

    def _notify(self):
        self._event.set()

    def wait_for_update(self, timeout: float) -> bool:
        """Espera hasta `timeout` s a que llegue un dato nuevo. True si llegó."""
        got = self._event.wait(timeout)
        self._event.clear()
        return got

    def is_fresh(self) -> bool:
        return self._stream.is_fresh()

    def latest(self) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """(kline_en_curso, mejor_bid_ask) más recientes, o None si aún no llegaron."""
        return self._stream.latest()

    def latest_closed(self) -> Optional[Dict[str, Any]]:
        """Última vela CERRADA (mensaje con x=true) o None. latest() solo guarda la actualización
        más reciente: si la primera de la vela nueva llega antes de que el bot lea, el cierre final
        de la anterior se perdía y la señal se calculaba con un precio de hasta ~250 ms antes."""
        return self._stream.latest_closed()

    def kline_changed(self) -> bool:
        """True si la vela cambió desde la última vez que se preguntó (evita reevaluar lo mismo)."""
        seq = self._stream.kline_seq
        changed = seq != self._last_seq
        self._last_seq = seq
        return changed

    def close(self):
        if not self.closed:
            self.closed = True
            self._stream.unsubscribe(self)


class SymbolStream:
    """Conexiones WebSocket compartidas de un (red, símbolo, intervalo)."""

    def __init__(self, symbol: str, interval: str, use_testnet: bool,
                 urls: Optional[List[Tuple[str, str]]] = None, stale_after_s: float = STALE_AFTER_S):
        self.symbol = symbol.replace("/", "").upper()
        self.interval = interval
        self.use_testnet = use_testnet
        self.stale_after_s = stale_after_s
        self._urls = urls if urls is not None else default_urls(symbol, interval, use_testnet)
        self._subs: List[Subscription] = []
        self._lock = threading.Lock()
        self._kline: Optional[Dict[str, Any]] = None
        self._closed_kline: Optional[Dict[str, Any]] = None
        self._book: Optional[Dict[str, Any]] = None
        self._kline_at = 0.0
        self._book_at = 0.0
        self.kline_seq = 0
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []

    # ── ciclo de vida ───────────────────────────────────────────────────────

    def start(self):
        for name, url in self._urls:
            t = threading.Thread(target=self._run, args=(name, url), name=f"ws-{self.symbol}-{name}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self._stop.set()

    def subscribe(self) -> Subscription:
        sub = Subscription(self)
        with self._lock:
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> int:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)
            return len(self._subs)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    # ── datos ───────────────────────────────────────────────────────────────

    def latest(self):
        with self._lock:
            return self._kline, self._book

    def latest_closed(self):
        with self._lock:
            return self._closed_kline

    def is_fresh(self) -> bool:
        now = time.monotonic()
        return (
            self._kline is not None and self._book is not None
            and (now - self._kline_at) < self.stale_after_s
            and (now - self._book_at) < self.stale_after_s
        )

    def _handle(self, raw: str):
        msg = json.loads(raw)
        data = msg.get("data", msg)
        event = data.get("e")
        now = time.monotonic()
        if event == "kline":
            k = data["k"]
            kline = {
                "timestamp": int(k["t"]), "open": float(k["o"]), "high": float(k["h"]),
                "low": float(k["l"]), "close": float(k["c"]), "volume": float(k["v"]),
                "closed": bool(k.get("x", False)),
            }
            with self._lock:
                self._kline, self._kline_at = kline, now
                if kline["closed"]:
                    self._closed_kline = kline
                self.kline_seq += 1
                subs = list(self._subs)
        elif event == "bookTicker":
            book = {
                "bid": float(data["b"]), "ask": float(data["a"]),
                "bid_qty": float(data["B"]), "ask_qty": float(data["A"]),
            }
            with self._lock:
                self._book, self._book_at = book, now
                subs = list(self._subs)
        else:
            return
        for sub in subs:
            sub._notify()

    def _run(self, name: str, url: str):
        attempt = 0
        while not self._stop.is_set():
            try:
                with connect(url, open_timeout=10, ping_interval=20, ping_timeout=20) as ws:
                    if attempt:
                        logger.info("WebSocket %s/%s reconectado", self.symbol, name)
                    attempt = 0
                    while not self._stop.is_set():
                        try:
                            raw = ws.recv(timeout=max(self.stale_after_s * 2, 10.0))
                        except TimeoutError:
                            logger.warning("WebSocket %s/%s sin datos: reconectando", self.symbol, name)
                            break
                        try:
                            self._handle(raw)
                        except Exception:
                            logger.exception("Mensaje de WebSocket ilegible en %s/%s", self.symbol, name)
            except Exception as e:
                logger.warning("WebSocket %s/%s caído (%s: %s)", self.symbol, name, type(e).__name__, str(e)[:100])
            if self._stop.is_set():
                break
            delay = RECONNECT_BACKOFF_S[min(attempt, len(RECONNECT_BACKOFF_S) - 1)]
            attempt += 1
            self._stop.wait(delay)


class StreamHub:
    """Registro de flujos compartidos: un SymbolStream por (red, símbolo, intervalo)."""

    def __init__(self, stream_factory: Optional[Callable[..., SymbolStream]] = None):
        self._streams: Dict[Tuple[bool, str, str], SymbolStream] = {}
        self._lock = threading.Lock()
        self._factory = stream_factory or SymbolStream

    def subscribe(self, symbol: str, interval: str, use_testnet: bool) -> Subscription:
        key = (bool(use_testnet), symbol.replace("/", "").upper(), interval)
        with self._lock:
            stream = self._streams.get(key)
            if stream is None:
                stream = self._factory(symbol, interval, use_testnet)
                stream.start()
                self._streams[key] = stream
            sub = stream.subscribe()
            sub._hub_key = key
        return sub

    def release(self, sub: Optional[Subscription]):
        """Da de baja un suscriptor; el flujo se cierra cuando ya nadie lo usa."""
        if sub is None:
            return
        stream = sub._stream
        sub.closed = True
        remaining = stream.unsubscribe(sub)
        if remaining == 0:
            with self._lock:
                key = getattr(sub, "_hub_key", (stream.use_testnet, stream.symbol, stream.interval))
                if self._streams.get(key) is stream and stream.subscriber_count == 0:
                    del self._streams[key]
                    stream.stop()

    @property
    def stream_count(self) -> int:
        with self._lock:
            return len(self._streams)


stream_hub = StreamHub()
