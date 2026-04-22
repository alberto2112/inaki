"""
ConsolidateMemoryUseCase — extrae recuerdos del historial y lo trunca.

Flujo:
  1. Cargar mensajes pendientes de procesamiento (`infused = 0`)
  2. Si no hay pendientes → no-op idempotente (sin trim, sin nada)
  3. Enviar esos mensajes al LLM con prompt extractor
  4. El LLM devuelve JSON con lista de recuerdos
  5. Filtrar hechos por min_relevance_score
  6. Para cada hecho: generar embedding + construir MemoryEntry + persistir
  7. Marcar todos los mensajes del agente como `infused = 1`
     (evita que la próxima corrida reprocese mensajes que siguen vivos en
     el buffer tras el trim, lo cual generaría recuerdos duplicados en la
     memoria vectorial)
  8. Regenerar digest markdown (best-effort)
  9. `history.trim(agent_id, keep_last=resolved_keep_last)`
     Preserva los últimos N mensajes como contexto inmediato para el próximo
     turno. El valor sale de `memory.keep_last_messages` (0 = fallback 84).
 10. Si falla en cualquier punto: NO marcar, NO truncar, historial intacto.

El truncado y el marcado son TRANSACCIONALES: solo ocurren si la extracción
y persistencia son exitosas.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.domain.entities.memory import MemoryEntry
from core.domain.entities.message import Role
from core.domain.errors import ConsolidationError
from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.history_port import IHistoryStore
from core.ports.outbound.llm_port import ILLMProvider
from core.ports.outbound.memory_port import IMemoryRepository
from infrastructure.config import MemoryConfig

logger = logging.getLogger(__name__)

_EXTRACTOR_PROMPT_TEMPLATE = """\
## Instructions

You are a long-term memory extractor for a personal AI assistant.
Your role is CONSERVATIVE: only save what has real and lasting value about the user.
When in doubt, do NOT save. It is better to miss a minor detail than to pollute long-term memory with noise.

**SAVE** only when the conversation reveals:
- Personal preferences of the user (food, health, work, family, technology, habits)
- Health information: about the user or their loved ones (diagnoses, reactions to medications or food, allergies, recurring symptoms)
- Significant events: accidents, unusual episodes, emergencies
- Important decisions made when facing a problem that worried the user
- Relevant facts about their personal life, family, work, or surroundings

**NEVER save**:
- Command outputs or technical query results
- Calendar, agenda, or reminder lookups
- Note-taking or dictation
- Trivial questions ("what time is it?", "how much is X?")
- Superficial conversation with no informational value about the user
- Ephemeral information with no value beyond the current conversation

**Memory content format**: include rich context. Not just "<User or User's name> prefers X" but "<User or User's name> prefers X because Y happened in such situation". Context is what makes a memory useful in the future.

**The `relevance` field encodes your confidence that this memory is worth keeping**:
- Close to 1.0 → you are certain this is important and should be preserved
- Close to 0.0 → you are unsure, it might be noise
- Only include memories you feel confident about. If you are genuinely unsure, omit the entry entirely rather than assigning a low relevance score.

Return ONLY valid JSON with the following schema, no additional text:
[
  {{
    "content": "contextually rich description of the fact, preference, or event",
    "relevance": 0.0-1.0,
    "tags": ["tag1", "tag2"],
    "timestamp": "2026-04-09T15:30:00Z"
  }}
]

The "timestamp" field is optional. If included, use the timestamp of the most relevant message (ISO8601 UTC).
If there is NOTHING worth remembering long-term, return an empty array: []

## Conversation:

