"""``build_config_tool`` marca con el origen ``runtime`` lo que pisó ``config_set``.

El VALOR sale de los objetos validados (que ya traen la capa de overrides aplicada por
el loader); el store solo aporta QUÉ hojas están pisadas. El origen de disco se parchea
para no depender del home de la máquina.
"""

from __future__ import annotations

from unittest.mock import patch

from inaki.config.runtime_overrides import EntradaOverride, OverridesDeRuntime
from inaki.config.wiring import build_config_tool
from tests.app.conftest import global_cfg


def _construir(agent_config, overrides):
    agent_config.llm.model = "gpt-5-mini"  # lo que el loader ya dejó aplicado
    with patch(
        "inaki.config.wiring._origenes_de_config",
        return_value={"llm.model": "agent", "llm.temperature": "global"},
    ):
        return build_config_tool(global_cfg(), agent_config, overrides)


async def test_el_override_del_agente_se_ve_como_runtime(agent_config):
    store = OverridesDeRuntime()
    store.agregar(EntradaOverride.para(agent_config.id, "llm.model", "gpt-5-mini"), None)

    tool = _construir(agent_config, store)

    modelo = await tool.execute(operation="get", path="llm.model")
    temperatura = await tool.execute(operation="get", path="llm.temperature")
    assert "gpt-5-mini" in modelo.output and "[runtime]" in modelo.output
    assert "[global]" in temperatura.output


async def test_el_override_de_otro_agente_no_marca_el_mio(agent_config):
    store = OverridesDeRuntime()
    store.agregar(EntradaOverride.para("otro-agente", "llm.model", "x"), None)

    tool = _construir(agent_config, store)

    modelo = await tool.execute(operation="get", path="llm.model")
    assert "[agent]" in modelo.output
    assert "[runtime]" not in modelo.output


async def test_sin_store_no_hay_origen_runtime(agent_config):
    tool = _construir(agent_config, None)

    modelo = await tool.execute(operation="get", path="llm.model")
    assert "[agent]" in modelo.output
