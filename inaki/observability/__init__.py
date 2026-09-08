"""Observabilidad del proceso: logging unificado, modo debug y trazas de turno.

Un solo stack de logging (``logging`` de la stdlib) con dos formatos —
``console`` legible y ``json`` una línea por evento — que en ambos casos
publican los campos ``extra`` de cada log. Antes había dos stacks (structlog
configurado, stdlib usado) y el formato ``%(message)s`` pelado se comía la
hora, el nivel, el logger y todos los ``extra``.

El modo debug (``app.debug`` o ``--debug``) sube el nivel a ``DEBUG`` y activa
las trazas de turno en ``<home>/debug/turns/<agent_id>.jsonl``.
"""

from inaki.observability.debug import is_debug_enabled, set_debug_override
from inaki.observability.log_setup import setup_logging
from inaki.observability.startup import startup_event
from inaki.observability.tracer import JsonlTurnTracer

__all__ = [
    "JsonlTurnTracer",
    "is_debug_enabled",
    "set_debug_override",
    "setup_logging",
    "startup_event",
]
