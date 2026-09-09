"""Wiring del módulo knowledge (tier harness-global): config → fuentes, orquestador y tools.

Único fichero del módulo con permiso para importar ``inaki.config``. Las fuentes
se recolectan en orden garantizado —(1) memoria, (2) configuradas— sobre UNA
lista que el orquestador y el use case de gestión comparten por referencia: el
composition root añade después las de nivel (3), las extensiones, sin
reconstruir nada.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from inaki.config import AgentConfig, GlobalConfig, KnowledgeSourceConfig
from inaki.kernel.ports.outbound.embedding_port import IEmbeddingProvider
from inaki.kernel.ports.outbound.knowledge_port import IKnowledgeSource
from inaki.kernel.ports.outbound.memory_port import IMemoryRepository
from inaki.kernel.ports.outbound.tool_port import ITool
from inaki.knowledge.adapters.document_knowledge_source import DocumentKnowledgeSource
from inaki.knowledge.adapters.sqlite_knowledge_source import SqliteKnowledgeSource
from inaki.knowledge.adapters.sqlite_memory_knowledge_source import SqliteMemoryKnowledgeSource
from inaki.knowledge.orchestrator import KnowledgeOrchestrator
from inaki.knowledge.tools.knowledge_admin_tool import KnowledgeAdminTool
from inaki.knowledge.tools.knowledge_search_tool import KnowledgeSearchTool
from inaki.knowledge.use_cases.manage_knowledge import ManageKnowledgeUseCase
from inaki.shared.errors import KnowledgeConfigError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KnowledgeBundle:
    """Las fuentes (lista VIVA: compartida por el orquestador y el use case), el
    orquestador que consume el turno y el use case de gestión."""

    sources: list[IKnowledgeSource]
    orchestrator: KnowledgeOrchestrator
    manage: ManageKnowledgeUseCase


def build_knowledge(
    global_cfg: GlobalConfig,
    agent_cfg: AgentConfig,
    *,
    memory: IMemoryRepository,
    embedder: IEmbeddingProvider,
) -> KnowledgeBundle:
    knowledge_cfg = getattr(global_cfg, "knowledge", None)
    include_memory = True
    params: dict[str, Any] = {
        "max_total_chunks": 10,
        "token_budget_threshold": 4000,
        "pre_fetch_enabled": True,
        "default_top_k_per_source": 3,
        "default_min_score": 0.5,
    }
    if knowledge_cfg is not None:
        include_memory = getattr(knowledge_cfg, "include_memory", True)
        params["max_total_chunks"] = getattr(knowledge_cfg, "max_total_chunks", 10)
        params["token_budget_threshold"] = getattr(
            knowledge_cfg, "token_budget_warn_threshold", 4000
        )
        params["pre_fetch_enabled"] = getattr(knowledge_cfg, "enabled", True)
        params["default_top_k_per_source"] = getattr(knowledge_cfg, "top_k_per_source", 3)
        params["default_min_score"] = getattr(knowledge_cfg, "min_score", 0.5)

    fuentes: list[IKnowledgeSource] = []
    if include_memory:
        fuentes.append(SqliteMemoryKnowledgeSource(memory=memory))
        logger.debug("Agente '%s': SqliteMemoryKnowledgeSource registrada", agent_cfg.id)
    if knowledge_cfg is not None:
        for fuente_cfg in getattr(knowledge_cfg, "sources", []) or []:
            if not getattr(fuente_cfg, "enabled", True):
                continue
            tipo = getattr(fuente_cfg, "type", "")
            if tipo == "document":
                fuentes.append(_build_document_source(global_cfg, agent_cfg, fuente_cfg, embedder))
            elif tipo == "sqlite":
                sqlite_source = _build_sqlite_source(agent_cfg.id, fuente_cfg)
                if sqlite_source is not None:
                    fuentes.append(sqlite_source)
            else:
                logger.warning(
                    "Agente '%s': tipo de fuente '%s' no reconocido para '%s' — skipping",
                    agent_cfg.id,
                    tipo,
                    getattr(fuente_cfg, "id", "<sin-id>"),
                )

    orchestrator = KnowledgeOrchestrator(
        sources=fuentes,
        max_total_chunks=params["max_total_chunks"],
        token_budget_threshold=params["token_budget_threshold"],
        pre_fetch_enabled=params["pre_fetch_enabled"],
        default_top_k_per_source=params["default_top_k_per_source"],
        default_min_score=params["default_min_score"],
    )
    return KnowledgeBundle(
        sources=fuentes,
        orchestrator=orchestrator,
        manage=ManageKnowledgeUseCase(sources=fuentes),
    )


def build_knowledge_tools(bundle: KnowledgeBundle, embedder: IEmbeddingProvider) -> list[ITool]:
    """Búsqueda (RAG) y gestión (ingest/reindex/list/stats/delete) expuestas al LLM."""
    return [
        KnowledgeSearchTool(orchestrator=bundle.orchestrator, embedder=embedder),
        KnowledgeAdminTool(manage_knowledge=bundle.manage),
    ]


def _build_document_source(
    global_cfg: GlobalConfig,
    agent_cfg: AgentConfig,
    fuente_cfg: KnowledgeSourceConfig,
    embedder: IEmbeddingProvider,
) -> IKnowledgeSource:
    if fuente_cfg.path is None:
        raise ValueError(
            f"Fuente de conocimiento '{fuente_cfg.id}' (type='document') requiere 'path' configurado."
        )
    return DocumentKnowledgeSource(
        source_id=fuente_cfg.id,
        description=fuente_cfg.description,
        path=fuente_cfg.path,
        embedder=embedder,
        db_dir=global_cfg.knowledge.db_dirname,
        glob=getattr(fuente_cfg, "glob", "**/*.md"),
        chunk_size=getattr(fuente_cfg, "chunk_size", 500),
        chunk_overlap=getattr(fuente_cfg, "chunk_overlap", 80),
        dimension=agent_cfg.embedding.dimension,
    )


def _build_sqlite_source(agent_id: str, fuente_cfg: object) -> IKnowledgeSource | None:
    """``None`` (con ``ERROR``) si la config de la fuente es irrecuperable: el agente
    arranca sin esa fuente en vez de no arrancar."""
    fuente_id = getattr(fuente_cfg, "id", "<sin-id>")
    db_path = getattr(fuente_cfg, "path", None)
    if not db_path:
        logger.error(
            "Agente '%s': fuente sqlite '%s' no tiene 'path' configurado — skipping",
            agent_id,
            fuente_id,
        )
        return None
    try:
        return SqliteKnowledgeSource(
            source_id=fuente_id,
            description=getattr(fuente_cfg, "description", ""),
            db_path=db_path,
        )
    except KnowledgeConfigError as exc:
        logger.error(
            "Agente '%s': error de configuración en fuente sqlite '%s': %s — skipping",
            agent_id,
            fuente_id,
            exc,
        )
        return None
