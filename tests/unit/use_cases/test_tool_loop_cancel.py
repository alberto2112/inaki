"""Tests del kill-switch (/stop) en el tool loop (feature ``turn-kill-switch``).

La cancelación es MECÁNICA: el loop consulta ``IScopeRegistry.is_cancel_requested``
en el checkpoint A y antes de cada tool del batch — no depende de que el LLM
interprete un "para" en el contexto. Al cortar, una última llamada SIN tools
produce el resumen de cierre.
"""

from __future__ import annotations

import json

from unittest.mock import AsyncMock

from adapters.outbound.scope_registry_adapter import InMemoryScopeRegistryAdapter
from core.domain.entities.message import Message, Role
from core.domain.value_objects.llm_response import LLMResponse
from core.ports.outbound.scope_registry_port import Scope
from core.ports.outbound.tool_port import ToolResult
from core.use_cases._tool_loop import _CANCELLED_TOOL_RESULT, run_tool_loop

_SCOPE: Scope = ("agent1", "telegram", "chat1")


def _multi_tool_response(*names: str) -> LLMResponse:
    return LLMResponse(
        text_blocks=[],
        tool_calls=[
            {"id": f"call_{i}", "function": {"name": n, "arguments": json.dumps({})}}
            for i, n in enumerate(names)
        ],
        raw="",
    )


async def _busy_registry_with_cancel() -> InMemoryScopeRegistryAdapter:
    registry = InMemoryScopeRegistryAdapter()
    await registry.try_mark_busy(_SCOPE)
    await registry.request_cancel(_SCOPE)
    return registry


async def test_cancel_en_checkpoint_a_corta_antes_del_llm_y_hace_wrapup():
    """Con el flag ya seteado, el loop NO gasta una llamada de trabajo: va
    directo al wrap-up (una única llamada SIN tools)."""
    registry = await _busy_registry_with_cancel()
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=LLMResponse.of_text("Resumen: no llegué a empezar."))
    llm.thinking_active = False

    result = await run_tool_loop(
        llm=llm,
        tools=AsyncMock(),
        messages=[Message(role=Role.USER, content="investigá X")],
        system_prompt="x",
        tool_schemas=[],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="agent1",
        scope=_SCOPE,
        scope_registry=registry,
    )

    assert result == "Resumen: no llegué a empezar."
    llm.complete.assert_awaited_once()  # SOLO el wrap-up
    assert llm.complete.await_args.kwargs["tools"] is None


async def test_cancel_mid_batch_sintetiza_resultados_y_preserva_pairing():
    """Un /stop a mitad de un batch de 3 tools: la primera se ejecuta, las
    siguientes reciben el resultado sintético — cada tool_call_id queda
    emparejado (sin esto el provider tira 400 en el wrap-up)."""
    registry = InMemoryScopeRegistryAdapter()
    await registry.try_mark_busy(_SCOPE)

    llm = AsyncMock()
    llm.complete = AsyncMock(
        side_effect=[
            _multi_tool_response("search_a", "search_b", "search_c"),
            LLMResponse.of_text("Resumen: ejecuté search_a, el resto quedó sin correr."),
        ]
    )
    llm.thinking_active = False

    executed: list[str] = []

    async def exec_tool(tool_name, **kwargs):
        executed.append(tool_name)
        # El /stop llega mientras corre la PRIMERA tool.
        await registry.request_cancel(_SCOPE)
        return ToolResult(tool_name=tool_name, output="resultado", success=True)

    tools = AsyncMock()
    tools.execute = AsyncMock(side_effect=exec_tool)

    trace: list[Message] = []
    result = await run_tool_loop(
        llm=llm,
        tools=tools,
        messages=[Message(role=Role.USER, content="investigá")],
        system_prompt="x",
        tool_schemas=[],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="agent1",
        scope=_SCOPE,
        scope_registry=registry,
        tool_trace=trace,
    )

    assert executed == ["search_a"]  # b y c NO se ejecutaron
    # Pairing intacto: 3 tool results en el trace (1 real + 2 sintéticos).
    tool_msgs = [m for m in trace if m.role == Role.TOOL]
    assert len(tool_msgs) == 3
    assert tool_msgs[1].content == _CANCELLED_TOOL_RESULT
    assert tool_msgs[2].content == _CANCELLED_TOOL_RESULT
    assert {m.tool_call_id for m in tool_msgs} == {"call_0", "call_1", "call_2"}
    assert "Resumen" in result


async def test_wrapup_fallido_devuelve_cierre_fijo():
    """Si el provider falla en la llamada de cierre, el turno termina igual
    (que es lo que el usuario pidió) con un texto fijo."""
    registry = await _busy_registry_with_cancel()
    llm = AsyncMock()
    llm.complete = AsyncMock(side_effect=RuntimeError("provider down"))
    llm.thinking_active = False

    result = await run_tool_loop(
        llm=llm,
        tools=AsyncMock(),
        messages=[Message(role=Role.USER, content="x")],
        system_prompt="x",
        tool_schemas=[],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="agent1",
        scope=_SCOPE,
        scope_registry=registry,
    )

    assert "detenida" in result


async def test_sin_registry_el_loop_es_legacy():
    """Sin scope_registry (one-shot, tests viejos) no hay kill-switch: el loop
    corre exactamente como antes."""
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=LLMResponse.of_text("respuesta normal"))
    llm.thinking_active = False

    result = await run_tool_loop(
        llm=llm,
        tools=AsyncMock(),
        messages=[Message(role=Role.USER, content="hola")],
        system_prompt="x",
        tool_schemas=[],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="agent1",
        scope=_SCOPE,  # scope sin registry → sin kill-switch
    )

    assert result == "respuesta normal"


async def test_cancel_sin_scope_es_inerte():
    """Registry presente pero sin scope (defensa): no hay chequeo posible."""
    registry = InMemoryScopeRegistryAdapter()
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=LLMResponse.of_text("ok"))
    llm.thinking_active = False

    result = await run_tool_loop(
        llm=llm,
        tools=AsyncMock(),
        messages=[Message(role=Role.USER, content="hola")],
        system_prompt="x",
        tool_schemas=[],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="agent1",
        scope_registry=registry,
    )

    assert result == "ok"
