"""``ensamblar``: el proceso entero desde config, sin LLM ni embedding reales.

Cubre lo que antes probaban los tests del ``AppContainer``: el init de dos
pasadas (delegación recién cuando existen todos los agentes), qué tools
built-in tiene un agente, el registro de bots y el acceso por id.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from inaki.agents.delegation.delegate_tool import DelegateTool
from inaki.app.assembly import ensamblar
from inaki.app.runtime import AgentRuntime, HarnessRuntime
from inaki.shared.errors import AgentNotFoundError
from tests.app.conftest import RegistryFalso, agent_cfg, global_cfg

_BUILTINS = {
    "knowledge_search",
    "knowledge_admin",
    "search_memory",
    "delete_memory",
    "update_memory",
    "search_history",
    "web_search",
    "read_file",
    "write_file",
    "patch_file",
    "edit_file",
    "config",
    "scheduler",
}


def _ensamblar(home: Path, *agentes: object, subs: list | None = None) -> HarnessRuntime:
    registry = RegistryFalso(list(agentes), subs)  # type: ignore[arg-type]
    return ensamblar(global_cfg(), registry, config_dir=home / "config")  # type: ignore[arg-type]


def test_dos_pasadas_la_delegacion_se_wirea_cuando_existen_todos(
    home: Path, bordes_falsos: AsyncMock
) -> None:
    """A delega en B (sub-agente); C no delega. Solo A tiene ``delegate``."""
    a = agent_cfg("agent-a", delegation_enabled=True, allowed_targets=["agent-b"])
    b = agent_cfg("agent-b")
    c = agent_cfg("agent-c")

    harness = _ensamblar(home, a, c, subs=[b])

    assert set(harness.agents) == {"agent-a", "agent-b", "agent-c"}
    assert isinstance(harness.agents["agent-a"].tools._tools["delegate"], DelegateTool)
    assert "delegate" not in harness.agents["agent-b"].tools._tools
    assert "delegate" not in harness.agents["agent-c"].tools._tools


def test_cada_agente_regular_tiene_los_builtins_y_el_sub_agente_no_tiene_scheduler(
    home: Path, bordes_falsos: AsyncMock
) -> None:
    harness = _ensamblar(home, agent_cfg("dev"), subs=[agent_cfg("worker")])

    dev = harness.agents["dev"]
    assert _BUILTINS <= set(dev.tools._tools), _BUILTINS - set(dev.tools._tools)
    assert dev.schedule_task is harness.scheduler.use_case
    assert dev.manual_task_runner is harness.scheduler.service
    worker = harness.agents["worker"]
    assert "scheduler" not in worker.tools._tools
    assert worker.schedule_task is None and worker.manual_task_runner is None


def test_el_runtime_es_inmutable_y_sus_none_son_por_config(
    home: Path, bordes_falsos: AsyncMock
) -> None:
    """Sin token de Telegram ni fotos: los campos wired por el harness son ``None``
    porque la capacidad no está configurada, y el objeto no se puede mutar."""
    harness = _ensamblar(home, agent_cfg("dev"))

    dev = harness.agents["dev"]
    assert isinstance(dev, AgentRuntime)
    assert dev.process_photo is None
    assert dev.broadcast_egress is None and dev.telegram_file_repo is None
    assert harness.channels == () and harness.telegram_bots == {}
    with pytest.raises(AttributeError):
        dev.process_photo = None  # type: ignore[misc]


def test_get_agent_nombra_los_disponibles(home: Path, bordes_falsos: AsyncMock) -> None:
    harness = _ensamblar(home, agent_cfg("dev"))

    assert harness.get_agent("dev").agent_config.id == "dev"
    with pytest.raises(AgentNotFoundError, match="dev"):
        harness.get_agent("ghost")


def test_el_dispatcher_resuelve_los_agentes_por_enlace_tardio(
    home: Path, bordes_falsos: AsyncMock
) -> None:
    """El dispatcher se construye en la pasada 2 con un dict vacío; al final del
    ensamblado ese MISMO dict tiene los runtimes."""
    harness = _ensamblar(home, agent_cfg("dev"))

    assert harness.dispatcher._agents["dev"] is harness.agents["dev"]


async def test_startup_y_shutdown_arrancan_y_paran_los_servicios(
    home: Path, bordes_falsos: AsyncMock
) -> None:
    harness = _ensamblar(home, agent_cfg("dev"))

    await harness.startup()
    try:
        assert harness.scheduler.service._task is not None
    finally:
        await harness.shutdown()
