"""Tests para la validación de argumentos obligatorios en ToolRegistry.execute().

Defecto cazado en producción: el LLM emitía una tool call de `delegate` sin
`agent_id` → `DelegateTool.execute(**kwargs)` reventaba con un TypeError críptico
("missing 1 required positional argument: 'agent_id'"). La guarda valida los
`required` del schema ANTES de invocar y devuelve un error claro y retryable.
"""

from unittest.mock import AsyncMock, MagicMock

from adapters.outbound.tools.tool_registry import ToolRegistry
from core.ports.outbound.tool_port import ToolResult


def _make_tool(
    name: str,
    *,
    required: list[str] | None = None,
    properties: dict | None = None,
) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.description = "desc"
    schema: dict = {"type": "object", "properties": properties or {}}
    if required is not None:
        schema["required"] = required
    tool.parameters_schema = schema
    tool.execute = AsyncMock(return_value=ToolResult(tool_name=name, output="ok", success=True))
    return tool


def _registry_with(tool: MagicMock) -> ToolRegistry:
    registry = ToolRegistry(AsyncMock())
    registry.register(tool)
    return registry


async def test_falta_arg_obligatorio_devuelve_error_claro_sin_invocar():
    tool = _make_tool(
        "delegate",
        required=["agent_id", "task"],
        properties={"agent_id": {}, "task": {}, "wait": {}},
    )
    registry = _registry_with(tool)

    # Llamada como la del bug: sin agent_id.
    result = await registry.execute("delegate", task="investigá X", wait=False)

    assert result.success is False
    assert result.retryable is True  # el modelo puede corregir y reintentar
    assert result.error == "missing_required_args: agent_id"
    assert "agent_id" in result.output
    # No se debe haber invocado la tool: la guarda corta antes.
    tool.execute.assert_not_called()


async def test_mensaje_lista_args_esperados_con_marca():
    tool = _make_tool(
        "delegate",
        required=["agent_id", "task"],
        properties={"agent_id": {}, "task": {}, "wait": {}},
    )
    registry = _registry_with(tool)

    result = await registry.execute("delegate", wait=False)

    # Faltan los dos obligatorios.
    assert "agent_id" in result.output
    assert "task" in result.output
    # El contrato completo aparece con su marca.
    assert "agent_id (obligatorio)" in result.output
    assert "wait (opcional)" in result.output


async def test_todos_los_obligatorios_presentes_invoca_la_tool():
    tool = _make_tool(
        "delegate",
        required=["agent_id", "task"],
        properties={"agent_id": {}, "task": {}},
    )
    registry = _registry_with(tool)

    result = await registry.execute("delegate", agent_id="worker", task="hacelo")

    assert result.success is True
    tool.execute.assert_awaited_once_with(agent_id="worker", task="hacelo")


async def test_schema_sin_required_no_valida():
    # Tool sin clave `required` → no se valida nada, se invoca normal.
    tool = _make_tool("noop", properties={"x": {}})
    registry = _registry_with(tool)

    result = await registry.execute("noop")

    assert result.success is True
    tool.execute.assert_awaited_once_with()


async def test_required_malformado_no_rompe():
    # Schema con `required` no-lista (defensivo) → no valida, invoca normal.
    tool = _make_tool("raro", properties={"x": {}})
    tool.parameters_schema["required"] = "agent_id"  # str, no lista
    registry = _registry_with(tool)

    result = await registry.execute("raro")

    assert result.success is True


async def test_tool_inexistente_sigue_devolviendo_no_registrada():
    registry = _registry_with(_make_tool("real"))

    result = await registry.execute("fantasma", x=1)

    assert result.success is False
    assert result.retryable is False
    assert "no encontrada" in result.output
