"""Tests para `AgentRegistry` con `agents_dir` explícito."""

from __future__ import annotations

import textwrap
from pathlib import Path

from infrastructure.config import AgentRegistry


_GLOBAL_RAW: dict = {
    "llm": {"provider": "openrouter", "model": "anthropic/claude-3-5-haiku"},
    "embedding": {"provider": "e5_onnx", "model_dirname": "models/e5-small"},
    "memory": {"db_filename": "data/inaki.db"},
    "history": {"db_filename": "data/history.db"},
}


def _write_agent(agents_dir: Path, agent_id: str, name: str = "Test Agent") -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{agent_id}.yaml").write_text(
        textwrap.dedent(
            f"""\
            id: {agent_id}
            name: "{name}"
            description: "un agente de prueba"
            system_prompt: "soy un test"
            """
        ),
        encoding="utf-8",
    )


def test_registry_uses_agents_dir_argument(tmp_path: Path) -> None:
    agents_dir = tmp_path / "custom_agents"
    _write_agent(agents_dir, "alpha", name="Alpha")

    registry = AgentRegistry(agents_dir, _GLOBAL_RAW)

    assert [a.id for a in registry.list_all()] == ["alpha"]
    assert registry.get("alpha").name == "Alpha"


def test_registry_does_not_look_under_config_subdir(tmp_path: Path) -> None:
    """Regresión: antes derivaba `agents_dir = config_dir / 'agents'`."""
    agents_dir = tmp_path / "agents"
    _write_agent(agents_dir, "alpha")

    wrong_dir = tmp_path / "config" / "agents"
    _write_agent(wrong_dir, "beta")

    registry = AgentRegistry(agents_dir, _GLOBAL_RAW)

    ids = {a.id for a in registry.list_all()}
    assert ids == {"alpha"}
    assert "beta" not in ids


def test_registry_empty_when_agents_dir_missing(tmp_path: Path) -> None:
    registry = AgentRegistry(tmp_path / "nonexistent", _GLOBAL_RAW)

    assert registry.list_all() == []


def test_registry_skips_secrets_and_examples(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    _write_agent(agents_dir, "real")
    (agents_dir / "real.secrets.yaml").write_text("llm:\n  api_key: x\n", encoding="utf-8")
    (agents_dir / "sample.example.yaml").write_text("id: sample\n", encoding="utf-8")

    registry = AgentRegistry(agents_dir, _GLOBAL_RAW)

    assert [a.id for a in registry.list_all()] == ["real"]
