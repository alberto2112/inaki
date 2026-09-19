"""
DigestWriter — regenera el digest markdown de un scope ``(channel, chat_id)``.

Único renderer del digest: antes vivía duplicado dentro de
``ConsolidateMemoryUseCase`` (`_render_digest`/`_write_digest`); a partir de la
tool `memory` unificada (fase 3 del plan `memory-tool-unificada`) hay un
segundo escritor — el `create` en vivo — que necesita el MISMO formato. Se
extrae acá para que ambos compartan una sola fuente de verdad.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from inaki.kernel.domain.agent_settings import MemorySettings
from inaki.kernel.domain.memory import MemoryEntry
from inaki.kernel.ports.memory_port import IMemoryRepository

logger = logging.getLogger(__name__)


class DigestWriter:
    """Regenera el digest markdown de un scope ``(channel, chat_id)`` desde la DB de recuerdos.

    Único renderer del digest: lo usan la consolidación nocturna y la tool
    ``memory``. ``write`` es best-effort (nunca propaga excepciones): un
    fallo de disco no puede abortar ni una consolidación ni un turno.
    """

    def __init__(
        self,
        memory: IMemoryRepository,
        *,
        agent_id: str,
        settings: MemorySettings,
    ) -> None:
        self._memory = memory
        self._agent_id = agent_id
        self._settings = settings

    def render(self, memories: list[MemoryEntry]) -> str:
        """Renderiza la lista de recuerdos (ya ordenada por el caller) a markdown."""
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        lines = [
            "# Recuerdos sobre el usuario",
            f"<!-- Generado por inaki — {now_iso} -->",
            "",
        ]
        for m in memories:
            date_str = (m.created_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
            tag_suffix = f" ({', '.join(m.tags)})" if m.tags else ""
            lines.append(f"- [{date_str}] {m.content}{tag_suffix}")
        return "\n".join(lines) + "\n"

    async def write(self, channel: str | None, chat_id: str | None) -> None:
        """
        Regenera el digest markdown del scope ``(channel, chat_id)``.

        Nunca propaga excepciones — un fallo no aborta al caller (consolidación
        nocturna o un `create` en vivo de la tool `memory`).
        """
        try:
            latest = await self._memory.get_recent(
                self._settings.digest_size,
                agent_id=self._agent_id,
                channel=channel,
                chat_id=chat_id,
            )
            markdown = self.render(latest)
            path = self._settings.resolved_digest_path(channel, chat_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown, encoding="utf-8")
            logger.info(
                "Digest scope=(%r, %r) regenerado: %s (%d recuerdos)",
                channel,
                chat_id,
                path,
                len(latest),
            )
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.error(
                "No se pudo regenerar el digest scope=(%r, %r): %s",
                channel,
                chat_id,
                exc,
            )
