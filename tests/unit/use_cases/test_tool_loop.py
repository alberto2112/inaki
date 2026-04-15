"""Tests unitarios para core/use_cases/_tool_loop.py — run_tool_loop."""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.domain.entities.message import Message, Role
from core.domain.errors import ToolLoopMaxIterationsError
from core.domain.value_objects.llm_response import LLMResponse
from core.ports.outbound.tool_port import ToolResult
from core.use_cases._tool_loop import run_tool_loop


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tool_call_response(tool_name: str, arguments: dict | None = None) -> LLMResponse:
    """Construye la respuesta estructurada que el LLM emitiría para llamar una tool."""
    return LLMResponse(
        text_blocks=[],
        tool_calls=[
            {
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(arguments or {}),
                }
            }
        ],
        raw="",
    )


def _make_llm(*responses: LLMResponse | str) -> AsyncMock:
    """LLM mock que devuelve las respuestas en orden.

    Acepta ``LLMResponse`` o ``str`` (los strings se envuelven con
    ``LLMResponse.of_text`` por conveniencia).
    """
    llm = AsyncMock()
    normalized: list[LLMResponse] = [
        r if isinstance(r, LLMResponse) else LLMResponse.of_text(r)
        for r in responses
    ]
    llm.complete.side_effect = normalized
    return llm


def _make_tools(tool_name: str = "mytool", success: bool = True) -> MagicMock:
    """Tools mock que devuelve siempre un ToolResult exitoso (o fallido)."""
    tools = AsyncMock()
    tools.execute = AsyncMock(
        return_value=ToolResult(
            tool_name=tool_name,
            output="resultado de tool",
            success=success,
        )
    )
    return tools


def _base_messages() -> list[Message]:
    return [Message(role=Role.USER, content="Hola")]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_happy_path_no_tool_calls_returns_immediately():
    """El LLM responde sin tool calls → el loop termina en la primera iteración."""
    llm = _make_llm("Respuesta directa sin tools")
    tools = _make_tools()

    result = await run_tool_loop(
        llm=llm,
        tools=tools,
        messages=_base_messages(),
        system_prompt="Sos un asistente.",
        tool_schemas=[{"name": "mytool"}],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="test-agent",
    )

    assert result == "Respuesta directa sin tools"
    assert llm.complete.call_count == 1
    tools.execute.assert_not_called()


async def test_happy_path_one_tool_call_then_final_response():
    """LLM pide una tool, luego responde sin más tool calls."""
    tool_call = _tool_call_response("mytool", {"key": "value"})
    final = "Respuesta final tras tool"

    llm = _make_llm(tool_call, final)
    tools = _make_tools()

    result = await run_tool_loop(
        llm=llm,
        tools=tools,
        messages=_base_messages(),
        system_prompt="Sos un asistente.",
        tool_schemas=[{"name": "mytool"}],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="test-agent",
    )

    assert result == final
    assert llm.complete.call_count == 2
    tools.execute.assert_called_once_with("mytool", key="value")


async def test_happy_path_multiple_tool_calls_then_final_response():
    """LLM pide tools en dos iteraciones, luego responde sin tools."""
    tool_call = _tool_call_response("mytool")

    llm = _make_llm(tool_call, tool_call, "Respuesta final")
    tools = _make_tools()

    result = await run_tool_loop(
        llm=llm,
        tools=tools,
        messages=_base_messages(),
        system_prompt="Sos un asistente.",
        tool_schemas=[{"name": "mytool"}],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="test-agent",
    )

    assert result == "Respuesta final"
    assert llm.complete.call_count == 3
    assert tools.execute.call_count == 2


async def test_does_not_mutate_original_messages():
    """El loop no muta la lista de mensajes original."""
    original = _base_messages()
    original_len = len(original)
    tool_call = _tool_call_response("mytool")

    llm = _make_llm(tool_call, "Listo")
    tools = _make_tools()

    await run_tool_loop(
        llm=llm,
        tools=tools,
        messages=original,
        system_prompt="Prompt",
        tool_schemas=[{"name": "mytool"}],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="agent",
    )

    assert len(original) == original_len


