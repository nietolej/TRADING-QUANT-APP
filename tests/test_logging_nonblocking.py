"""Una consola bloqueada (ventana de Windows seleccionada) no debe congelar a la aplicación ni al vigilante."""
import asyncio
import io
import logging
import os
import queue
import threading
import time

from app_runtime import logging_setup as ls
from app_runtime.watchdog import LoopWatchdog, format_all_stacks


class BlockingStream(io.StringIO):
    """Imita una consola pausada: write() no vuelve hasta que se libera."""

    def __init__(self):
        super().__init__()
        self.release = threading.Event()
        self.entered = threading.Event()

    def write(self, s):
        self.entered.set()
        self.release.wait(30)
        return super().write(s)


def test_blocked_console_never_blocks_the_caller_and_the_file_still_gets_logs(tmp_path):
    logger = logging.getLogger("tqa_test_blocking")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    console_stream = BlockingStream()
    fmt = logging.Formatter("%(message)s")
    file_handler = logging.FileHandler(tmp_path / "app.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler(console_stream)
    console.setFormatter(fmt)
    listeners = ls.attach_async_handlers(logger, file_handler, console)
    try:
        t0 = time.perf_counter()
        for i in range(500):
            logger.info("mensaje %d", i)
        elapsed = time.perf_counter() - t0
        assert console_stream.entered.wait(5), "el hilo de consola debería haberse quedado bloqueado escribiendo"
        assert elapsed < 1.0, f"registrar no debe esperar a la consola (tardó {elapsed:.2f}s)"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and "mensaje 499" not in (tmp_path / "app.log").read_text(encoding="utf-8"):
            time.sleep(0.05)
        assert "mensaje 499" in (tmp_path / "app.log").read_text(encoding="utf-8"), "el archivo sigue recibiendo logs"
    finally:
        console_stream.release.set()
        for listener in listeners:
            listener.stop()


def test_full_queue_drops_instead_of_blocking(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "QUEUE_MAX", 5)
    logger = logging.getLogger("tqa_test_full")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    stream = BlockingStream()
    handler = logging.StreamHandler(stream)
    listeners = ls.attach_async_handlers(logger, None, handler)
    try:
        t0 = time.perf_counter()
        for i in range(200):
            logger.info("x%d", i)
        assert time.perf_counter() - t0 < 1.0
    finally:
        stream.release.set()
        for listener in listeners:
            try:
                listener.stop()   # con la cola aún llena el centinela puede no caber: no importa al salir
            except queue.Full:
                pass


def test_watchdog_writes_the_thread_dump_even_when_logging_is_stuck(tmp_path, monkeypatch):
    monkeypatch.setenv("TQA_LOG_DIR", str(tmp_path))
    blocked = threading.Event()

    class StuckHandler(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.CRITICAL:     # solo se atasca el aviso del vigilante, no el arranque
                blocked.set()
                time.sleep(30)

    stuck = StuckHandler()
    watchdog_logger = logging.getLogger("LoopWatchdog")
    watchdog_logger.addHandler(stuck)
    dog = LoopWatchdog("unit", threshold=1.0)

    async def scenario():
        await dog.start()
        await asyncio.sleep(0.5)
        time.sleep(4.5)          # bloquea el event loop (como una llamada síncrona lenta)
        await asyncio.sleep(0.2)

    try:
        asyncio.run(scenario())
    finally:
        watchdog_logger.removeHandler(stuck)
    dump = tmp_path / "unit.stall.log"
    assert dump.exists(), "el volcado debe escribirse antes de tocar el logging"
    text = dump.read_text(encoding="utf-8")
    assert "loop detenido" in text and "--- hilo" in text and "scenario" in text
    dog._stop.set()


def test_format_all_stacks_names_threads():
    started = threading.Event()
    stop = threading.Event()

    def worker():
        started.set()
        stop.wait(5)

    t = threading.Thread(target=worker, name="hilo-de-prueba")
    t.start()
    started.wait(2)
    try:
        assert "hilo-de-prueba" in format_all_stacks()
    finally:
        stop.set()
        t.join()


def test_tests_never_log_into_the_real_logs_folder():
    assert os.environ["TQA_LOG_DIR"] != ls.DEFAULT_LOG_DIR
    assert ls.get_log_dir() == os.environ["TQA_LOG_DIR"]
