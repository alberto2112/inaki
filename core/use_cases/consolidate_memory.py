"""
ConsolidateMemoryUseCase — extrae recuerdos del historial y lo trunca.

Flujo:
  1. Cargar mensajes pendientes de procesamiento (`infused = 0`)
  2. Si no hay pendientes → no-op idempotente (sin trim, sin nada)
  3. AGRUPAR mensajes por ``(channel, chat_id)`` — cada grupo es una
     conversación distinta. Sin esto, el LLM extractor mezclaría
     contextos no relacionados (p. ej. dos grupos de Telegram distintos
     manejados por el mismo agente).
  4. Por cada grupo:
       a. Enviar los mensajes del grupo al LLM con prompt extractor
       b. Filtrar hechos por min_relevance_score
       c. Para cada hecho: generar embedding + construir MemoryEntry
          (con ``channel``/``chat_id`` del grupo) + persistir
       d. Regenerar digest markdown del scope ``(channel, chat_id)``
       e. Esperar ``delay_seconds`` antes del siguiente grupo
          (excepto el último) para no saturar el LLM remoto.
  5. Marcar TODOS los mensajes del agente como `infused = 1` después
     de procesar todos los grupos.
  6. `history.trim(agent_id, keep_last=resolved_keep_last)`.

Si falla la extracción/persistencia de UN grupo, abortamos: NO marcamos
infused, NO truncamos, historial intacto. Los recuerdos de los grupos
anteriores ya quedaron en el storage vectorial — son inocuos: el próximo
intento volverá a ver los mismos mensajes uninfused y los reextraerá.
Para evitar duplicados a largo plazo, los embeddings idénticos (mismo
``content``) se reemplazan por id en el adaptador SQLite.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone

from core.domain.entities.memory import MemoryEntry
from core.domain.entities.message import Message, Role
from core.domain.errors import ConsolidationError
from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.history_port import IHistoryStore
from core.ports.outbound.llm_port import ILLMProvider
from core.ports.outbound.memory_port import IMemoryRepository
from core.use_cases.run_agent_one_shot import RunAgentOneShotUseCase
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
        delay_seconds: int = 0,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._embedder = embedder
        self._history = history
        self._agent_id = agent_id
        self._memory_cfg = memory_config
        # Espera entre extracciones de scopes ``(channel, chat_id)`` distintos
        # dentro del mismo agente. Misma intención que el delay entre agentes
        # del ``ConsolidateAllAgentsUseCase``: respetar rate limits del LLM remoto.
        self._delay_seconds = max(0, int(delay_seconds))

        # Extractor sub-agente — wired post-construcción por AppContainer Phase 6
        # cuando memory.llm.agent_id apunta a un sub-agente válido. Si está
        # seteado, execute() delega la extracción al sub-agente vía one-shot.
        # Si es None, usa el prompt hardcodeado + self._llm como antes.
        self._extractor_one_shot: RunAgentOneShotUseCase | None = None
        self._extractor_max_iterations: int = 5
        self._extractor_timeout_seconds: int = 180

    def set_extractor(
        self,
        one_shot: RunAgentOneShotUseCase,
        *,
        max_iterations: int = 5,
        timeout_seconds: int = 180,
    ) -> None:
        """Configura un sub-agente extractor. Reemplaza el prompt hardcodeado."""
        self._extractor_one_shot = one_shot
        self._extractor_max_iterations = max_iterations
        self._extractor_timeout_seconds = timeout_seconds

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

        # 2. Agrupar por (channel, chat_id) preservando orden de aparición.
        # OrderedDict garantiza determinismo: el primer scope que aparezca en
        # el historial se procesa primero (útil para tests y logs reproducibles).
        groups: "OrderedDict[tuple[str | None, str | None], list[Message]]" = OrderedDict()
        for msg in messages:
            if msg.role not in (Role.USER, Role.ASSISTANT):
                continue
            key = (msg.channel, msg.chat_id)
            groups.setdefault(key, []).append(msg)

        if not groups:
            return "No hay mensajes user/assistant para consolidar."

        logger.info(
            "Consolidación '%s': %d mensaje(s) repartidos en %d scope(s) (channel, chat_id)",
            self._agent_id,
            len(messages),
            len(groups),
        )

        # 3. Procesar cada grupo. Si UN grupo falla, abortamos sin marcar
        # infused ni truncar — el caller ve el error y los grupos previos ya
        # tienen sus recuerdos en la DB (idempotencia por id en SQLite).
        total_stored = 0
        scope_keys = list(groups.keys())
        for idx, scope in enumerate(scope_keys):
            channel, chat_id = scope
            stored = await self._consolidate_scope(channel, chat_id, groups[scope])
            total_stored += stored

            # Delay entre scopes (no antes del primero, no después del último).
            is_last = idx == len(scope_keys) - 1
            if not is_last and self._delay_seconds > 0:
                logger.debug(
                    "Consolidación '%s': esperando %ds antes del siguiente scope",
                    self._agent_id,
                    self._delay_seconds,
                )
                await asyncio.sleep(self._delay_seconds)

        # 4. Marcar todos los mensajes del agente como infused ANTES del trim.
        # Esto es el gate que evita que la próxima corrida reprocese mensajes
        # que sigan vivos en el buffer (por el keep_last del trim). Si falla,
        # abortamos — los recuerdos ya están persistidos en inaki.db pero el
        # error queda visible y se puede reintentar.
        try:
            await self._history.mark_infused(self._agent_id)
        except Exception as exc:
            raise ConsolidationError(f"Error marcando mensajes como infused: {exc}") from exc

        # 5. Truncar historial (solo si llegamos hasta aquí sin errores).
        keep_last = self._memory_cfg.resolved_keep_last_messages()
        try:
            await self._history.trim(self._agent_id, keep_last=keep_last)
        except Exception as exc:
            raise ConsolidationError(f"Error truncando historial: {exc}") from exc

        logger.info(
            "Consolidación completada para '%s': %d recuerdo(s) extraído(s) "
            "en %d scope(s), últimos %d mensaje(s) preservados",
            self._agent_id,
            total_stored,
            len(groups),
            keep_last,
        )
        return (
            f"✓ {total_stored} recuerdo(s) extraído(s) en {len(groups)} scope(s). "
            f"Historial truncado (últimos {keep_last} mensajes preservados)."
        )

    async def _consolidate_scope(
        self,
        channel: str | None,
        chat_id: str | None,
        scope_messages: list[Message],
    ) -> int:
        """
        Extrae y persiste recuerdos para UN scope ``(channel, chat_id)``.

        Devuelve la cantidad de recuerdos persistidos. Lanza
        ``ConsolidationError`` ante cualquier fallo del LLM o de la
        persistencia — el caller decide si propagar o continuar.
        """

        def _fmt(m: Message) -> str:
            if m.timestamp is not None:
                ts = m.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
                return f"{m.role.value} [{ts}]: {m.content}"
            return f"{m.role.value}: {m.content}"

        history_text = "\n".join(_fmt(m) for m in scope_messages)

        # Extracción: dos caminos.
        #  a) Sub-agente extractor configurado → delegar via one-shot. El
        #     sub-agente tiene su propio system_prompt (con instrucciones de
        #     extracción) y recibe el historial como user task.
        #  b) Default → prompt hardcodeado + LLM directo (comportamiento legacy).
        try:
            if self._extractor_one_shot is not None:
                raw_json = await self._extractor_one_shot.execute(
                    task=history_text,
                    system_prompt=None,  # usar el system_prompt del sub-agente
                    max_iterations=self._extractor_max_iterations,
                    timeout_seconds=self._extractor_timeout_seconds,
                )
            else:
                prompt = _EXTRACTOR_PROMPT_TEMPLATE.format(history=history_text)
                response = await self._llm.complete(
                    messages=[],
                    system_prompt=prompt,
                )
                raw_json = response.text
        except Exception as exc:
            raise ConsolidationError(
                f"El LLM falló durante la extracción "
                f"(channel={channel!r}, chat_id={chat_id!r}): {exc}"
            ) from exc

        logger.debug(
            "Extractor scope=(%r, %r) — respuesta raw (primeros 500 chars): %s",
            channel,
            chat_id,
            raw_json[:500],
        )

        try:
            facts = self._parse_facts(raw_json)
        except ConsolidationError:
            raise
        except Exception as exc:
            raise ConsolidationError(
                f"Error parseando respuesta del LLM "
                f"(channel={channel!r}, chat_id={chat_id!r}): {exc}"
            ) from exc

        if not facts:
            logger.info(
                "Scope (%r, %r): el LLM no encontró recuerdos relevantes",
                channel,
                chat_id,
            )

        # Filtro por relevance mínimo (ahorra tokens de embedding).
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
                "Scope (%r, %r): %d recuerdo(s) descartado(s) por relevance < %.2f",
                channel,
                chat_id,
                dropped,
                threshold,
            )
        facts = filtered

        # Embeddings + persistencia.
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
                    agent_id=self._agent_id,
                    channel=channel,
                    chat_id=chat_id,
                    created_at=created_at or datetime.now(timezone.utc),
                )
                await self._memory.store(entry)
                stored += 1
            except Exception as exc:
                raise ConsolidationError(
                    f"Error persistiendo recuerdo '{fact.get('content', '')}' "
                    f"(channel={channel!r}, chat_id={chat_id!r}): {exc}"
                ) from exc

        # Regenerar el digest del scope (best-effort, no aborta).
        await self._write_digest(channel, chat_id)

        return stored

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

    async def _write_digest(self, channel: str | None, chat_id: str | None) -> None:
        """
        Regenera el digest markdown del scope ``(channel, chat_id)``.
        Nunca propaga excepciones — un fallo no aborta la consolidación.
        """
        try:
            latest = await self._memory.get_recent(
                self._memory_cfg.digest_size,
                agent_id=self._agent_id,
                channel=channel,
                chat_id=chat_id,
            )
            markdown = self._render_digest(latest)
            path = self._memory_cfg.resolved_digest_path(channel, chat_id)
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
