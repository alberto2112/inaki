"""
Tests para AgentContainer._register_extensions() — soporte de KNOWLEDGE_SOURCES.

Cobertura:
- Manifest con KNOWLEDGE_SOURCES → fuente registrada en el orquestrador.
- Orden de descubrimiento: memoria primero, fuentes configuradas segundo, extensiones tercero.
- Factory que lanza excepción → WARNING emitido, otras fuentes siguen registradas.
- KNOWLEDGE_SOURCES = [] → no-op, sin error.
- Manifest sin atributo KNOWLEDGE_SOURCES → no-op (compatibilidad hacia atrás).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
import pytest

from adapters.outbound.skills.yaml_skill_repo import YamlSkillRepository
from adapters.outbound.tools.tool_registry import ToolRegistry
from core.domain.value_objects.knowledge_chunk import KnowledgeChunk
from core.ports.outbound.knowledge_port import IKnowledgeSource
from infrastructure.container import AgentContainer


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeEmbedder:
    async def embed_passage(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


class FakeMemory:
    async def search(self, *args, **kwargs):
        return []

    async def search_with_scores(self, *args, **kwargs):
        return []

    async def store(self, *args, **kwargs):
        pass

    async def get_recent(self, *args, **kwargs):
        return []

    async def get_all(self, *args, **kwargs):
        return []


class FakeKnowledgeSource(IKnowledgeSource):
    """Fuente de conocimiento mínima para tests."""

    def __init__(self, source_id: str = "ext-source") -> None:
        self._id = source_id

    @property
    def source_id(self) -> str:
        return self._id

    @property
    def description(self) -> str:
        return "Fake ext knowledge source"

    async def search(
        self,
        query_vec: list[float],
        top_k: int,
        min_score: float,
    ) -> list[KnowledgeChunk]:
        return []


class FakeMemoryKnowledgeSource(IKnowledgeSource):
    """Simula la SqliteMemoryKnowledgeSource sin depender de SQLite."""

    @property
    def source_id(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return "Memory source (fake)"

    async def search(
        self,
        query_vec: list[float],
        top_k: int,
        min_score: float,
    ) -> list[KnowledgeChunk]:
        return []


# ---------------------------------------------------------------------------
# Fixture: container mínimo sin __init__ pesado
# ---------------------------------------------------------------------------


def _make_container(tmp_path: Path) -> AgentContainer:
    """
    Crea un AgentContainer con _tools, _skills, _knowledge_orchestrator y
    _pending_knowledge_sources inicializados sin ejecutar __init__ completo.
    """
    from core.domain.services.knowledge_orchestrator import KnowledgeOrchestrator

    container = AgentContainer.__new__(AgentContainer)
    container._tools = ToolRegistry(embedder=FakeEmbedder())
    container._skills = YamlSkillRepository(FakeEmbedder())
    container._embedder = FakeEmbedder()
    container._memory = FakeMemory()

    # Simular un agent_config mínimo
    fake_cfg = types.SimpleNamespace(id="test-agent")
    container.agent_config = fake_cfg

    # Simular global_config mínimo
    fake_global_cfg = types.SimpleNamespace(knowledge=None)
    container._global_config = fake_global_cfg

    # Fuentes de nivel 1+2 pre-cargadas (normalmente se cargan en _register_tools)
    memory_source = FakeMemoryKnowledgeSource()
    container._pending_knowledge_sources = [memory_source]
    container._knowledge_max_chunks = 10
    container._knowledge_token_budget = 4000
    container._knowledge_orchestrator = KnowledgeOrchestrator(
        sources=container._pending_knowledge_sources,
        max_total_chunks=10,
        token_budget_threshold=4000,
    )

    return container


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_manifest(ext_dir: Path, name: str, content: str) -> Path:
    pkg_dir = ext_dir / name
    pkg_dir.mkdir(parents=True, exist_ok=True)
    manifest = pkg_dir / "manifest.py"
    manifest.write_text(content, encoding="utf-8")
    return manifest


# ---------------------------------------------------------------------------
# Cleanup sys.modules entre tests para evitar contaminación de módulos ext
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_inaki_ext_modules():
    yield
    to_remove = [k for k in sys.modules if k.startswith("_inaki_ext_")]
    for k in to_remove:
        del sys.modules[k]


# ---------------------------------------------------------------------------
# Tests — happy path
# ---------------------------------------------------------------------------


def test_ext_knowledge_source_registrada(tmp_path: Path) -> None:
    """Manifest con KNOWLEDGE_SOURCES factory → fuente aparece en el orquestrador."""
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()

    # Inyectar FakeKnowledgeSource como módulo importable por el manifest
    fake_mod = types.ModuleType("_test_ks_fake_mod")
    fake_mod.FakeKnowledgeSource = FakeKnowledgeSource
    sys.modules["_test_ks_fake_mod"] = fake_mod

    _write_manifest(
        ext_dir,
        "myext",
        "from _test_ks_fake_mod import FakeKnowledgeSource\n"
        "KNOWLEDGE_SOURCES = [\n"
        "    lambda agent_cfg, global_cfg, embedder: FakeKnowledgeSource('ext-source'),\n"
        "]\n",
    )

    container = _make_container(tmp_path)
    container._register_extensions([str(ext_dir)])

    assert "ext-source" in container._knowledge_orchestrator.source_ids

    del sys.modules["_test_ks_fake_mod"]


def test_orden_descubrimiento_memoria_config_ext(tmp_path: Path) -> None:
    """
    Orden: memoria primero, fuentes configuradas segundo, extensiones tercero.

    El fixture _make_container ya incluye la memory source (nivel 1).
    Simulamos una fuente de nivel 2 (configurada) añadiéndola a _pending_knowledge_sources.
    Luego registramos una ext que añade una fuente de nivel 3.
    Verificamos el orden resultante en source_ids.
    """
    from core.domain.services.knowledge_orchestrator import KnowledgeOrchestrator

    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()

    fake_mod = types.ModuleType("_test_ks_order_mod")
    fake_mod.FakeKnowledgeSource = FakeKnowledgeSource
    sys.modules["_test_ks_order_mod"] = fake_mod

    _write_manifest(
        ext_dir,
        "myext",
        "from _test_ks_order_mod import FakeKnowledgeSource\n"
        "KNOWLEDGE_SOURCES = [\n"
        "    lambda agent_cfg, global_cfg, embedder: FakeKnowledgeSource('ext-level-3'),\n"
        "]\n",
    )

    container = _make_container(tmp_path)

    # Añadir manualmente una fuente de nivel 2 (simulando lo que hace _collect_knowledge_sources)
    config_source = FakeKnowledgeSource("config-level-2")
    container._pending_knowledge_sources.append(config_source)
    # Reconstruir el orquestrador para que apunte a la lista actualizada
    container._knowledge_orchestrator = KnowledgeOrchestrator(
        sources=container._pending_knowledge_sources,
        max_total_chunks=10,
        token_budget_threshold=4000,
    )

    container._register_extensions([str(ext_dir)])

    ids = container._knowledge_orchestrator.source_ids
    assert ids.index("memory") < ids.index("config-level-2"), "memoria debe preceder a config"
    assert ids.index("config-level-2") < ids.index("ext-level-3"), "config debe preceder a ext"

    del sys.modules["_test_ks_order_mod"]


# ---------------------------------------------------------------------------
# Tests — aislamiento de fallos
# ---------------------------------------------------------------------------


def test_factory_que_falla_loguea_warning_y_continua(tmp_path: Path, caplog) -> None:
    """Factory que lanza excepción → WARNING emitido, otras fuentes siguen registradas."""
    import logging

    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()

    fake_mod = types.ModuleType("_test_ks_fail_mod")
    fake_mod.FakeKnowledgeSource = FakeKnowledgeSource
    sys.modules["_test_ks_fail_mod"] = fake_mod

    # Dos factories: la primera falla, la segunda funciona
    _write_manifest(
        ext_dir,
        "failext",
        "from _test_ks_fail_mod import FakeKnowledgeSource\n"
        "def _fail(agent_cfg, global_cfg, embedder):\n"
        "    raise RuntimeError('factory error intencional')\n"
        "KNOWLEDGE_SOURCES = [\n"
        "    _fail,\n"
        "    lambda agent_cfg, global_cfg, embedder: FakeKnowledgeSource('ext-ok'),\n"
        "]\n",
    )

    container = _make_container(tmp_path)
    with caplog.at_level(logging.WARNING):
        container._register_extensions([str(ext_dir)])

    # La factory que falló emite WARNING
    assert "factory de knowledge source falló" in caplog.text
    assert "factory error intencional" in caplog.text

    # La factory que funcionó sigue registrada
    assert "ext-ok" in container._knowledge_orchestrator.source_ids

    del sys.modules["_test_ks_fail_mod"]


# ---------------------------------------------------------------------------
# Tests — edge cases
# ---------------------------------------------------------------------------


def test_knowledge_sources_vacia_noop(tmp_path: Path) -> None:
    """KNOWLEDGE_SOURCES = [] → no-op, sin error, sin fuentes nuevas."""
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()

    _write_manifest(ext_dir, "emptyext", "KNOWLEDGE_SOURCES = []\n")

    container = _make_container(tmp_path)
    ids_antes = list(container._knowledge_orchestrator.source_ids)

    container._register_extensions([str(ext_dir)])

    assert container._knowledge_orchestrator.source_ids == ids_antes


def test_manifest_sin_knowledge_sources_es_compatible(tmp_path: Path) -> None:
    """
    Manifest sin atributo KNOWLEDGE_SOURCES → no-op, compatibilidad hacia atrás.
    Los manifests pre-existentes con solo TOOLS/SKILLS no deben verse afectados.
    """
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()

    # Manifest de extensión antigua — solo declara TOOLS y SKILLS, sin KNOWLEDGE_SOURCES
    _write_manifest(ext_dir, "legacyext", "TOOLS = []\nSKILLS = []\n")

    container = _make_container(tmp_path)
    ids_antes = list(container._knowledge_orchestrator.source_ids)

    container._register_extensions([str(ext_dir)])

    assert container._knowledge_orchestrator.source_ids == ids_antes
