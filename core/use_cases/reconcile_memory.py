"""
ReconcileMemoryUseCase — protocolo de reconciliación de memoria («reflection»).

Objetivo: reconsiderar recuerdos viejos que un recuerdo nuevo vuelve obsoletos
o contradictorios.

Ejemplo canónico: el usuario dijo "estoy enfermo, tomo tratamiento X" (recuerdo
viejo); días después "ya me recuperé, dejé el tratamiento" (recuerdo nuevo).
El protocolo los fusiona en un recuerdo actualizado que preserva la línea
temporal, soft-deleteando los viejos.

Modo: BALANCEADA + SEED-BASED.

Flujo:
  1. Cargar todos los recuerdos no reconciliados del agente (todos los scopes).
  2. Para cada seed (en orden cronológico):
       a. Si el seed ya fue procesado en esta corrida → saltar.
       b. Buscar vecinos por similitud coseno; filtrar al MISMO scope del seed.
       c. Si no hay vecinos sobre el umbral → marcar seed como reconciliado y seguir.
       d. Enviar seed + cluster al LLM para decidir qué hacer.
       e. Aplicar acciones: merge, supersede, downweight, keep.
       f. Marcar como reconciliados todos los ids del cluster que no fueron borrados.
  3. Devolver resumen con contadores.

Limitación V1: ``search_with_scores`` NO filtra por scope nativamente — el filtro
por (channel, chat_id) se aplica en este use case luego del resultado de la búsqueda.
Se usa un ``reconcile_top_k`` generoso para compensar. No se modifica el puerto de
búsqueda — esto quedó documentado para V2 si la performance de la búsqueda importa.

Anti-loop: los MemoryEntry nuevos creados por la acción «merge» se persisten con
``reconciled=True``. Así no re-entran al ciclo como seeds en la próxima corrida.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from core.domain.entities.memory import MemoryEntry
from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.llm_port import ILLMProvider
from core.ports.outbound.memory_port import IMemoryRepository
from core.use_cases._json_extract import extract_json_array
from core.use_cases.run_agent_one_shot import RunAgentOneShotUseCase
from core.domain.value_objects.agent_settings import MemorySettings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt hardcodeado del reconciliador (en inglés — el LLM comprende mejor;
# routing_keywords no aplica acá porque no es una tool del LLM).
# ---------------------------------------------------------------------------

_RECONCILER_PROMPT = """\
## Role

You are a long-term memory reconciler for a personal AI assistant.
You receive a cluster of related memories (with ids and timestamps) and must
decide how to reconcile them: merge contradictions, supersede outdated facts,
downweight uncertain ones, or keep them as-is.

## Ephemeral vs Persistent facts

Before deciding, classify each memory:

**EPHEMERAL** (time-bounded, relevant only while the event is active):
- Current or in-progress location ("user is at X", "heading to Y", "on the metro")
- Active short-term health state ("has a cold", "is feverish", "started treatment X")
- Ongoing activity ("watching the game", "cooking dinner", "traveling to Z today")
- Scheduled near-term event ("meeting tomorrow", "doctor appointment next week")

**PERSISTENT** (indefinitely relevant):
- Identity, name, relationships ("user's son is called X")
- Residence or regular home location ("lives in Valencia")
- Stable preferences, beliefs, skills
- Historical facts with lasting significance ("had surgery in 2024")

**Rule**: If a memory is EPHEMERAL and its `created_at` is more than ~48 hours old,
the event it describes has almost certainly already passed. Use `supersede` to remove it —
there is no value in remembering where someone was on the metro last month.
Do NOT use `keep` or `downweight` for stale ephemeral memories. Supersede them.

## Guidelines

