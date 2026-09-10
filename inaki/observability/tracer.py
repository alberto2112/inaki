"""``JsonlTurnTracer`` — escribe los eventos del turno como líneas JSON por agente.

Fichero: ``<dir>/<agent_id>.jsonl`` (append). Cada línea lleva ``ts``, ``event``,
el contexto bindeado (``agent_id``, ``turn_id``, ``channel``, ``chat_id``) y los
campos del evento. Los strings largos se recortan a ``max_field_chars`` con un
marcador explícito: el objetivo es diagnosticar, no archivar.

Es best-effort por contrato del port: cualquier fallo de escritura se loguea
una vez (WARNING) y el turno sigue.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from inaki.kernel.ports.turn_tracer_port import ITurnTracer

logger = logging.getLogger(__name__)

_SIN_AGENTE = "sin-agente"


class JsonlTurnTracer(ITurnTracer):
    def __init__(
        self,
        directorio: Path,
        *,
        max_field_chars: int = 4000,
        context: dict[str, object] | None = None,
    ) -> None:
        self._dir = Path(directorio)
        self._max = max_field_chars
        self._context: dict[str, object] = dict(context or {})
        self._fallo_reportado = False

    def bind(self, **context: object) -> JsonlTurnTracer:
        hijo = JsonlTurnTracer(
            self._dir, max_field_chars=self._max, context={**self._context, **context}
        )
        hijo._fallo_reportado = self._fallo_reportado
        return hijo

    def trace(self, event: str, **fields: object) -> None:
        try:
            linea = {
                "ts": datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds"),
                "event": event,
                **self._context,
                **{k: self._recortar(v) for k, v in fields.items()},
            }
            agent_id = str(self._context.get("agent_id") or _SIN_AGENTE)
            self._dir.mkdir(parents=True, exist_ok=True)
            with (self._dir / f"{agent_id}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(linea, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001 — el tracer jamás rompe el turno
            if not self._fallo_reportado:
                self._fallo_reportado = True
                logger.warning(
                    "Traza de turno deshabilitada: no se pudo escribir en %s: %s", self._dir, exc
                )

    def _recortar(self, valor: object) -> object:
        """Recorta strings largos, recursivo sobre listas y dicts."""
        if isinstance(valor, str):
            if len(valor) <= self._max:
                return valor
            return valor[: self._max] + f"… [recortado, {len(valor)} chars]"
        if isinstance(valor, dict):
            return {k: self._recortar(v) for k, v in valor.items()}
        if isinstance(valor, (list, tuple)):
            return [self._recortar(v) for v in valor]
        return valor
