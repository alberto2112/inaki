"""Tests de ``requires_operator`` en ToolRegistry y en el contrato ITool/IToolExecutor.

Una tool privilegiada DECLARA ``requires_operator = True``; el loop del kernel solo
conoce ``IToolExecutor.requires_operator(nombre)``. Una tool inexistente NO es
privilegiada (el rechazo de "no encontrada" ya la corta en ``execute``).
"""

from unittest.mock import AsyncMock

import pytest

from inaki.kernel.ports.tool_port import ITool, IToolExecutor, ToolResult
from inaki.tools.registry import ToolRegistry


class _ToolComun(ITool):
    name = "comun"
    description = "tool sin privilegio"
    parameters_schema = {"type": "object", "properties": {}}

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(tool_name=self.name, output="ok", success=True)


class _ToolPrivilegiada(ITool):
    name = "privilegiada"
    description = "tool de operador"
    parameters_schema = {"type": "object", "properties": {}}
    requires_operator = True

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(tool_name=self.name, output="ok", success=True)


def _registry() -> ToolRegistry:
    registry = ToolRegistry(AsyncMock())
    registry.register(_ToolComun())
    registry.register(_ToolPrivilegiada())
    return registry


def test_itool_no_es_privilegiada_por_defecto():
    assert ITool.requires_operator is False
    assert _ToolComun.requires_operator is False


def test_registry_reporta_privilegiada():
    assert _registry().requires_operator("privilegiada", {}) is True


def test_registry_reporta_tool_comun_como_no_privilegiada():
    assert _registry().requires_operator("comun", {}) is False


def test_registry_tool_inexistente_no_es_privilegiada():
    assert _registry().requires_operator("no_existe", {}) is False


def test_executor_exige_implementar_requires_operator():
    """Abstracto a propósito: un default False haría que un executor que no lo
    implemente ejecute privilegiadas sin chequeo (falla abierto)."""
    assert "requires_operator" in IToolExecutor.__abstractmethods__

    class _Incompleto(IToolExecutor):
        def register(self, tool): ...
        async def execute(self, tool_name, **kwargs): ...
        def get_schemas(self): ...
        async def get_schemas_relevant(self, query_embedding, top_k=5, min_score=0.0): ...
        async def get_schemas_relevant_with_scores(
            self, query_embedding, top_k=5, min_score=0.0
        ): ...

    with pytest.raises(TypeError):
        _Incompleto()  # type: ignore[abstract]


# -- pisar una tool privilegiada (config-set-efimero §5.7) -------------------------


def _con_nombre(nombre: str, *, privilegiada: bool) -> ITool:
    clase = type(
        "_Impostora",
        (_ToolComun,),
        {"name": nombre, "requires_operator": privilegiada},
    )
    return clase()


def test_pisar_una_privilegiada_con_una_comun_loguea_warning(caplog):
    registry = ToolRegistry(AsyncMock())
    registry.register(_con_nombre("config_set", privilegiada=True))

    with caplog.at_level("WARNING", logger="inaki.tools.registry"):
        registry.register(_con_nombre("config_set", privilegiada=False))

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "config_set" in warnings[0].getMessage()
    assert (
        registry.requires_operator("config_set", {}) is False
    )  # el privilegio se perdió: está dicho


@pytest.mark.parametrize(
    "antes, despues",
    [
        (False, False),  # pisar una común: preexistente y sin riesgo
        (False, True),  # ganar privilegio: más estricto, no es una pérdida
        (True, True),  # la nueva también lo declara
    ],
)
def test_los_otros_pisados_no_loguean_warning(caplog, antes, despues):
    registry = ToolRegistry(AsyncMock())
    registry.register(_con_nombre("x", privilegiada=antes))

    with caplog.at_level("WARNING", logger="inaki.tools.registry"):
        registry.register(_con_nombre("x", privilegiada=despues))

    assert [r for r in caplog.records if r.levelname == "WARNING"] == []
