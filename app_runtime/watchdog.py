"""
Vigilante del event loop.

Si el bucle de asyncio deja de responder (interbloqueo de locks, una llamada bloqueante sin
timeout, etc.) el servidor entero se congela y hasta ahora no quedaba ninguna pista de dónde.
Este vigilante lanza una tarea que marca un "latido" cada segundo y un hilo aparte que, si el
latido se detiene más de `threshold` segundos, vuelca la pila de TODOS los hilos (con
`faulthandler`) al log y a un archivo dedicado. Así el siguiente cuelgue se diagnostica con
datos en vez de con hipótesis.
"""
import asyncio
import faulthandler
import logging
import os
import threading
import time

from .logging_setup import LOG_DIR

logger = logging.getLogger("LoopWatchdog")


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
        """Debe llamarse desde el event loop a vigilar (p. ej. en el evento startup de FastAPI)."""
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

    def _watch(self):
        dump_path = os.path.join(LOG_DIR, f"{self.name}.stall.log")
        while not self._stop.wait(2.0):
            stalled_for = time.monotonic() - self._beat
            if stalled_for < self.threshold:
                self._last_dump = 0.0
                continue
            now = time.monotonic()
            if self._last_dump and now - self._last_dump < self.repeat_every:
                continue
            self._last_dump = now
            logger.critical(
                "EVENT LOOP SIN RESPONDER desde hace %.0f s. Pila de todos los hilos en %s",
                stalled_for, dump_path,
            )
            try:
                with open(dump_path, "a", encoding="utf-8") as f:
                    f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} loop detenido {stalled_for:.0f}s =====\n")
                    f.flush()
                    faulthandler.dump_traceback(file=f, all_threads=True)
            except Exception:
                logger.exception("No se pudo escribir el volcado de hilos")
