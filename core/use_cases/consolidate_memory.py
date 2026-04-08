"""
ConsolidateMemoryUseCase — extrae recuerdos del historial y lo archiva.

Flujo:
  1. Cargar historial completo del agente
  2. Enviar historial al LLM con prompt extractor
  3. El LLM devuelve JSON con lista de recuerdos
  4. Para cada recuerdo: generar embedding + construir MemoryEntry + persistir
  5. Si todo OK: archivar historial + clear
  6. Si falla en cualquier punto: NO archivar, historial intacto

El archivado es TRANSACCIONAL: solo ocurre si la extracción y persistencia son exitosas.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from core.domain.entities.memory import MemoryEntry
from core.domain.entities.message import Role
from core.domain.errors import ConsolidationError
from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.history_port import IHistoryStore
from core.ports.outbound.llm_port import ILLMProvider
from core.ports.outbound.memory_port import IMemoryRepository

logger = logging.getLogger(__name__)

_EXTRACTOR_PROMPT_TEMPLATE = """\
Eres un extractor de memoria de un asistente personal.
Analiza la siguiente conversación e identifica hechos, preferencias,
información relevante y contexto importante sobre el usuario.

Devuelve ÚNICAMENTE un JSON válido con el siguiente schema, sin texto adicional:
[
  {{
    "content": "descripción clara del hecho o preferencia",
    "relevance": 0.0-1.0,
    "tags": ["tag1", "tag2"]
  }}
]

Si no hay nada relevante para recordar, devuelve un array vacío: []

Conversación:
{history}
"""


@dataclass
class ConsolidationResult:
    memories_extracted: int
    archive_path: str


class ConsolidateMemoryUseCase:

    def __init__(
        self,
        llm: ILLMProvider,
        memory: IMemoryRepository,
        embedder: IEmbeddingProvider,
        history: IHistoryStore,
        agent_id: str,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._embedder = embedder
        self._history = history
        self._agent_id = agent_id

    async def execute(self) -> str:
        """
        Ejecuta la consolidación completa.
        Retorna un mensaje descriptivo del resultado.
        Lanza ConsolidationError si falla (historial intacto).
        """
        # 1. Cargar historial completo desde disco (ignora la ventana en memoria)
        messages = await self._history.load_full(self._agent_id)
        if not messages:
            return "El historial está vacío — nada que consolidar."

        # 2. Formatear historial para el LLM
        history_text = "\n".join(
            f"{m.role.value}: {m.content}"
            for m in messages
            if m.role in (Role.USER, Role.ASSISTANT)
        )
        prompt = _EXTRACTOR_PROMPT_TEMPLATE.format(history=history_text)

        # 3. Llamar al LLM extractor
        try:
            raw_json = await self._llm.complete(
                messages=[],
                system_prompt=prompt,
            )
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

        # 5. Generar embeddings y persistir
        stored = 0
        for fact in facts:
            try:
                embedding = await self._embedder.embed_passage(fact["content"])
                entry = MemoryEntry(
                    content=fact["content"],
                    embedding=embedding,
                    relevance=float(fact.get("relevance", 0.5)),
                    tags=fact.get("tags", []),
                    agent_id=None,  # global compartido
                )
                await self._memory.store(entry)
                stored += 1
            except Exception as exc:
                raise ConsolidationError(
                    f"Error persistiendo recuerdo '{fact.get('content', '')}': {exc}"
                ) from exc

        # 6. Archivar historial (solo si llegamos hasta aquí sin errores)
        try:
            archive_path = await self._history.archive(self._agent_id)
            await self._history.clear(self._agent_id)
        except Exception as exc:
            raise ConsolidationError(f"Error archivando historial: {exc}") from exc

        logger.info(
            "Consolidación completada: %d recuerdos extraídos, historial archivado en %s",
            stored,
            archive_path,
        )
        return f"✓ {stored} recuerdo(s) extraído(s). Historial archivado en {archive_path}"

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
            raise ConsolidationError(
                f"Se esperaba una lista JSON, recibido: {type(data).__name__}"
            )

        validated = []
        for item in data:
            if not isinstance(item, dict) or "content" not in item:
                continue
            validated.append(item)

        return validated