# ---------------------------------------------------------------------------
# Max iterations
# ---------------------------------------------------------------------------


async def test_max_iterations_raises_tool_loop_max_iterations_error():
    """Al agotar max_iterations sin respuesta final → raise ToolLoopMaxIterationsError."""
    tool_call_1 = _tool_call_response("mytool")
    tool_call_2 = _tool_call_response("mytool")
    # La tercera respuesta (última iteración) también es una tool call → agota el límite
    tool_call_last = _tool_call_response("mytool")

    llm = _make_llm(tool_call_1, tool_call_2, tool_call_last)
    tools = _make_tools()

    with pytest.raises(ToolLoopMaxIterationsError) as exc_info:
        await run_tool_loop(
            llm=llm,
            tools=tools,
            messages=_base_messages(),
            system_prompt="Prompt",
            tool_schemas=[{"name": "mytool"}],
            max_iterations=3,
            circuit_breaker_threshold=10,
            agent_id="agent",
        )

    error = exc_info.value
    # tool-only response → last_response es "" (sin text_blocks)
    assert error.last_response == ""


async def test_max_iterations_last_response_is_the_last_llm_output():
    """El .last_response del error debe ser la última respuesta del LLM (no una anterior)."""
    responses = [
        _tool_call_response("tool_a"),
        _tool_call_response("tool_b"),
        _tool_call_response("tool_c"),  # <-- ésta debe ser last_response
    ]

    llm = _make_llm(*responses)
    tools = _make_tools()

    with pytest.raises(ToolLoopMaxIterationsError) as exc_info:
        await run_tool_loop(
            llm=llm,
            tools=tools,
            messages=_base_messages(),
            system_prompt="Prompt",
            tool_schemas=[{"name": "mytool"}],
            max_iterations=3,
            circuit_breaker_threshold=10,
            agent_id="agent",
        )

    # tool-only response → last_response es "" (sin text_blocks)
    assert exc_info.value.last_response == ""


async def test_max_iterations_one_iteration():
    """max_iterations=1 → si el único turno devuelve tool calls → raise."""
    tool_call = _tool_call_response("mytool")

    llm = _make_llm(tool_call)
    tools = _make_tools()

    with pytest.raises(ToolLoopMaxIterationsError) as exc_info:
        await run_tool_loop(
            llm=llm,
            tools=tools,
            messages=_base_messages(),
            system_prompt="Prompt",
            tool_schemas=[{"name": "mytool"}],
            max_iterations=1,
            circuit_breaker_threshold=10,
            agent_id="agent",
        )

    # tool-only response → last_response es "" (sin text_blocks)
    assert exc_info.value.last_response == ""


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


async def test_circuit_breaker_blocks_after_threshold_failures():
    """Tras threshold fallos, el circuit breaker bloquea la tool y reporta CIRCUIT OPEN."""
    failing_result = ToolResult(tool_name="flaky", output="error", success=False)
    tool_call = _tool_call_response("flaky")
    threshold = 2

    # El LLM siempre pide la misma tool (vamos a llegar al breaker antes de max_iter)
    llm = _make_llm(tool_call, tool_call, tool_call, "Respuesta final")
    tools = AsyncMock()
    tools.execute = AsyncMock(return_value=failing_result)

    result = await run_tool_loop(
        llm=llm,
        tools=tools,
        messages=_base_messages(),
        system_prompt="Prompt",
        tool_schemas=[{"name": "flaky"}],
        max_iterations=10,
        circuit_breaker_threshold=threshold,
        agent_id="agent",
    )

    # Después del circuit breaker abierto, el LLM recibe el mensaje CIRCUIT OPEN
    # y finalmente responde sin tool calls
    assert result == "Respuesta final"


