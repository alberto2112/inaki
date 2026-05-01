"""Tests para `AgentRegistry` con `agents_dir` explícito."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from core.domain.errors import ConfigError
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


def _write_agent_with_channels(
    agents_dir: Path, agent_id: str, channels_block: str
) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    header = (
        f'id: {agent_id}\n'
        f'name: "{agent_id}"\n'
        'description: "agente de prueba"\n'
        'system_prompt: "soy un test"\n'
        'channels:\n'
    )
    (agents_dir / f"{agent_id}.yaml").write_text(
        header + channels_block,
        encoding="utf-8",
    )


def test_registry_rechaza_token_telegram_duplicado(tmp_path: Path) -> None:
    """Dos agentes con el mismo token Telegram levantarían pollings en conflicto."""
    agents_dir = tmp_path / "agents"
    block = '  telegram:\n    token: "SAME-TOKEN"\n'
    _write_agent_with_channels(agents_dir, "principal", block)
    _write_agent_with_channels(agents_dir, "secundario", block)

    with pytest.raises(ConfigError) as exc_info:
        AgentRegistry(agents_dir, _GLOBAL_RAW)

    msg = str(exc_info.value)
    assert "Telegram" in msg
    assert "principal" in msg and "secundario" in msg
    assert "delegate" in msg  # guía al shape correcto


def test_registry_rechaza_rest_host_port_duplicado(tmp_path: Path) -> None:
    """Dos agentes con el mismo host:port REST chocarían al bindear."""
    agents_dir = tmp_path / "agents"
    block = '  rest:\n    host: "0.0.0.0"\n    port: 6498\n'
    _write_agent_with_channels(agents_dir, "uno", block)
    _write_agent_with_channels(agents_dir, "dos", block)

    with pytest.raises(ConfigError) as exc_info:
        AgentRegistry(agents_dir, _GLOBAL_RAW)

    msg = str(exc_info.value)
    assert "6498" in msg
    assert "uno" in msg and "dos" in msg


def test_registry_permite_rest_mismo_host_puertos_distintos(tmp_path: Path) -> None:
    """Same host, different ports → no conflict."""
    agents_dir = tmp_path / "agents"
    _write_agent_with_channels(
        agents_dir, "a", '  rest:\n    host: "0.0.0.0"\n    port: 6498\n'
    )
    _write_agent_with_channels(
        agents_dir, "b", '  rest:\n    host: "0.0.0.0"\n    port: 6499\n'
    )

    registry = AgentRegistry(agents_dir, _GLOBAL_RAW)

    assert {a.id for a in registry.list_all()} == {"a", "b"}


def test_registry_permite_un_solo_agente_con_telegram(tmp_path: Path) -> None:
    """Happy path: solo el agente principal declara channels.telegram."""
    agents_dir = tmp_path / "agents"
    _write_agent_with_channels(
        agents_dir, "principal", '  telegram:\n    token: "UNIQUE-TOKEN"\n'
    )
    _write_agent(agents_dir, "subagente")  # sin channels

    registry = AgentRegistry(agents_dir, _GLOBAL_RAW)

    assert {a.id for a in registry.list_all()} == {"principal", "subagente"}
    assert len(registry.agents_with_channel("telegram")) == 1


def test_sub_agents_loaded_from_sub_agents_subdir(tmp_path: Path) -> None:
    """Sub-agentes en agents/sub-agents/ se cargan y se distinguen de los regulares."""
    agents_dir = tmp_path / "agents"
    _write_agent(agents_dir, "principal")
    sub_dir = agents_dir / "sub-agents"
    _write_agent(sub_dir, "worker", name="Worker")

    registry = AgentRegistry(agents_dir, _GLOBAL_RAW)

    assert {a.id for a in registry.list_all()} == {"principal", "worker"}
    assert [a.id for a in registry.list_regular()] == ["principal"]
    assert [a.id for a in registry.list_sub_agents()] == ["worker"]
    assert not registry.is_sub_agent("principal")
    assert registry.is_sub_agent("worker")


def test_sub_agents_excluded_from_agents_with_channel(tmp_path: Path) -> None:
    """Sub-agentes con channels declarados no aparecen en agents_with_channel."""
    agents_dir = tmp_path / "agents"
    _write_agent_with_channels(
        agents_dir, "principal", '  telegram:\n    token: "TOKEN-MAIN"\n'
    )
    sub_dir = agents_dir / "sub-agents"
    # Sub-agente con channels: debe ser ignorado para canales
    _write_agent_with_channels(
        sub_dir, "worker", '  telegram:\n    token: "TOKEN-WORKER"\n'
    )

    registry = AgentRegistry(agents_dir, _GLOBAL_RAW)

    assert len(registry.agents_with_channel("telegram")) == 1
    assert registry.agents_with_channel("telegram")[0].id == "principal"


def test_sub_agents_dont_trigger_channel_uniqueness_validation(tmp_path: Path) -> None:
    """Dos sub-agentes con el mismo token NO levantan ConfigError (no tienen canales)."""
    agents_dir = tmp_path / "agents"
    sub_dir = agents_dir / "sub-agents"
    _write_agent_with_channels(sub_dir, "worker_a", '  telegram:\n    token: "SAME-TOKEN"\n')
    _write_agent_with_channels(sub_dir, "worker_b", '  telegram:\n    token: "SAME-TOKEN"\n')

    # No debe lanzar — la validación de unicidad solo aplica a agentes regulares
    registry = AgentRegistry(agents_dir, _GLOBAL_RAW)

    assert {a.id for a in registry.list_sub_agents()} == {"worker_a", "worker_b"}


def test_registry_empty_sub_agents_dir_is_fine(tmp_path: Path) -> None:
    """Si no existe el directorio sub-agents/, se carga normalmente sin error."""
    agents_dir = tmp_path / "agents"
    _write_agent(agents_dir, "principal")

    registry = AgentRegistry(agents_dir, _GLOBAL_RAW)

    assert [a.id for a in registry.list_all()] == ["principal"]
    assert registry.list_sub_agents() == []


def _write_sub_agent_with_memory(
    sub_dir: Path, agent_id: str, memory_block: str | None
) -> None:
    """Escribe un sub-agente con bloque memory: opcional."""
    sub_dir.mkdir(parents=True, exist_ok=True)
    body = (
        f'id: {agent_id}\n'
        f'name: "{agent_id}"\n'
        'description: "sub agente"\n'
        'system_prompt: "soy un test"\n'
    )
    if memory_block is not None:
        body += memory_block
    (sub_dir / f"{agent_id}.yaml").write_text(body, encoding="utf-8")


def test_sub_agent_memory_default_false_when_not_specified(tmp_path: Path) -> None:
    """Sub-agente sin bloque memory: → memory.enabled debe ser false (default override)."""
    agents_dir = tmp_path / "agents"
    sub_dir = agents_dir / "sub-agents"
    global_raw = {**_GLOBAL_RAW, "memory": {"db_filename": "data/inaki.db", "enabled": True}}

    _write_sub_agent_with_memory(sub_dir, "worker", memory_block=None)

    registry = AgentRegistry(agents_dir, global_raw)

    worker = registry.get("worker")
    assert worker.memory.enabled is False, (
        "memory.enabled debe forzarse a false cuando el sub-agente no lo especifica"
    )


def test_sub_agent_memory_enabled_explicit_true_is_respected(tmp_path: Path) -> None:
    """Sub-agente con memory.enabled: true explícito → se respeta, no se pisa."""
    agents_dir = tmp_path / "agents"
    sub_dir = agents_dir / "sub-agents"
    global_raw = {**_GLOBAL_RAW, "memory": {"db_filename": "data/inaki.db", "enabled": False}}

    _write_sub_agent_with_memory(
        sub_dir, "stateful_worker", memory_block="memory:\n  enabled: true\n"
    )

    registry = AgentRegistry(agents_dir, global_raw)

    worker = registry.get("stateful_worker")
    assert worker.memory.enabled is True, (
        "memory.enabled: true explícito en el sub-agente debe respetarse"
    )


def test_sub_agent_memory_enabled_explicit_false_is_respected(tmp_path: Path) -> None:
    """Sub-agente con memory.enabled: false explícito → se respeta (no es no-op)."""
    agents_dir = tmp_path / "agents"
    sub_dir = agents_dir / "sub-agents"
    global_raw = {**_GLOBAL_RAW, "memory": {"db_filename": "data/inaki.db", "enabled": True}}

    _write_sub_agent_with_memory(
        sub_dir, "worker", memory_block="memory:\n  enabled: false\n"
    )

    registry = AgentRegistry(agents_dir, global_raw)

    assert registry.get("worker").memory.enabled is False


def test_regular_agent_memory_default_inherited_from_global(tmp_path: Path) -> None:
    """Agente regular SIN memory.enabled hereda del global (no se fuerza a false)."""
    agents_dir = tmp_path / "agents"
    global_raw = {**_GLOBAL_RAW, "memory": {"db_filename": "data/inaki.db", "enabled": True}}

    _write_agent(agents_dir, "principal")  # sin bloque memory

    registry = AgentRegistry(agents_dir, global_raw)

    assert registry.get("principal").memory.enabled is True, (
        "agentes regulares heredan memory.enabled del global — solo sub-agentes son default-false"
    )


def test_registry_skips_secrets_and_examples(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    _write_agent(agents_dir, "real")
    (agents_dir / "real.secrets.yaml").write_text(
        "providers:\n  openrouter:\n    api_key: x\n",
        encoding="utf-8",
    )
    (agents_dir / "sample.example.yaml").write_text("id: sample\n", encoding="utf-8")

    registry = AgentRegistry(agents_dir, _GLOBAL_RAW)

    assert [a.id for a in registry.list_all()] == ["real"]
