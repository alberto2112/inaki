"""``ensamblar(overrides=)``: dónde vive ``config_set`` y con qué reloader.

Con el store del proceso, cada agente REGULAR recibe la tool ``config_set`` y el
``DaemonReloader`` descarta ese mismo store en un reload manual. Sin store (``inaki
chat``, tests) no hay reload que aplique el cambio, así que la tool ni se registra.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from inaki.app.assembly import ensamblar
from inaki.config.runtime_overrides import EntradaOverride, OverridesDeRuntime
from tests.app.conftest import RegistryFalso, agent_cfg, global_cfg


def _ensamblar(home: Path, store: OverridesDeRuntime | None, *agentes, subs=None):
    registry = RegistryFalso(list(agentes), subs)  # type: ignore[arg-type]
    return ensamblar(
        global_cfg(),
        registry,  # type: ignore[arg-type]
        config_dir=home / "config",
        overrides=store,
    )


def test_con_overrides_cada_agente_regular_tiene_config_set_privilegiada(
    home: Path, bordes_falsos: AsyncMock
) -> None:
    harness = _ensamblar(home, OverridesDeRuntime(), agent_cfg("a"), agent_cfg("b"))

    for agente in ("a", "b"):
        tools = harness.agents[agente].tools
        assert "config_set" in tools._tools
        assert tools.requires_operator("config_set") is True
        assert tools.requires_operator("config") is False


def test_sin_overrides_no_hay_config_set_pero_si_config(
    home: Path, bordes_falsos: AsyncMock
) -> None:
    harness = _ensamblar(home, None, agent_cfg("a"))

    assert "config_set" not in harness.agents["a"].tools._tools
    assert "config" in harness.agents["a"].tools._tools


def test_un_sub_agente_no_tiene_config_set(home: Path, bordes_falsos: AsyncMock) -> None:
    # El store solo se aplica a agentes regulares (`runtime_por_agente`): una tool
    # que aceptara cambios para un sub-agente los guardaría sin que nada los aplique.
    harness = _ensamblar(home, OverridesDeRuntime(), agent_cfg("a"), subs=[agent_cfg("worker")])

    assert "config_set" in harness.agents["a"].tools._tools
    assert "config_set" not in harness.agents["worker"].tools._tools


def test_el_reloader_del_harness_descarta_el_store_del_proceso(
    home: Path, bordes_falsos: AsyncMock
) -> None:
    store = OverridesDeRuntime()
    store.agregar(EntradaOverride.para("a", "llm.model", "x"), None)
    harness = _ensamblar(home, store, agent_cfg("a"))

    descartados = harness.reloader.request_reload()

    assert descartados == ["a: llm.model = 'x'"]
    assert store.paths_runtime("a") == frozenset()


def test_config_get_ve_los_overrides_del_store_como_runtime(
    home: Path, bordes_falsos: AsyncMock
) -> None:
    store = OverridesDeRuntime()
    store.agregar(EntradaOverride.para("a", "llm.temperature", 0.1), None)
    harness = _ensamblar(home, store, agent_cfg("a"))

    import asyncio

    r = asyncio.run(
        harness.agents["a"].tools.execute("config", operation="get", path="llm.temperature")
    )

    assert "[runtime]" in r.output
