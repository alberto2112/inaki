"""Regression guard: built-in tools registradas y extensiones NO en built-ins."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


from adapters.outbound.skills.yaml_skill_repo import YamlSkillRepository
from adapters.outbound.tools.tool_registry import ToolRegistry
from infrastructure.container import AgentContainer


class FakeEmbedder:
    async def embed_passage(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


def _make_container(tmp_path: Path) -> AgentContainer:
    container = AgentContainer.__new__(AgentContainer)
    container._tools = ToolRegistry(embedder=FakeEmbedder())
    container._skills = YamlSkillRepository(FakeEmbedder())
    container.agent_config = SimpleNamespace(
        id="test-agent",
        workspace=SimpleNamespace(
            path=str(tmp_path / "workspace"),
            containment="strict",
        ),
    )
    return container


def test_builtin_tools_present(tmp_path: Path) -> None:
    """web_search, read_file, write_file, patch_file presentes con ext_dirs=[]."""
    container = _make_container(tmp_path)
    container._register_tools()
    container._register_extensions([])

    registered = set(container._tools._tools.keys())
    for expected in ("web_search", "read_file", "write_file", "patch_file"):
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
