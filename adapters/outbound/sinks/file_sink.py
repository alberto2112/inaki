"""FileSink — append de mensajes a un archivo local.

Formato de línea: ``<ISO8601> | <text>``.
Sin sandbox: el path del usuario se respeta literal. Crea el directorio padre
si no existe. Cualquier ``OSError`` (permisos, disco lleno) propaga hacia arriba.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.domain.value_objects.dispatch_result import DispatchResult
from core.ports.outbound.outbound_sink_port import IOutboundSink


class FileSink(IOutboundSink):
    """Append sink a archivo. Target: ``file://<ruta-absoluta>``."""

    prefix = "file"

    async def send(self, target: str, text: str) -> DispatchResult:
        if not target.startswith("file://"):
            raise ValueError(f"FileSink espera target con prefix 'file://', recibió: '{target}'")
        ruta = Path(target.removeprefix("file://"))
        ruta.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        linea = f"{timestamp} | {text}\n"
        with ruta.open("a", encoding="utf-8") as fh:
            fh.write(linea)
        return DispatchResult(original_target=target, resolved_target=target)
