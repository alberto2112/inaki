"""``inaki.knowledge.wiring``: fuentes en orden, una lista viva, tools armadas."""

from __future__ import annotations

from unittest.mock import MagicMock

from inaki.config import AgentConfig, GlobalConfig
from inaki.knowledge.adapters.sqlite_memory_knowledge_source import SqliteMemoryKnowledgeSource
from inaki.knowledge.tools.knowledge_admin_tool import KnowledgeAdminTool
from inaki.knowledge.tools.knowledge_search_tool import KnowledgeSearchTool
from inaki.knowledge.wiring import build_knowledge, build_knowledge_tools

_AGENTE = {
    "id": "dev",
    "name": "Dev",
    "description": "agente de prueba",
    "llm": {},
    "embedding": {},
    "memories": {"db_filename": ":memory:"},
    "chat_history": {},
}


def _global(**knowledge: object) -> GlobalConfig:
    base: dict[str, object] = {
        "app": {},
        "llm": {},
        "embedding": {},
        "chat_history": {},
        "memories": {"db_filename": ":memory:"},
    }
    if knowledge:
        base["knowledge"] = knowledge
    return GlobalConfig.model_validate(base)


def _agente() -> AgentConfig:
    return AgentConfig.model_validate(_AGENTE)


def test_la_memoria_es_la_primera_fuente_y_los_parametros_salen_de_la_config() -> None:
    bundle = build_knowledge(
        _global(max_total_chunks=7, top_k_per_source=2, min_score=0.9),
        _agente(),
        memory=MagicMock(),
        embedder=MagicMock(),
    )

    assert isinstance(bundle.sources[0], SqliteMemoryKnowledgeSource)
    assert bundle.orchestrator._cap == 7
    assert bundle.orchestrator._default_top_k_per_source == 2
    assert bundle.orchestrator._default_min_score == 0.9


def test_sin_include_memory_no_hay_fuente_de_memoria() -> None:
    bundle = build_knowledge(
        _global(include_memory=False),
        _agente(),
        memory=MagicMock(),
        embedder=MagicMock(),
    )

    assert bundle.sources == []


def test_el_orquestador_y_el_use_case_comparten_la_MISMA_lista_de_fuentes() -> None:
    """Las extensiones (nivel 3) se añaden después sobre esa lista sin reconstruir nada."""
    bundle = build_knowledge(_global(), _agente(), memory=MagicMock(), embedder=MagicMock())
    nueva = MagicMock(source_id="ext")

    bundle.sources.append(nueva)

    assert "ext" in bundle.orchestrator.source_ids
    assert bundle.manage._sources is bundle.sources


def test_las_tools_son_busqueda_y_gestion() -> None:
    bundle = build_knowledge(_global(), _agente(), memory=MagicMock(), embedder=MagicMock())

    tools = build_knowledge_tools(bundle, MagicMock())

    assert [type(t) for t in tools] == [KnowledgeSearchTool, KnowledgeAdminTool]
