"""Regression guard: built-in tools registradas y extensiones NO en built-ins."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


from adapters.outbound.config_repository.yaml_tool_config_store import YamlToolConfigStore
from adapters.outbound.history.sqlite_history_store import (
    HistoryStoreSettings,
    SQLiteHistoryStore,
)
from adapters.outbound.skills.yaml_skill_repo import YamlSkillRepository
from adapters.outbound.tools.tool_registry import ToolRegistry
from infrastructure.container import AgentContainer
from infrastructure.home import set_inaki_home


class FakeEmbedder:
    async def embed_passage(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


class FakeMemory:
    async def store(self, entry) -> None: ...
    async def search(self, query_embedding, top_k=5):
        return []

    async def search_with_scores(self, query_vec, top_k=5):
        return []

    async def get_recent(self, limit=10, agent_id=None, channel=None, chat_id=None):
        return []

    async def get_by_id(self, memory_id):
        return None

    async def delete(self, memory_id):
        return None

    async def update(self, memory_id, content=None, tags=None, relevance=None, embedding=None):
        return None

    async def load_unreconciled(self, agent_id, channel=None, chat_id=None):
        return []

    async def mark_reconciled(self, ids) -> int:
        return 0


@pytest.fixture(autouse=True)
def _home_aislado(tmp_path: Path):
    """Ancla el home al tmp_path del test.

    El container resuelve el home para leer las capas de config (el origen de
    cada valor). Sin esto el test leería el ``~/.inaki`` REAL de quien corre la
    suite. El override es global de proceso: se limpia al salir o se filtra a
    todo lo que venga después.
    """
    set_inaki_home(tmp_path)
    yield
    set_inaki_home(None)


def _make_container(tmp_path: Path) -> AgentContainer:
    embedder = FakeEmbedder()
    container = AgentContainer.__new__(AgentContainer)
    container._tools = ToolRegistry(embedder=embedder)
    container._skills = YamlSkillRepository(embedder)
    container._embedder = embedder
    container._memory = FakeMemory()
    # _history lo consume _register_tools (SearchHistoryTool); el container real
    # lo arma en container.py:303. Usamos un store real en archivo temporal.
    container._history = SQLiteHistoryStore(
        HistoryStoreSettings(db_filename=str(tmp_path / "history.db"))
    )
    # `model_dump` no es decorativo: `_register_tools` arma con él el snapshot
    # de config que sirve la tool `config`. Un fake de AgentConfig tiene que
    # poder volcarse igual que el real.
    container.agent_config = SimpleNamespace(  # type: ignore[assignment]
        id="test-agent",
        workspace=SimpleNamespace(
            path=str(tmp_path / "workspace"),
            containment="strict",
        ),
        model_dump=lambda: {"id": "test-agent", "llm": {"model": "fake-model"}},
    )
    container._tool_config_store = YamlToolConfigStore(
        store_path=tmp_path / "tool_config.yaml",
        key_path=tmp_path / "secret.key",
    )
    # _global_config necesario para _build_knowledge_orchestrator
    container._global_config = SimpleNamespace(  # type: ignore[assignment]
        knowledge=None,
        model_dump=lambda: {"app": {"ext_dirs": []}},
    )
    return container


def test_builtin_tools_present(tmp_path: Path) -> None:
    """knowledge_search, web_search, read_file, write_file, patch_file, edit_file presentes con ext_dirs=[]."""
    container = _make_container(tmp_path)
    container._register_tools()
    container._register_extensions([])

    registered = set(container._tools._tools.keys())
    for expected in (
        "knowledge_search",
        "web_search",
        "read_file",
        "write_file",
        "patch_file",
        "edit_file",
    ):
        assert expected in registered, f"Built-in '{expected}' no registrada"


def test_shell_and_exchange_not_in_builtins(tmp_path: Path) -> None:
    """shell_exec y exchange_calendar NO en built-ins cuando ext_dirs=[]."""
    container = _make_container(tmp_path)
    container._register_tools()
    container._register_extensions([])

    registered = set(container._tools._tools.keys())
    assert "shell_exec" not in registered
    assert "shell" not in registered
    assert "exchange_calendar" not in registered