{history}
"""


@dataclass
class ConsolidationResult:
    memories_extracted: int
    kept_messages: int


class ConsolidateMemoryUseCase:
    def __init__(
        self,
        llm: ILLMProvider,
        memory: IMemoryRepository,
        embedder: IEmbeddingProvider,
        history: IHistoryStore,
        agent_id: str,
        memory_config: MemoryConfig,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._embedder = embedder
        self._history = history
        self._agent_id = agent_id
        self._memory_cfg = memory_config

    async def execute(self) -> str:
        """
        Ejecuta la consolidación completa.
        Retorna un mensaje descriptivo del resultado.
        Lanza ConsolidationError si falla (historial intacto).
        """
        # 1. Cargar solo los mensajes NO procesados aún por el extractor.
        # Si channels_infused está configurado, se filtran los canales para que
        # la consolidación solo incluya mensajes de esos canales (p. ej. solo
        # "telegram" y no mensajes de CLI o daemon que no aportan recuerdos relevantes).
        channels_infused = self._memory_cfg.channels_infused or None
        messages = await self._history.load_uninfused(self._agent_id, channels=channels_infused)
        if not messages:
            return "No hay mensajes nuevos para consolidar."

        # 2. Formatear historial para el LLM (incluye timestamp si está disponible)
        def _fmt(m):
            if m.timestamp is not None:
                ts = m.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
                return f"{m.role.value} [{ts}]: {m.content}"
            return f"{m.role.value}: {m.content}"

        history_text = "\n".join(_fmt(m) for m in messages if m.role in (Role.USER, Role.ASSISTANT))
        prompt = _EXTRACTOR_PROMPT_TEMPLATE.format(history=history_text)

        # 3. Llamar al LLM extractor (consolidación no usa tools → esperamos
        # solo text_blocks; .text concatena por si viniera más de un bloque).
        try:
            response = await self._llm.complete(
                messages=[],
                system_prompt=prompt,
            )
            raw_json = response.text
        except Exception as exc:
            raise ConsolidationError(f"El LLM falló durante la extracción: {exc}") from exc

        # 4. Parsear JSON
        try:
            facts = self._parse_facts(raw_json)
        except ConsolidationError:
            raise
        except Exception as exc:
            raise ConsolidationError(f"Error parseando respuesta del LLM: {exc}") from exc

        if not facts:
            # LLM dice que no hay nada relevante — archivamos igual
            logger.info("El LLM no encontró recuerdos relevantes en el historial")

        # 4b. Filtrar por relevance_score mínimo (ahorra tokens de embedding)
        threshold = self._memory_cfg.min_relevance_score
        filtered: list[dict] = []
        dropped = 0
        for fact in facts:
            try:
                score = float(fact.get("relevance", 0.0))
            except (TypeError, ValueError):
                score = 0.0
            if score >= threshold:
                filtered.append(fact)
            else:
                dropped += 1
        if dropped:
            logger.info(
                "Consolidación: %d recuerdo(s) descartado(s) por relevance < %.2f",
                dropped,
                threshold,
            )
        facts = filtered

        # 5. Generar embeddings y persistir
        stored = 0
        for fact in facts:
            try:
                embedding = await self._embedder.embed_passage(fact["content"])
                created_at = None
                raw_ts = fact.get("timestamp")
                if raw_ts:
                    try:
                        created_at = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pass
                entry = MemoryEntry(
                    content=fact["content"],
                    embedding=embedding,
                    relevance=float(fact.get("relevance", 0.5)),
                    tags=fact.get("tags", []),
                    agent_id=self._agent_id,  # atribución: quién extrajo este hecho
                    created_at=created_at or datetime.now(timezone.utc),
                )
                await self._memory.store(entry)
                stored += 1
            except Exception as exc:
                raise ConsolidationError(
                    f"Error persistiendo recuerdo '{fact.get('content', '')}': {exc}"
                ) from exc

        # 6. Marcar todos los mensajes del agente como infused ANTES del trim.
        # Esto es el gate que evita que la próxima corrida reprocese mensajes
        # que sigan vivos en el buffer (por el keep_last del trim). Si falla,
        # abortamos — los recuerdos ya están persistidos en inaki.db pero el
        # error queda visible y se puede reintentar.
        try:
            await self._history.mark_infused(self._agent_id)
        except Exception as exc:
            raise ConsolidationError(f"Error marcando mensajes como infused: {exc}") from exc

        # 7. Regenerar digest markdown (best-effort, no rompe consolidación)
        await self._write_digest()

        # 8. Truncar historial (solo si llegamos hasta aquí sin errores).
        # Preserva los últimos N mensajes como contexto inmediato para el
        # próximo turno; los recuerdos extraídos ya están en la memoria
        # vectorial, así que el resto del historial es descartable.
        keep_last = self._memory_cfg.resolved_keep_last_messages()
        try:
            await self._history.trim(self._agent_id, keep_last=keep_last)
        except Exception as exc:
            raise ConsolidationError(f"Error truncando historial: {exc}") from exc

        logger.info(
            "Consolidación completada para '%s': %d recuerdo(s) extraído(s), "
            "últimos %d mensaje(s) preservados",
            self._agent_id,
            stored,
            keep_last,
        )
        return f"✓ {stored} recuerdo(s) extraído(s). Historial truncado (últimos {keep_last} mensajes preservados)."

    def _render_digest(self, memories: list[MemoryEntry]) -> str:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        lines = [
            "# Recuerdos sobre el usuario",
            f"<!-- Generado por /consolidate — {now_iso} -->",
            "",
        ]
        for m in memories:
            date_str = (m.created_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
            tag_suffix = f" ({', '.join(m.tags)})" if m.tags else ""
            lines.append(f"- [{date_str}] {m.content}{tag_suffix}")
        return "\n".join(lines) + "\n"

    async def _write_digest(self) -> None:
        """Regenera el digest markdown. Nunca propaga excepciones — un fallo no aborta la consolidación."""
        try:
            latest = await self._memory.get_recent(self._memory_cfg.digest_size)
            markdown = self._render_digest(latest)
            path = Path(self._memory_cfg.digest_filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown, encoding="utf-8")
            logger.info("Digest regenerado: %s (%d recuerdos)", path, len(latest))
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.error("No se pudo regenerar el digest: %s", exc)

    def _parse_facts(self, raw: str) -> list[dict]:
        """Extrae y valida el JSON de recuerdos del LLM."""
        raw = raw.strip()

        # El LLM a veces envuelve en markdown ```json ... ```
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConsolidationError(
                f"El LLM no devolvió JSON válido. Respuesta: {raw[:200]}"
            ) from exc

        if not isinstance(data, list):
            raise ConsolidationError(f"Se esperaba una lista JSON, recibido: {type(data).__name__}")

        validated = []
        for item in data:
            if not isinstance(item, dict) or "content" not in item:
                continue
            validated.append(item)

        return validated