- BALANCED mode: merge obvious contradictions into a single updated memory that
  PRESERVES the timeline (e.g. "Had flu in March treated with X; recovered in
  April, no longer on treatment"). When in DOUBT about persistent facts, prefer
  downweight over delete.
- Stale ephemeral facts: use `supersede` — no doubt needed, they are simply expired.
- If there is no conflict, redundancy, or staleness, return keep.
- NEVER invent facts not present in the original memories.
- Reference memories by their exact `id` field in `target_ids`.

## Actions schema

Return ONLY a raw JSON array (no markdown, no preamble, no explanation).
Each element is one of:

[
  {
    "action": "merge",
    "target_ids": ["id1", "id2", ...],
    "new_content": "updated content preserving the full timeline",
    "new_relevance": 0.0-1.0,
    "new_tags": ["tag1", "tag2"]
  },
  {
    "action": "supersede",
    "target_ids": ["id_of_outdated_memory"]
  },
  {
    "action": "downweight",
    "target_ids": ["id_of_uncertain_memory"],
    "new_relevance": 0.1
  },
  {
    "action": "keep"
  }
]

- `target_ids` must reference exact ids from the provided memory cluster.
- Use `merge` when memories contradict each other and a unified timeline makes sense.
- Use `supersede` when a memory is fully overridden by a newer one (no nuance to preserve).
- Use `downweight` when a memory might be outdated but you're not sure — lower its relevance.
- Use `keep` when no action is needed. This is ALWAYS valid.
- If there is nothing to reconcile, return exactly: []
"""


class ReconcileMemoryUseCase:
    """Reconcilia recuerdos no reconciliados de un agente (protocolo «reflection»).

    El constructor recibe los mismos puertos que ConsolidateMemoryUseCase (minus
    history, que no aplica acá). El sub-agente reconciliador se wirea post-
    construcción via ``set_reconciler`` (análogo a ``set_extractor`` en consolidación).
    """

    def __init__(
        self,
        llm: ILLMProvider,
        memory: IMemoryRepository,
        embedder: IEmbeddingProvider,
        agent_id: str,
        memory_config: MemorySettings,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._embedder = embedder
        self._agent_id = agent_id
        self._memory_cfg = memory_config

        # Reconciliador sub-agente — wired post-construcción por AppContainer
        # cuando memory.reconcile_llm.agent_id apunta a un sub-agente válido.
        # Si es None, usa el prompt hardcodeado + self._llm directamente.
        self._reconciler_one_shot: RunAgentOneShotUseCase | None = None
        self._reconciler_max_iterations: int = 5
        self._reconciler_timeout_seconds: int = 180

    def set_reconciler(
        self,
        one_shot: RunAgentOneShotUseCase,
        *,
        max_iterations: int = 5,
        timeout_seconds: int = 180,
    ) -> None:
        """Configura un sub-agente reconciliador. Reemplaza el prompt hardcodeado."""
        self._reconciler_one_shot = one_shot
        self._reconciler_max_iterations = max_iterations
        self._reconciler_timeout_seconds = timeout_seconds

    async def execute(self) -> str:
        """
        Ejecuta el protocolo de reconciliación completo para el agente.

        Retorna un mensaje descriptivo del resultado.
        Best-effort por cluster: un cluster que falla no aborta el resto.
        """
        seeds = await self._memory.load_unreconciled(self._agent_id)
        if not seeds:
            return "No hay recuerdos nuevos para reconciliar."

        procesados: set[str] = set()  # ids borrados o reconciliados en esta corrida
        total_merges = 0
        total_supersedes = 0
        total_downweights = 0
        clusters_procesados = 0

        threshold = self._memory_cfg.reconciliation.similarity_threshold
        top_k = self._memory_cfg.reconciliation.top_k

        for seed in seeds:
            if seed.id in procesados:
                continue

            # Vecinos por similitud coseno — search_with_scores no filtra por
            # scope (limitación V1 documentada en el módulo). Filtramos manualmente.
            vecinos_scored = await self._memory.search_with_scores(seed.embedding, top_k=top_k)
            cluster = [
                m
                for m, score in vecinos_scored
                if score >= threshold
                and m.id != seed.id
                and m.id not in procesados
                and m.channel == seed.channel
                and m.chat_id == seed.chat_id
            ]

            if not cluster:
                # Sin vecinos → marcar seed reconciliado y seguir
                await self._memory.mark_reconciled([seed.id])
                procesados.add(seed.id)
                continue

            # Invocar al LLM / sub-agente para este cluster
            try:
                decisiones = await self._reconcile_cluster(seed, cluster)
            except Exception as exc:  # noqa: BLE001 — best-effort por cluster
                logger.warning(
                    "ReconcileMemoryUseCase: error en cluster seed=%r: %s — se saltea",
                    seed.id,
                    exc,
                )
                # Marcar seed reconciliado para evitar re-proceso indefinido
                await self._memory.mark_reconciled([seed.id])
                procesados.add(seed.id)
                continue

            # Aplicar acciones y acumular contadores
            merges, supersedes, downweights, ids_borrados = await self._aplicar(
                decisiones, seed, cluster
            )
            total_merges += merges
            total_supersedes += supersedes
            total_downweights += downweights
            clusters_procesados += 1

            # Marcar reconciliados todos los ids del cluster + seed que NO fueron borrados
            ids_cluster = [seed.id] + [m.id for m in cluster]
            ids_a_reconciliar = [i for i in ids_cluster if i not in ids_borrados]
            if ids_a_reconciliar:
                await self._memory.mark_reconciled(ids_a_reconciliar)

            procesados.update(ids_cluster)

        logger.info(
            "Reconciliación completada para '%s': %d cluster(s) procesado(s), "
            "%d merge(s), %d supersede(s), %d downweight(s)",
            self._agent_id,
            clusters_procesados,
            total_merges,
            total_supersedes,
            total_downweights,
        )

        return (
            f"✓ Reconciliación completada: {clusters_procesados} cluster(s) procesado(s). "
            f"Merges: {total_merges}, supersedes: {total_supersedes}, "
            f"downweights: {total_downweights}."
        )

    async def _reconcile_cluster(self, seed: MemoryEntry, cluster: list[MemoryEntry]) -> list[dict]:
        """
        Envía seed + cluster al LLM y devuelve la lista de acciones parseadas.

        Devuelve lista vacía si el LLM no devuelve JSON válido (best-effort).
        """
        # Formatear el cluster para el LLM: seed primero, luego el resto
        todas = [seed] + cluster
        entradas = []
        for m in todas:
            ts = (m.created_at or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
            entradas.append(
                f"id: {m.id}\ncreated_at: {ts}\nrelevance: {m.relevance:.2f}\ncontent: {m.content}"
            )
        cluster_text = "\n\n---\n\n".join(entradas)
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        task_text = f"Current date/time (UTC): {now_ts}\n\nReconcile the following memory cluster:\n\n{cluster_text}"

        # Dos caminos: sub-agente one-shot o LLM directo con prompt hardcodeado
        if self._reconciler_one_shot is not None:
            raw_json = await self._reconciler_one_shot.execute(
                task=task_text,
                system_prompt=None,  # usar el system_prompt del sub-agente
                max_iterations=self._reconciler_max_iterations,
                timeout_seconds=self._reconciler_timeout_seconds,
            )
        else:
            from core.domain.entities.message import Message, Role  # import local evita ciclo

            response = await self._llm.complete(
                messages=[Message(role=Role.USER, content=task_text)],
                system_prompt=_RECONCILER_PROMPT,
            )
            raw_json = response.text

        logger.debug(
            "Reconciliador seed=%r — respuesta raw (primeros 500 chars): %s",
            seed.id,
            raw_json[:500],
        )

        return self._parsear_acciones(raw_json, seed.id)

    def _parsear_acciones(self, raw: str, seed_id: str) -> list[dict]:
        """Parsea el JSON de acciones del LLM. Devuelve [] si no es válido."""
        raw = raw.strip()

        # Quitar fences markdown si el modelo las usa
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
            raw = raw.strip()

        data = None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            candidate = extract_json_array(raw)
            if candidate is not None:
                try:
                    data = json.loads(candidate)
                except json.JSONDecodeError:
                    pass

        if data is None:
            logger.warning(
                "ReconcileMemoryUseCase: seed=%r — el LLM no devolvió JSON válido: %s",
                seed_id,
                raw[:300],
            )
            return []

        if not isinstance(data, list):
            logger.warning(
                "ReconcileMemoryUseCase: seed=%r — se esperaba lista JSON, recibido: %s",
                seed_id,
                type(data).__name__,
            )
            return []

        # Validar que cada item sea dict con acción reconocida
        acciones_validas = {"merge", "supersede", "downweight", "keep"}
        validadas = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if item.get("action") not in acciones_validas:
                logger.warning(
                    "ReconcileMemoryUseCase: seed=%r — acción desconocida ignorada: %r",
                    seed_id,
                    item.get("action"),
                )
                continue
            validadas.append(item)

        return validadas

    async def _aplicar(
        self,
        decisiones: list[dict],
        seed: MemoryEntry,
        cluster: list[MemoryEntry],
    ) -> tuple[int, int, int, set[str]]:
        """
        Aplica las acciones del LLM sobre la DB.

        Retorna (merges, supersedes, downweights, ids_borrados).
        Best-effort por acción: un error no aborta las demás.
        """
        merges = 0
        supersedes = 0
        downweights = 0
        ids_borrados: set[str] = set()

        for accion in decisiones:
            tipo = accion.get("action")

            try:
                if tipo == "merge":
                    new_content = accion.get("new_content", "")
                    new_relevance = float(accion.get("new_relevance", 0.5))
                    new_tags = accion.get("new_tags", [])
                    target_ids: list[str] = accion.get("target_ids", [])

                    if not new_content:
                        logger.warning(
                            "ReconcileMemoryUseCase: acción merge sin new_content — ignorada"
                        )
                        continue

                    embedding = await self._embedder.embed_passage(new_content)
                    nuevo = MemoryEntry(
                        content=new_content,
                        embedding=embedding,
                        relevance=new_relevance,
                        tags=new_tags if isinstance(new_tags, list) else [],
                        agent_id=self._agent_id,
                        channel=seed.channel,
                        chat_id=seed.chat_id,
                        created_at=datetime.now(timezone.utc),
                        reconciled=True,  # anti-loop: no re-entra en próxima corrida
                    )
                    await self._memory.store(nuevo)

                    for mid in target_ids:
                        result = await self._memory.delete(mid)
                        if result is not None:
                            ids_borrados.add(mid)

                    merges += 1

                elif tipo == "supersede":
                    target_ids = accion.get("target_ids", [])
                    for mid in target_ids:
                        result = await self._memory.delete(mid)
                        if result is not None:
                            ids_borrados.add(mid)
                    supersedes += 1

                elif tipo == "downweight":
                    target_ids = accion.get("target_ids", [])
                    new_relevance = float(accion.get("new_relevance", 0.1))
                    for mid in target_ids:
                        await self._memory.update(mid, relevance=new_relevance)
                    downweights += 1

                elif tipo == "keep":
                    pass  # no-op explícito

            except Exception as exc:  # noqa: BLE001 — best-effort por acción
                logger.warning(
                    "ReconcileMemoryUseCase: error aplicando acción %r: %s — se continúa",
                    tipo,
                    exc,
                )

        return merges, supersedes, downweights, ids_borrados
