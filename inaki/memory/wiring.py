"""Wiring del módulo memory: config → adapters, use cases y tools de memoria.

Único fichero del módulo con permiso para importar ``inaki.config`` (declarado
en su contrato de ``import-linter``): acá se traduce el schema user-facing a lo
que cada pieza del módulo consume. El composition root llama a estas funciones
y no sabe cómo se arma un repo de memoria ni cuándo un LLM de memorias se
comparte con el del agente.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from inaki.config import AgentConfig, MemoriesConfig
from inaki.kernel.domain.agent_settings import (
    ConsolidationSettings,
    MemorySettings,
    ReconciliationSettings,
)
from inaki.kernel.ports.embedding_port import IEmbeddingProvider
from inaki.kernel.ports.history_port import IHistoryStore
from inaki.kernel.ports.llm_port import ILLMProvider
from inaki.kernel.ports.memory_port import IMemoryRepository
from inaki.kernel.ports.tool_port import ITool
from inaki.kernel.run_agent_one_shot import RunAgentOneShotUseCase
from inaki.llm.wiring import LLMProviderFactory
from inaki.memory.adapters.sqlite_history_store import HistoryStoreSettings, SQLiteHistoryStore
from inaki.memory.adapters.sqlite_memory_repo import SQLiteMemoryRepository
from inaki.memory.tools.memory_tools import DeleteMemoryTool, SearchMemoryTool, UpdateMemoryTool
from inaki.memory.tools.search_history_tool import SearchHistoryTool
from inaki.memory.use_cases.consolidate_all_agents import ConsolidateAllAgentsUseCase
from inaki.memory.use_cases.consolidate_memory import ConsolidateMemoryUseCase
from inaki.memory.use_cases.reconcile_memory import ReconcileMemoryUseCase

logger = logging.getLogger(__name__)


def build_memory_settings(memories_cfg: MemoriesConfig) -> MemorySettings:
    """Mapea el bloque ``memories`` → VO del kernel (lo consume el turno y la consolidación)."""
    cons = memories_cfg.consolidation
    rec = memories_cfg.reconciliation
    return MemorySettings(
        digest_template=memories_cfg.digest_filename,
        digest_size=memories_cfg.digest_size,
        consolidation=ConsolidationSettings(
            min_relevance_score=cons.min_relevance_score,
            keep_last_messages=cons.keep_last_messages,
            channels_infused=(tuple(cons.channels_infused) if cons.channels_infused else None),
        ),
        reconciliation=ReconciliationSettings(
            similarity_threshold=rec.similarity_threshold,
            top_k=rec.top_k,
        ),
    )


def build_memory_repo(cfg: AgentConfig, embedder: IEmbeddingProvider) -> IMemoryRepository:
    return SQLiteMemoryRepository(cfg.memories.db_filename, embedder)


def build_history_store(cfg: AgentConfig) -> SQLiteHistoryStore:
    return SQLiteHistoryStore(
        HistoryStoreSettings(
            db_filename=cfg.chat_history.db_filename,
            max_messages=cfg.chat_history.max_messages,
        )
    )


def resolver_llm_de_memorias(cfg: AgentConfig, base_llm: ILLMProvider) -> ILLMProvider:
    """El ``ILLMProvider`` COMPARTIDO por consolidación y reconciliación (``memories.llm``).

    Si ``memories.llm`` no existe o su config efectiva es idéntica a ``cfg.llm``,
    REUSA ``base_llm`` (no duplica clientes HTTP). Si difiere, instancia un
    provider nuevo desde el ``ResolvedLLMConfig`` compuesto contra el registry.
    La customización POR JOB no pasa por acá: la aporta el sub-agente de cada
    sección (``consolidation.agent_id`` / ``reconciliation.agent_id``) vía
    :func:`wire_sub_agentes_de_memoria`. Puede lanzar ``ConfigError`` si el
    provider del override requiere creds y no existe entrada en el registry.
    """
    merged = cfg.memories.merged_llm_config(cfg.llm)
    if merged == cfg.llm:
        return base_llm
    resolved = LLMProviderFactory.resolve(merged, cfg.providers)
    logger.info(
        "Agente '%s': LLM de memoria dedicado (compartido por ambos jobs) — "
        "provider=%s, model=%s, reasoning_effort=%s, max_tokens=%d",
        cfg.id,
        resolved.provider,
        resolved.model,
        resolved.reasoning_effort,
        resolved.max_tokens,
    )
    return LLMProviderFactory.create_from_resolved(resolved)


@dataclass(frozen=True)
class MemoryJobs:
    """Los dos jobs de memoria de un agente; ``None`` el que su config no habilita."""

    consolidate: ConsolidateMemoryUseCase | None
    reconcile: ReconcileMemoryUseCase | None


def build_memory_jobs(
    cfg: AgentConfig,
    *,
    base_llm: ILLMProvider,
    memory: IMemoryRepository,
    embedder: IEmbeddingProvider,
    history: IHistoryStore,
) -> MemoryJobs:
    cons_enabled = cfg.memories.consolidation.enabled
    rec_enabled = cfg.memories.reconciliation.enabled
    if not (cons_enabled or rec_enabled):
        return MemoryJobs(consolidate=None, reconcile=None)
    llm = resolver_llm_de_memorias(cfg, base_llm)
    settings = build_memory_settings(cfg.memories)
    consolidate = (
        ConsolidateMemoryUseCase(
            llm=llm,
            memory=memory,
            embedder=embedder,
            history=history,
            agent_id=cfg.id,
            memory_config=settings,
            delay_seconds=cfg.memories.consolidation.delay_seconds,
        )
        if cons_enabled
        else None
    )
    reconcile = (
        ReconcileMemoryUseCase(
            llm=llm,
            memory=memory,
            embedder=embedder,
            agent_id=cfg.id,
            memory_config=settings,
        )
        if rec_enabled
        else None
    )
    return MemoryJobs(consolidate=consolidate, reconcile=reconcile)


def build_memory_tools(
    *,
    memory: IMemoryRepository,
    embedder: IEmbeddingProvider,
    history: IHistoryStore,
    agent_id: str,
) -> list[ITool]:
    """Gestión directa de recuerdos (buscar/borrar/editar, sin filtro de scope) y
    búsqueda en el historial CRUDO scopeada al ``agent_id``."""
    return [
        SearchMemoryTool(memory=memory, embedder=embedder),
        DeleteMemoryTool(memory=memory),
        UpdateMemoryTool(memory=memory, embedder=embedder),
        SearchHistoryTool(history=history, agent_id=agent_id),
    ]


def build_consolidate_all(
    consolidators: Mapping[str, ConsolidateMemoryUseCase], *, delay_seconds: int
) -> ConsolidateAllAgentsUseCase:
    return ConsolidateAllAgentsUseCase(
        enabled_agents=dict(consolidators), delay_seconds=delay_seconds
    )


@dataclass(frozen=True)
class SubAgenteDeMemoria:
    """Lo que un job de memoria necesita de un sub-agente resuelto: su one-shot y su prompt."""

    one_shot: RunAgentOneShotUseCase
    system_prompt: str


def wire_sub_agentes_de_memoria(
    agent_id: str,
    jobs: MemoryJobs,
    memories_cfg: MemoriesConfig,
    *,
    agentes: Mapping[str, SubAgenteDeMemoria],
    es_sub_agente: Callable[[str], bool],
    max_iterations: int,
    timeout_seconds: int,
) -> None:
    """Conecta el extractor (consolidación) y el reconciliador (reconciliación) sub-agente.

    Cada sección puede nombrar un ``agent_id``; si no existe o no es un
    sub-agente se loguea ``ERROR`` y el job sigue con su prompt por defecto
    (graceful: la memoria no se apaga por un id mal escrito).
    """
    if jobs.consolidate is not None:
        sub = _resolver_sub_agente(
            agent_id,
            memories_cfg.consolidation.agent_id,
            "consolidation",
            "consolidación usará el prompt extractor por defecto",
            agentes=agentes,
            es_sub_agente=es_sub_agente,
        )
        if sub is not None:
            jobs.consolidate.set_extractor(
                sub.one_shot,
                system_prompt_override=sub.system_prompt if sub.system_prompt.strip() else None,
                max_iterations=max_iterations,
                timeout_seconds=timeout_seconds,
            )
            logger.info(
                "Agente '%s': memory extractor wired → sub-agente '%s'",
                agent_id,
                memories_cfg.consolidation.agent_id,
            )
    if jobs.reconcile is not None:
        sub = _resolver_sub_agente(
            agent_id,
            memories_cfg.reconciliation.agent_id,
            "reconciliation",
            "reconciliación usará el prompt hardcodeado + LLM del agente",
            agentes=agentes,
            es_sub_agente=es_sub_agente,
        )
        if sub is not None:
            jobs.reconcile.set_reconciler(
                sub.one_shot,
                system_prompt_override=sub.system_prompt if sub.system_prompt.strip() else None,
                max_iterations=max_iterations,
                timeout_seconds=timeout_seconds,
            )
            logger.info(
                "Agente '%s': memory reconciler wired → sub-agente '%s'",
                agent_id,
                memories_cfg.reconciliation.agent_id,
            )


def _resolver_sub_agente(
    agent_id: str,
    target_id: str | None,
    seccion: str,
    consecuencia: str,
    *,
    agentes: Mapping[str, SubAgenteDeMemoria],
    es_sub_agente: Callable[[str], bool],
) -> SubAgenteDeMemoria | None:
    if not target_id:
        return None
    sub = agentes.get(target_id)
    if sub is None:
        logger.error(
            "Agente '%s': memories.%s.agent_id='%s' no existe — %s",
            agent_id,
            seccion,
            target_id,
            consecuencia,
        )
        return None
    if not es_sub_agente(target_id):
        logger.error(
            "Agente '%s': memories.%s.agent_id='%s' debe apuntar a un sub-agente "
            "(en agents/sub-agents/), no a un agente regular — %s",
            agent_id,
            seccion,
            target_id,
            consecuencia,
        )
        return None
    return sub
