"""
Configuración de logging compartida por el servidor web y el daemon de bots.

Antes el servidor web no configuraba nada (los `logger.info` se perdían) y el daemon solo
escribía en consola: al cerrar la terminal no quedaba rastro de qué hizo cada bot ni de por
qué falló una orden. Aquí se deja un archivo rotativo por proceso en `logs/` (ya ignorado por
git) y se habilita `faulthandler`, que vuelca la pila de todos los hilos ante un fallo fatal.

IMPORTANTE (Windows): escribir en una consola pausada BLOQUEA al proceso. Basta con hacer clic dentro
de la ventana (modo "QuickEdit"/selección de texto) para que el primer `print`/log posterior se quede
esperando y, con él, el event loop y todos los hilos que registren algo (daemon "colgado" sin causa
aparente mientras la ventana está seleccionada). Por eso: (1) se desactiva QuickEdit al arrancar,
(2) la consola y el archivo se escriben desde hilos propios detrás de colas, de modo que ni una
consola bloqueada ni un disco lento detienen a la aplicación, y (3) el log de accesos HTTP de uvicorn
—una línea por petición, incluidos los /health cada pocos segundos— se silencia.
"""
import atexit
import faulthandler
import logging
import os
import queue
import sys
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from typing import List, Optional

DEFAULT_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
LOG_DIR = DEFAULT_LOG_DIR  # compatibilidad; usar get_log_dir()
LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(process)d] %(name)s: %(message)s"
QUEUE_MAX = 20000

_configured = False
_fault_file = None
_listeners: List[QueueListener] = []


def get_log_dir() -> str:
    """Carpeta de logs; TQA_LOG_DIR permite redirigirla (las pruebas no deben ensuciar la real)."""
    return os.environ.get("TQA_LOG_DIR") or DEFAULT_LOG_DIR


class DropOnFullQueueHandler(QueueHandler):
    """QueueHandler que descarta el registro si la cola está llena en vez de bloquear a quien registra."""

    def enqueue(self, record):
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            pass


def disable_console_quickedit() -> bool:
    """Desactiva el modo de selección (QuickEdit) de la consola de Windows. True si se pudo."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False  # no hay consola adjunta (proceso en segundo plano)
        ENABLE_QUICK_EDIT_MODE, ENABLE_EXTENDED_FLAGS = 0x0040, 0x0080
        return bool(kernel32.SetConsoleMode(handle, (mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS))
    except Exception:
        return False


def attach_async_handlers(
    logger: logging.Logger, file_handler: Optional[logging.Handler], console_handler: Optional[logging.Handler]
) -> List[QueueListener]:
    """Conecta los handlers reales al logger a través de colas con hilos escritores independientes."""
    listeners = []
    for handler in (file_handler, console_handler):
        if handler is None:
            continue
        q: "queue.Queue" = queue.Queue(maxsize=QUEUE_MAX)
        logger.addHandler(DropOnFullQueueHandler(q))
        listener = QueueListener(q, handler, respect_handler_level=True)
        listener.start()
        listeners.append(listener)
    return listeners


def _stop_listeners() -> None:
    for listener in _listeners:
        try:
            listener.stop()
        except Exception:
            pass


def setup_logging(process_name: str, level: int = logging.INFO) -> str:
    """Configura el logging raíz del proceso. Idempotente. Devuelve la ruta del archivo de log."""
    global _configured, _fault_file
    log_dir = get_log_dir()
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{process_name}.log")
    if _configured:
        return log_path

    disable_console_quickedit()

    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8", delay=True)
    file_handler.setFormatter(formatter)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    # Los handlers síncronos que ya hubiera (p. ej. los de basicConfig) se sustituyen por los asíncronos.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    _listeners.extend(attach_async_handlers(root, file_handler, console))
    atexit.register(_stop_listeners)

    # uvicorn escribe sus logs directamente en la consola desde el event loop: se redirigen al root (colas) y
    # el log de accesos (una línea por petición) se silencia.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    # Librerías muy verbosas a nivel INFO/DEBUG que ahogarían el log de la app.
    for noisy in ("urllib3", "websockets", "asyncio", "matplotlib", "PIL", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        _fault_file = open(os.path.join(log_dir, f"{process_name}.faulthandler.log"), "a", encoding="utf-8")
        faulthandler.enable(file=_fault_file, all_threads=True)
    except Exception:
        logging.getLogger(__name__).warning("No se pudo habilitar faulthandler", exc_info=True)

    _configured = True
    logging.getLogger(__name__).info("Logging iniciado para '%s' -> %s", process_name, log_path)
    return log_path
