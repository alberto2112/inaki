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
from inaki.config import AgentDelegationConfig
from inaki.shared.errors import AgentNotFoundError
from tests.app.conftest import RegistryFalso, agent_cfg, global_cfg

_BUILTINS = {
    "knowledge_search",
    "knowledge_admin",
    "memory",
    "search_history",
    "tool_output",
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


# --- cola de background: store durable y workdirs (bg-delegation-transcript, F6) -------------


def test_la_cola_recibe_un_store_en_el_directorio_de_datos_sin_abrirlo(
    home: Path, bordes_falsos: AsyncMock
) -> None:
    """El store es del tier harness-global: UN fichero junto al ``history.db`` (mismo patrón
    que ``faces.db`` / ``telegram_files.db``). Ensamblar es sync y no toca el disco: el store
    se abre recién en ``start()`` de la cola."""
    from inaki.agents.delegation.sqlite_task_store import SQLiteBackgroundTaskStore

    harness = _ensamblar(home, agent_cfg("dev"), agent_cfg("ops"))

    store = harness.background_queue._store
    assert isinstance(store, SQLiteBackgroundTaskStore)
    assert store._path == home / "data" / "background_tasks.db"
    assert store._db is None  # no se abrió
    assert not store._path.exists()  # ni se creó el fichero


async def test_startup_abre_el_store_y_shutdown_lo_cierra(
    home: Path, bordes_falsos: AsyncMock
) -> None:
    harness = _ensamblar(home, agent_cfg("dev"))
    store = harness.background_queue._store
    assert store is not None

    await harness.startup()
    try:
        assert store._db is not None  # type: ignore[attr-defined]
        assert (home / "data" / "background_tasks.db").exists()
    finally:
        await harness.shutdown()

    assert store._db is None  # type: ignore[attr-defined]


def test_con_el_flag_el_caller_tiene_workdir_y_las_raices_son_las_de_los_regulares(
    home: Path, bordes_falsos: AsyncMock
) -> None:
    """REQ-BGW-1 S2: ``grant_workdir`` en un agente solo le da workdir a ese agente; los
    sub-agentes no delegan, así que no aportan raíz."""
    con = agent_cfg("ana", delegation=AgentDelegationConfig(enabled=True, grant_workdir=True))
    sin = agent_cfg("beto", delegation=AgentDelegationConfig(enabled=True))

    harness = _ensamblar(home, con, sin, subs=[agent_cfg("worker")])

    gestor = harness.background_queue._workdirs
    assert gestor is not None
    assert gestor.tiene_workdir("ana") is True
    assert gestor.tiene_workdir("beto") is False
    assert set(gestor._workspaces) == {"ana", "beto"}  # el sub-agente no tiene raíz
    assert (
        gestor._workspaces["ana"]
        == Path(harness.agents["ana"].agent_config.workspace.path).resolve()
    )


def test_sin_el_flag_nadie_tiene_workdir_pero_el_gestor_existe(
    home: Path, bordes_falsos: AsyncMock
) -> None:
    """El gestor se construye SIEMPRE: ``inspect`` y la poda lo necesitan aunque ningún
    caller tenga el flag (una fila vieja puede traer un workdir)."""
    harness = _ensamblar(home, agent_cfg("dev", delegation_enabled=True))

    gestor = harness.background_queue._workdirs
    assert gestor is not None
    assert gestor.tiene_workdir("dev") is False


def test_el_flag_sin_delegacion_habilitada_no_da_workdir(
    home: Path, bordes_falsos: AsyncMock
) -> None:
    """Sin ``delegate`` el agente no puede encolar nada: el flag solo vale con ``enabled``."""
    cfg = agent_cfg("dev", delegation=AgentDelegationConfig(enabled=False, grant_workdir=True))

    harness = _ensamblar(home, cfg)

    assert harness.background_queue._workdirs.tiene_workdir("dev") is False  # type: ignore[union-attr]
