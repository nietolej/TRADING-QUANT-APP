"""
Configuración de logging compartida por el servidor web y el daemon de bots.

Antes el servidor web no configuraba nada (los `logger.info` se perdían) y el daemon solo
escribía en consola: al cerrar la terminal no quedaba rastro de qué hizo cada bot ni de por
qué falló una orden. Aquí se deja un archivo rotativo por proceso en `logs/` (ya ignorado por
git) y se habilita `faulthandler`, que vuelca la pila de todos los hilos ante un fallo fatal.
"""
import faulthandler
import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(process)d] %(name)s: %(message)s"

_configured = False
_fault_file = None


def setup_logging(process_name: str, level: int = logging.INFO) -> str:
    """Configura el logging raíz del proceso. Idempotente. Devuelve la ruta del archivo de log."""
    global _configured, _fault_file
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"{process_name}.log")
    if _configured:
        return log_path

    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8", delay=True
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)

    # Librerías muy verbosas a nivel INFO/DEBUG que ahogarían el log de la app.
    for noisy in ("urllib3", "websockets", "asyncio", "matplotlib", "PIL", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        _fault_file = open(os.path.join(LOG_DIR, f"{process_name}.faulthandler.log"), "a", encoding="utf-8")
        faulthandler.enable(file=_fault_file, all_threads=True)
    except Exception:
        logging.getLogger(__name__).warning("No se pudo habilitar faulthandler", exc_info=True)

    _configured = True
    logging.getLogger(__name__).info("Logging iniciado para '%s' -> %s", process_name, log_path)
    return log_path
