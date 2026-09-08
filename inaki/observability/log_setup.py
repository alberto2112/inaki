"""Configuración del logging del proceso — un solo stack, dos formatos.

Los dos formatters publican los campos ``extra`` de cada ``LogRecord`` (lo que
un ``logger.info("...", extra={"chat_id": ...})`` adjunta). Con el viejo
``format="%(message)s"`` esos campos se perdían: los eventos del broadcast
(``broadcast.message.received`` con ``from_agent_id`` y ``chat_id``) llegaban a
``journalctl`` como una etiqueta pelada, sin ninguno de sus datos.

``setup_logging`` es idempotente: el reload del daemon la vuelve a llamar y
reemplaza SOLO el handler que instaló antes (nunca toca handlers ajenos, como
el de captura de pytest).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import IO, Literal

LogFormat = Literal["console", "json"]

# Atributos que trae todo LogRecord: lo que NO esté acá es un ``extra`` del caller.
_CAMPOS_STDLIB = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys() | {"message", "asctime"}
)
_MARCA_HANDLER = "_inaki_handler"


def _extras(record: logging.LogRecord) -> dict[str, object]:
    return {
        k: v
        for k, v in record.__dict__.items()
        if k not in _CAMPOS_STDLIB and not k.startswith("_")
    }


class ConsoleFormatter(logging.Formatter):
    """``HH:MM:SS NIVEL logger: mensaje  clave=valor ...`` — para terminal y journalctl."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = _extras(record)
        if extras:
            base += "  " + " ".join(f"{k}={v!r}" for k, v in extras.items())
        return base


class JsonFormatter(logging.Formatter):
    """Una línea JSON por evento: ``ts``, ``level``, ``logger``, ``msg`` + los ``extra``."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(_extras(record))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(
    log_level: str = "INFO",
    log_format: LogFormat = "console",
    *,
    stream: IO[str] | None = None,
) -> None:
    """Instala el handler raíz de Inaki (reemplazando el propio si ya existía).

    Args:
        log_level: nombre de nivel de ``logging``; desconocido → ``INFO``.
        log_format: ``console`` (legible) o ``json`` (una línea por evento).
        stream: destino; default ``sys.stdout``. Inyectable para tests.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)
    formatter: logging.Formatter = JsonFormatter() if log_format == "json" else ConsoleFormatter()

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(formatter)
    setattr(handler, _MARCA_HANDLER, True)

    root = logging.getLogger()
    for existente in list(root.handlers):
        if getattr(existente, _MARCA_HANDLER, False):
            root.removeHandler(existente)
    root.addHandler(handler)
    root.setLevel(level)
