"""
Tareas en segundo plano que no pierden sus errores.

`asyncio.create_task(coro)` sin guardar la referencia tiene dos problemas: el bucle solo
mantiene una referencia débil (la tarea puede ser recolectada antes de terminar) y, si falla,
la excepción solo aparece —a veces— al recolectarla. `spawn` conserva la referencia y registra
cualquier excepción en el log en cuanto ocurre.
"""
import asyncio
import logging
from typing import Any, Coroutine, Optional, Set

logger = logging.getLogger("BackgroundTasks")

_tasks: Set["asyncio.Task[Any]"] = set()


def _on_done(task: "asyncio.Task[Any]") -> None:
    _tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Tarea en segundo plano '%s' falló: %r", task.get_name(), exc, exc_info=exc)


def spawn(coro: Coroutine[Any, Any, Any], name: Optional[str] = None) -> "asyncio.Task[Any]":
    """Lanza `coro` como tarea de fondo con referencia fuerte y registro de errores."""
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_on_done)
    return task