async def test_circuit_breaker_does_not_execute_tripped_tool():
    """Una tool en circuito abierto NO se ejecuta (tools.execute no se llama)."""
    failing_result = ToolResult(
        tool_name="flaky", output="error", success=False, retryable=False,
    )
    tool_call = _tool_call_response("flaky")
    threshold = 1  # trip tras 1 fallo no-retryable

    # Iteración 1: fallo no-retryable → trip
    # Iteración 2: bloqueado, mensaje CIRCUIT OPEN al LLM
    # Iteración 3: LLM responde sin tools
    llm = _make_llm(tool_call, tool_call, "Respuesta sin tools")
    tools = AsyncMock()
    tools.execute = AsyncMock(return_value=failing_result)

    await run_tool_loop(
        llm=llm,
        tools=tools,
        messages=_base_messages(),
        system_prompt="Prompt",
        tool_schemas=[{"name": "flaky"}],
        max_iterations=10,
        circuit_breaker_threshold=threshold,
        agent_id="agent",
    )

    # Threshold=1 → 1 fallo → trip. Solo se ejecuta 1 vez (la que falla).
    assert tools.execute.call_count == 1


async def test_circuit_breaker_not_tripped_for_retryable_errors():
    """Errores retryable (default) nunca disparan el circuit breaker."""
    failing_result = ToolResult(tool_name="flaky", output="error", success=False)
    tool_call = _tool_call_response("flaky")

    llm = _make_llm(tool_call, tool_call, "Final")
    tools = AsyncMock()
    tools.execute = AsyncMock(return_value=failing_result)

    result = await run_tool_loop(
        llm=llm,
        tools=tools,
        messages=_base_messages(),
        system_prompt="Prompt",
        tool_schemas=[{"name": "flaky"}],
        max_iterations=10,
        circuit_breaker_threshold=3,
        agent_id="agent",
    )

    assert result == "Final"
    # 2 fallos, threshold=3 → no tripped → ambas ejecuciones ocurren
    assert tools.execute.call_count == 2


async def test_circuit_breaker_resets_on_success():
    """Una tool que falla y luego tiene éxito resetea el contador de fallos."""
    success_result = ToolResult(tool_name="mytool", output="ok", success=True)
    fail_result = ToolResult(tool_name="mytool", output="error", success=False)
    tool_call = _tool_call_response("mytool")

    # fallo → éxito → fallo → éxito → sin tools
    llm = _make_llm(tool_call, tool_call, tool_call, tool_call, "Final")
    tools = AsyncMock()
    tools.execute = AsyncMock(
        side_effect=[fail_result, success_result, fail_result, success_result]
    )

    result = await run_tool_loop(
        llm=llm,
        tools=tools,
        messages=_base_messages(),
        system_prompt="Prompt",
        tool_schemas=[{"name": "mytool"}],
        max_iterations=10,
        circuit_breaker_threshold=2,
        agent_id="agent",
    )

    assert result == "Final"
    assert tools.execute.call_count == 4


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_empty_tool_schemas_passes_none_to_llm():
    """Con tool_schemas=[] → el LLM recibe tools=None (no lista vacía)."""
    llm = _make_llm("Respuesta")
    tools = _make_tools()

    await run_tool_loop(
        llm=llm,
        tools=tools,
        messages=_base_messages(),
        system_prompt="Prompt",
        tool_schemas=[],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="agent",
    )

    call_kwargs = llm.complete.call_args
    # El tercer argumento (tools=) debe ser None cuando la lista está vacía
    assert call_kwargs.kwargs.get("tools") is None or (
        len(call_kwargs.args) >= 3 and call_kwargs.args[2] is None
    )


async def test_malformed_json_args_fall_back_to_empty_kwargs():
    """Si los argumentos de la tool no son JSON válido, la tool se llama con kwargs vacíos."""
    raw_call = LLMResponse(
        text_blocks=[],
        tool_calls=[
            {
                "function": {
                    "name": "mytool",
                    "arguments": "no-es-json",
                }
            }
        ],
        raw="",
    )

    llm = _make_llm(raw_call, "Final")
    tools = _make_tools()

    result = await run_tool_loop(
        llm=llm,
        tools=tools,
        messages=_base_messages(),
        system_prompt="Prompt",
        tool_schemas=[{"name": "mytool"}],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="agent",
    )

    assert result == "Final"
    tools.execute.assert_called_once_with("mytool")
