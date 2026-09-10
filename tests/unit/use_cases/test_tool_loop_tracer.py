"""El tool loop emite trazas en cada llamada al LLM y en cada tool — sin cambiar el flujo."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from inaki.kernel.domain.llm_response import LLMResponse
from inaki.kernel.ports.tool_port import ToolResult
from inaki.kernel.ports.turn_tracer_port import ITurnTracer
from inaki.kernel._tool_loop import run_tool_loop
from inaki.shared.message import Message, Role


class TracerEspia(ITurnTracer):
    def __init__(self) -> None:
        self.eventos: list[tuple[str, dict]] = []

    def bind(self, **context: object) -> ITurnTracer:
        return self

    def trace(self, event: str, **fields: object) -> None:
        self.eventos.append((event, fields))


def _tool_call(nombre: str, args: str = "{}") -> dict:
    return {"id": f"call-{nombre}", "function": {"name": nombre, "arguments": args}}


async def test_traza_llm_y_tools_en_orden() -> None:
    llm = MagicMock()
    llm.thinking_active = False
    llm.complete = AsyncMock(
        side_effect=[
            LLMResponse(text_blocks=["busco"], tool_calls=[_tool_call("web_search", '{"q": "x"}')]),
            LLMResponse.of_text("listo"),
        ]
    )
    tools = MagicMock()
    tools.execute = AsyncMock(
        return_value=ToolResult(tool_name="web_search", output="resultado", success=True)
    )
    espia = TracerEspia()

    respuesta = await run_tool_loop(
        llm=llm,
        tools=tools,
        messages=[Message(role=Role.USER, content="hola")],
        system_prompt="sp",
        tool_schemas=[{"function": {"name": "web_search"}}],
        max_iterations=5,
        circuit_breaker_threshold=3,
        agent_id="a",
        tracer=espia,
    )

    assert respuesta == "listo"
    assert [e for e, _ in espia.eventos] == [
        "llm.response",
        "tool.call",
        "tool.result",
        "llm.response",
    ]
    primera = espia.eventos[0][1]
    assert primera["iteration"] == 0 and primera["tool_calls"] == ["web_search"]
    llamada = espia.eventos[1][1]
    assert llamada["tool"] == "web_search" and llamada["args"] == {"q": "x"}
    resultado = espia.eventos[2][1]
    assert resultado["success"] is True and resultado["output"] == "resultado"


async def test_sin_tracer_no_cambia_nada() -> None:
    llm = MagicMock()
    llm.thinking_active = False
    llm.complete = AsyncMock(return_value=LLMResponse.of_text("ok"))

    respuesta = await run_tool_loop(
        llm=llm,
        tools=MagicMock(),
        messages=[Message(role=Role.USER, content="hola")],
        system_prompt="sp",
        tool_schemas=[],
        max_iterations=1,
        circuit_breaker_threshold=3,
        agent_id="a",
    )

    assert respuesta == "ok"
