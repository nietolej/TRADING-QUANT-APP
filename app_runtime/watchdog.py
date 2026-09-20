"""
Vigilante del event loop.

Si el bucle de asyncio deja de responder (interbloqueo de locks, una llamada bloqueante sin
timeout, un cálculo largo, etc.) el servidor entero se congela y hasta ahora no quedaba ninguna
pista de dónde. Este vigilante lanza una tarea que marca un "latido" cada segundo y un hilo aparte
que, si el latido se detiene más de `threshold` segundos, vuelca la pila de TODOS los hilos al
archivo `logs/<nombre>.stall.log` y después lo anota en el log. Así el siguiente cuelgue se
diagnostica con datos en vez de con hipótesis.

El volcado se escribe PRIMERO y con Python puro (`sys._current_frames`), sin pasar por el logging ni
la consola: si la consola está bloqueada (ventana de Windows seleccionada) o el logging está
atascado, el volcado igualmente queda en disco.
"""
import asyncio
import logging
import os
import sys
import threading
import time
import traceback

from .logging_setup import get_log_dir

logger = logging.getLogger("LoopWatchdog")


def format_all_stacks() -> str:
    """Pila de todos los hilos vivos, con su nombre."""
    names = {t.ident: t.name for t in threading.enumerate()}
    parts = []
    for ident, frame in sys._current_frames().items():
        parts.append(f"\n--- hilo {names.get(ident, '?')} ({ident}) ---\n" + "".join(traceback.format_stack(frame)))
    return "".join(parts)


class LoopWatchdog:
    def __init__(self, name: str, threshold: float = 10.0, repeat_every: float = 60.0):
        self.name = name
        self.threshold = threshold
        self.repeat_every = repeat_every
        self._beat = time.monotonic()
        self._thread = None
        self._stop = threading.Event()
        self._last_dump = 0.0

    async def start(self):
        """Debe llamarse desde el event loop a vigilar (p. ej. en el arranque de la aplicación)."""
        if self._thread is not None:
            return
        self._beat = time.monotonic()
        asyncio.get_running_loop().create_task(self._heartbeat())
        self._thread = threading.Thread(target=self._watch, name=f"watchdog-{self.name}", daemon=True)
        self._thread.start()
        logger.info("Vigilante del event loop activo (umbral %.0f s).", self.threshold)

    async def _heartbeat(self):
        while True:
            self._beat = time.monotonic()
            await asyncio.sleep(1.0)

    def dump_now(self, stalled_for: float) -> str:
        """Escribe el volcado de hilos en disco y devuelve la ruta del archivo."""
        dump_path = os.path.join(get_log_dir(), f"{self.name}.stall.log")
        os.makedirs(os.path.dirname(dump_path), exist_ok=True)
        with open(dump_path, "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} loop detenido {stalled_for:.0f}s =====")
            f.write(format_all_stacks())
            f.flush()
        return dump_path

    def _watch(self):
        while not self._stop.wait(2.0):
            stalled_for = time.monotonic() - self._beat
            if stalled_for < self.threshold:
                self._last_dump = 0.0
                continue
            now = time.monotonic()
            if self._last_dump and now - self._last_dump < self.repeat_every:
                continue
            self._last_dump = now
            try:
                dump_path = self.dump_now(stalled_for)
            except Exception:
                logger.exception("No se pudo escribir el volcado de hilos")
                continue
            logger.critical(
                "EVENT LOOP SIN RESPONDER desde hace %.0f s. Pila de todos los hilos en %s", stalled_for, dump_path
            )
