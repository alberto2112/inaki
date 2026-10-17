"""Enforcement de tools privilegiadas en el tool loop (feature ``config-set-efimero``).

La tool DECLARA ``requires_operator``, el canal DECIDE ``es_operador`` y el
kernel HACE CUMPLIR: ``run_tool_loop`` rechaza una privilegiada en un turno sin
operador ANTES de ejecutarla. La ejecución es la única puerta que nadie esquiva
(el page-in deja llamar cualquier tool del registry aunque el routing no la muestre).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from inaki.kernel._tool_loop import _RECHAZO_PRIVILEGIO, _rechazo_privilegio, run_tool_loop
from inaki.kernel.domain.llm_response import LLMResponse
from inaki.kernel.ports.tool_port import ToolResult
from inaki.shared.message import Message, Role


def _llamada(nombre: str, call_id: str = "c1", **args: Any) -> dict:
    return {"id": call_id, "function": {"name": nombre, "arguments": json.dumps(args)}}


def _executor(
    privilegiadas: set[str], *, protegidas: dict[str, set[str]] | None = None
) -> MagicMock:
    """``privilegiadas``: tools enteras. ``protegidas``: ``{tool: {operaciones}}`` — el
    veredicto depende de la operación que viaja en los kwargs de la llamada."""
    protegidas = protegidas or {}
    tools = MagicMock()
    tools.requires_operator.side_effect = lambda nombre, kwargs: (
        nombre in privilegiadas or kwargs.get("operation") in protegidas.get(nombre, set())
    )
    tools.operation_of.side_effect = lambda nombre, kwargs: kwargs.get("operation")
    tools.execute = AsyncMock(
        side_effect=lambda nombre, **kw: ToolResult(tool_name=nombre, output="hecho", success=True)
    )
    return tools


def _llm(*respuestas: LLMResponse) -> MagicMock:
    llm = MagicMock()
    llm.thinking_active = False
    llm.complete = AsyncMock(side_effect=list(respuestas))
    return llm


async def _correr(llm, tools, *, turno_de_operador: bool | None = None, **extra) -> str:
    kwargs: dict[str, Any] = (
        {} if turno_de_operador is None else {"turno_de_operador": turno_de_operador}
    )
    return await run_tool_loop(
        llm=llm,
        tools=tools,
        messages=[Message(role=Role.USER, content="hola")],
        system_prompt="sp",
        tool_schemas=extra.pop("tool_schemas", [{"function": {"name": "config_set"}}]),
        max_iterations=extra.pop("max_iterations", 6),
        circuit_breaker_threshold=extra.pop("circuit_breaker_threshold", 3),
        agent_id="a",
        **kwargs,
        **extra,
    )


async def test_privilegiada_sin_operador_no_ejecuta_y_rechaza():
    tools = _executor({"config_set"})
    llm = _llm(
        LLMResponse(text_blocks=[], tool_calls=[_llamada("config_set")]),
        LLMResponse.of_text("eso lo pide el operador"),
    )
    tool_trace: list[Message] = []

    respuesta = await _correr(llm, tools, turno_de_operador=False, tool_trace=tool_trace)

    assert respuesta == "eso lo pide el operador"
    tools.execute.assert_not_called()
    resultados = [m for m in tool_trace if m.role == Role.TOOL]
    assert [m.content for m in resultados] == [_rechazo_privilegio("config_set", None)]
    assert "NO la vuelvas a llamar" in _RECHAZO_PRIVILEGIO


async def test_default_es_sin_operador():
    """Sin el kwarg el turno NO es de operador: todo camino sin canal falla cerrado."""
    tools = _executor({"config_set"})
    llm = _llm(
        LLMResponse(text_blocks=[], tool_calls=[_llamada("config_set")]),
        LLMResponse.of_text("fin"),
    )

    await _correr(llm, tools)

    tools.execute.assert_not_called()


async def test_privilegiada_con_operador_se_ejecuta():
    tools = _executor({"config_set"})
    llm = _llm(
        LLMResponse(text_blocks=[], tool_calls=[_llamada("config_set")]),
        LLMResponse.of_text("listo"),
    )

    respuesta = await _correr(llm, tools, turno_de_operador=True)

    assert respuesta == "listo"
    tools.execute.assert_awaited_once_with("config_set")


async def test_tool_comun_se_ejecuta_sin_operador():
    tools = _executor({"config_set"})
    llm = _llm(
        LLMResponse(text_blocks=[], tool_calls=[_llamada("web_search")]),
        LLMResponse.of_text("listo"),
    )

    await _correr(llm, tools, turno_de_operador=False, tool_schemas=[])

    tools.execute.assert_awaited_once_with("web_search")


async def test_rechazo_es_no_retryable_y_abre_el_circuit_breaker():
    """Tres intentos de la privilegiada abren el breaker: la 4ª llamada ya no
    llega ni al chequeo de privilegio (CIRCUIT OPEN)."""
    tools = _executor({"config_set"})
    llm = _llm(
        *[
            LLMResponse(text_blocks=[], tool_calls=[_llamada("config_set", f"c{i}")])
            for i in range(4)
        ],
        LLMResponse.of_text("me rindo"),
    )
    tool_trace: list[Message] = []

    await _correr(
        llm,
        tools,
        turno_de_operador=False,
        tool_trace=tool_trace,
        circuit_breaker_threshold=3,
        max_iterations=10,
    )

    tools.execute.assert_not_called()
    contenidos = [m.content for m in tool_trace if m.role == Role.TOOL]
    assert contenidos[:3] == [_rechazo_privilegio("config_set", None)] * 3
    assert contenidos[3].startswith("CIRCUIT OPEN")


async def test_rechazo_se_persiste_por_persist_message():
    tools = _executor({"config_set"})
    llm = _llm(
        LLMResponse(text_blocks=[], tool_calls=[_llamada("config_set")]),
        LLMResponse.of_text("fin"),
    )
    persistidos: list[Message] = []

    async def persist(msg: Message) -> None:
        persistidos.append(msg)

    await _correr(llm, tools, turno_de_operador=False, persist_message=persist)

    tool_msgs = [m for m in persistidos if m.role == Role.TOOL]
    assert [m.content for m in tool_msgs] == [_rechazo_privilegio("config_set", None)]
    assert tool_msgs[0].tool_call_id == "c1"


async def test_page_in_no_saltea_el_chequeo():
    """La tool no está en el set visible (el routing no la mostró) pero existe en el
    registry: el page-in la agrega al set y aun así el chequeo la rechaza."""
    tools = _executor({"config_set"})
    llm = _llm(
        LLMResponse(text_blocks=[], tool_calls=[_llamada("config_set")]),
        LLMResponse.of_text("fin"),
    )
    schema_paginado = {"function": {"name": "config_set"}}
    visibles: list[dict] = [{"function": {"name": "otra"}}]

    await _correr(
        llm,
        tools,
        turno_de_operador=False,
        tool_schemas=visibles,
        page_in_schemas=[schema_paginado],
    )

    # el page-in SÍ ocurrió: la 2ª llamada al LLM ya ve el schema de la tool...
    assert schema_paginado in llm.complete.call_args_list[1].kwargs["tools"]
    # ...y aun así la ejecución quedó rechazada.
    tools.execute.assert_not_called()


async def test_trazas_del_rechazo():
    tools = _executor({"config_set"})
    llm = _llm(
        LLMResponse(text_blocks=[], tool_calls=[_llamada("config_set")]),
        LLMResponse.of_text("fin"),
    )
    eventos: list[tuple[str, dict]] = []
    tracer = MagicMock()
    tracer.trace.side_effect = lambda evento, **campos: eventos.append((evento, campos))

    await _correr(llm, tools, turno_de_operador=False, tracer=tracer)

    resultado = [c for e, c in eventos if e == "tool.result"]
    assert len(resultado) == 1
    assert resultado[0]["tool"] == "config_set"
    assert resultado[0]["success"] is False
    assert resultado[0]["output"] == _rechazo_privilegio("config_set", None)


def test_el_mensaje_de_rechazo_no_conoce_conceptos_de_canal():
    """``kernel-limpio``: "privado" es un concepto del canal (en REST el operador no está en
    un chat privado). El kernel solo sabe que el turno no es de operador."""
    for texto in (
        _RECHAZO_PRIVILEGIO.lower(),
        _rechazo_privilegio("config", "set").lower(),
        _rechazo_privilegio("config_set", None).lower(),
    ):
        assert "privad" not in texto and "private" not in texto
        assert "operador" in texto
    assert "NO la vuelvas a llamar" in _RECHAZO_PRIVILEGIO


# -- privilegio por operación ----------------------------------------------------------


def test_el_rechazo_nombra_la_operacion_si_la_hay():
    con_operacion = _rechazo_privilegio("config", "set")
    sin_operacion = _rechazo_privilegio("config", None)

    assert "'set'" in con_operacion and "'config'" in con_operacion
    assert "'config'" in sin_operacion and "'set'" not in sin_operacion
    assert "NO la vuelvas a llamar" in con_operacion and "NO la vuelvas a llamar" in sin_operacion


async def test_el_kernel_le_pasa_los_kwargs_al_veredicto():
    tools = _executor(set(), protegidas={"config": {"set"}})
    llm = _llm(
        LLMResponse(text_blocks=[], tool_calls=[_llamada("config", operation="get")]),
        LLMResponse.of_text("fin"),
    )

    await _correr(llm, tools, turno_de_operador=False, tool_schemas=[])

    tools.requires_operator.assert_called_once_with("config", {"operation": "get"})


async def test_operacion_protegida_sin_operador_se_rechaza_nombrandola():
    tools = _executor(set(), protegidas={"config": {"set"}})
    llm = _llm(
        LLMResponse(
            text_blocks=[], tool_calls=[_llamada("config", operation="set", path="llm.model")]
        ),
        LLMResponse.of_text("eso lo pide el operador"),
    )
    tool_trace: list[Message] = []

    await _correr(llm, tools, turno_de_operador=False, tool_schemas=[], tool_trace=tool_trace)

    tools.execute.assert_not_called()
    resultados = [m.content for m in tool_trace if m.role == Role.TOOL]
    assert resultados == [_rechazo_privilegio("config", "set")]
    assert "'set'" in resultados[0]


async def test_operacion_desbloqueada_se_ejecuta_sin_operador():
    tools = _executor(set(), protegidas={"config": {"set"}})
    llm = _llm(
        LLMResponse(
            text_blocks=[], tool_calls=[_llamada("config", operation="list", prefix="llm")]
        ),
        LLMResponse.of_text("listo"),
    )

    respuesta = await _correr(llm, tools, turno_de_operador=False, tool_schemas=[])

    assert respuesta == "listo"
    tools.execute.assert_awaited_once_with("config", operation="list", prefix="llm")


async def test_operacion_protegida_con_operador_se_ejecuta():
    tools = _executor(set(), protegidas={"config": {"set"}})
    llm = _llm(
        LLMResponse(
            text_blocks=[],
            tool_calls=[_llamada("config", operation="set", path="llm.model", value="x")],
        ),
        LLMResponse.of_text("listo"),
    )

    await _correr(llm, tools, turno_de_operador=True, tool_schemas=[])

    tools.execute.assert_awaited_once_with("config", operation="set", path="llm.model", value="x")
