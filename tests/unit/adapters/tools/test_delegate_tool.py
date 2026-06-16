"""
Tests unitarios para adapters/outbound/tools/delegate_tool.py.

Cobertura de requisitos:
- REQ-DG-2: target_not_allowed — allow-list check ANTES de tocar el registry.
- REQ-DG-3: unknown_agent — registry lookup retorna None.
- REQ-DG-4: happy path — child retorna JSON block válido → DelegationResult(status="success").
- REQ-DG-5: result_parse_error — texto sin bloque JSON / JSON inválido.
- REQ-DG-6: timeout → DelegationResult(reason="timeout").
- REQ-DG-8: NUNCA propaga — cada failure mode retorna ToolResult, no lanza.
- max_iterations_exceeded: ToolLoopMaxIterationsError → max_iterations_exceeded.
- child_exception:<Type>: Exception genérica → reason con tipo incluido.
- Canonical reason strings match del design doc.
- Wire check: args correctos pasados al child one-shot.
- Round-trip: ToolResult.output es JSON válido para DelegationResult.model_validate_json.
- Task 6.2: _RESULT_FORMAT_FOOTER SIEMPRE presente en el system_prompt pasado al hijo.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from unittest.mock import AsyncMock, MagicMock

from adapters.outbound.tools.delegate_tool import DelegateTool, _RESULT_FORMAT_FOOTER
from core.domain.errors import ToolLoopMaxIterationsError
from core.domain.value_objects.channel_context import ChannelContext
from core.domain.value_objects.delegation_result import DelegationResult
from core.ports.outbound.tool_port import ToolResult


# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------

_ALLOWED_TARGETS = ["specialist", "researcher"]
_MAX_ITERATIONS = 5
_TIMEOUT_SECONDS = 30
_AGENT_ID = "specialist"


def _make_child_container(
    one_shot_response: str = "response",
    default_system_prompt: str = "Default child system prompt.",
) -> MagicMock:
    """Crea un mock de AgentContainer con run_agent_one_shot configurado.

    DelegateTool accede a container.run_agent_one_shot (el use case) y luego
    llama await child_one_shot.execute(...), por lo que run_agent_one_shot debe
    ser un objeto con un método execute() asíncrono.

    Flujo C: ``build_child(agent_id)`` devuelve directamente ``run_agent_one_shot``
    (la instancia efímera). Los tests inyectan
    ``build_child=MagicMock(return_value=container.run_agent_one_shot)``. El one-shot
    expone ``execute`` (await) y la property ``system_prompt`` (prompt default del sub,
    que DelegateTool lee cuando el caller no pasa uno).
    """
    container = MagicMock()
    use_case = MagicMock()
    use_case.execute = AsyncMock(return_value=one_shot_response)
    use_case.system_prompt = default_system_prompt
    container.run_agent_one_shot = use_case
    return container


def _make_tool(
    allowed_targets: list[str] | None = None,
    build_child=None,
    *,
    caller_container=None,
    queue=None,
) -> DelegateTool:
    """Factory de DelegateTool con defaults razonables para tests.

    Flujo C: el ctor recibe ``build_child`` (callable agent_id → one-shot efímero o
    None) en vez de ``get_agent_container``. Para los tests del path sync, ``build_child``
    es un ``MagicMock(return_value=<one-shot>)``. ``caller_container`` y ``queue`` se
    mockean — esos tests llaman ``wait=True`` y no tocan la cola ni el contexto de canal.
    """
    if allowed_targets is None:
        allowed_targets = _ALLOWED_TARGETS

    if build_child is None:
        container = _make_child_container()
        build_child = MagicMock(return_value=container.run_agent_one_shot)

    if caller_container is None:
        caller_container = MagicMock()
        caller_container.get_channel_context = MagicMock(return_value=None)

    if queue is None:
        queue = MagicMock()
        queue.enqueue = AsyncMock(return_value="bg-1")

    return DelegateTool(
        allowed_targets=allowed_targets,
        build_child=build_child,
        max_iterations_per_sub=_MAX_ITERATIONS,
        timeout_seconds=_TIMEOUT_SECONDS,
        caller_agent_id="caller",
        caller_container=caller_container,
        queue=queue,
    )


def _valid_json_response(
    status: str = "success",
    summary: str = "Task completed",
    details: str | None = None,
    reason: str | None = None,
) -> str:
    """Construye la respuesta completa del hijo con un bloque ```json``` válido al final."""
    data: dict = {"status": status, "summary": summary}
    if details is not None:
        data["details"] = details
    if reason is not None:
        data["reason"] = reason
    json_block = f"```json\n{json.dumps(data)}\n```"
    return f"Some narrative from the child.\n\n{json_block}"


# ---------------------------------------------------------------------------
# REQ-DG-4 — Happy path: child retorna JSON block válido
# ---------------------------------------------------------------------------


async def test_happy_path_returns_success_delegation_result():
    """
    REQ-DG-4: Child retorna texto con bloque ```json``` válido.
    DelegateTool.execute retorna ToolResult con output que parsea a
    DelegationResult(status="success").
    """
    response = _valid_json_response(status="success", summary="Done")
    container = _make_child_container(one_shot_response=response)
    get_container = MagicMock(return_value=container.run_agent_one_shot)

    tool = _make_tool(build_child=get_container)
    result = await tool.execute(wait=True, agent_id=_AGENT_ID, task="Do the thing")

    assert isinstance(result, ToolResult)
    assert result.tool_name == "delegate"
    assert result.success is True

    dr = DelegationResult.model_validate_json(result.output)
    assert dr.status == "success"
    assert dr.summary == "Done"


# ---------------------------------------------------------------------------
# REQ-DG-2 — target_not_allowed
# ---------------------------------------------------------------------------


async def test_target_not_allowed_returns_structured_failure():
    """
    REQ-DG-2: agent_id no está en allowed_targets → reason exactamente "target_not_allowed".
    El registry NUNCA es llamado.
    """
    get_container = MagicMock()  # Si se llama, el test falla por verificación al final

    tool = DelegateTool(
        allowed_targets=["specialist"],
        build_child=get_container,
        max_iterations_per_sub=_MAX_ITERATIONS,
        timeout_seconds=_TIMEOUT_SECONDS,
        caller_agent_id="caller",
        caller_container=MagicMock(),
        queue=MagicMock(),
    )

    result = await tool.execute(wait=True, agent_id="evil", task="do something malicious")

    assert isinstance(result, ToolResult)
    assert result.success is False

    dr = DelegationResult.model_validate_json(result.output)
    assert dr.status == "failed"
    assert dr.reason == "target_not_allowed"

    # El registry NO debe haber sido llamado — REQ-DG-2
    get_container.assert_not_called()


async def test_target_not_allowed_empty_allowed_list_passes_all():
    """
    allowed_targets vacío → no hay restricción; cualquier agente registrado es válido.
    """
    container = _make_child_container(_valid_json_response())
    get_container = MagicMock(return_value=container.run_agent_one_shot)

    tool = DelegateTool(
        allowed_targets=[],  # vacío = sin restricción
        build_child=get_container,
        max_iterations_per_sub=_MAX_ITERATIONS,
        timeout_seconds=_TIMEOUT_SECONDS,
        caller_agent_id="caller",
        caller_container=MagicMock(),
        queue=MagicMock(),
    )

    result = await tool.execute(wait=True, agent_id="any-agent-id", task="some task")
    get_container.assert_called_once_with("any-agent-id")

    dr = DelegationResult.model_validate_json(result.output)
    # Puede ser success o parse_error dependiendo de la respuesta, pero NO target_not_allowed
    assert dr.reason != "target_not_allowed"


# ---------------------------------------------------------------------------
# REQ-DG-3 — unknown_agent
# ---------------------------------------------------------------------------


async def test_unknown_agent_returns_structured_failure():
    """
    REQ-DG-3: agent_id en allow-list pero registry retorna None → reason "unknown_agent".
    """
    get_container = MagicMock(return_value=None)

    tool = DelegateTool(
        allowed_targets=["known-agent", "ghost"],
        build_child=get_container,
        max_iterations_per_sub=_MAX_ITERATIONS,
        timeout_seconds=_TIMEOUT_SECONDS,
        caller_agent_id="caller",
        caller_container=MagicMock(),
        queue=MagicMock(),
    )

    result = await tool.execute(wait=True, agent_id="ghost", task="haunt something")

    assert isinstance(result, ToolResult)
    assert result.success is False

    dr = DelegationResult.model_validate_json(result.output)
    assert dr.status == "failed"
    assert dr.reason == "unknown_agent"


# ---------------------------------------------------------------------------
# REQ-DG-5 — result_parse_error
# ---------------------------------------------------------------------------


async def test_result_parse_error_no_json_block():
    """
    REQ-DG-5: Child retorna texto plano sin bloque ```json``` →
    reason exactamente "result_parse_error".
    """
    plain_text = "I did the thing but forgot to format the result."
    container = _make_child_container(one_shot_response=plain_text)

    tool = _make_tool(build_child=MagicMock(return_value=container.run_agent_one_shot))
    result = await tool.execute(wait=True, agent_id=_AGENT_ID, task="task")

    dr = DelegationResult.model_validate_json(result.output)
    assert dr.status == "failed"
    assert dr.reason == "result_parse_error"
    assert dr.details == plain_text


async def test_result_parse_error_invalid_json_in_block():
    """
    REQ-DG-5: Child retorna un bloque ```json``` con JSON inválido →
    reason exactamente "result_parse_error".
    """
    bad_json_response = "Some output\n```json\n{not: valid json!!}\n```"
    container = _make_child_container(one_shot_response=bad_json_response)

    tool = _make_tool(build_child=MagicMock(return_value=container.run_agent_one_shot))
    result = await tool.execute(wait=True, agent_id=_AGENT_ID, task="task")

    dr = DelegationResult.model_validate_json(result.output)
    assert dr.status == "failed"
    assert dr.reason == "result_parse_error"


# ---------------------------------------------------------------------------
# REQ-DG-6 — timeout
# ---------------------------------------------------------------------------


async def test_timeout_error_mapped_to_delegation_result():
    """
    REQ-DG-6: Child execute lanza asyncio.TimeoutError →
    reason exactamente "timeout".
    DelegateTool NO envuelve en wait_for — el TimeoutError viene del use case.
    """
    container = MagicMock()
    use_case = MagicMock()
    use_case.execute = AsyncMock(side_effect=asyncio.TimeoutError())
    container.run_agent_one_shot = use_case

    tool = _make_tool(build_child=MagicMock(return_value=container.run_agent_one_shot))
    result = await tool.execute(wait=True, agent_id=_AGENT_ID, task="slow task")

    assert isinstance(result, ToolResult)
    assert result.success is False

    dr = DelegationResult.model_validate_json(result.output)
    assert dr.status == "failed"
    assert dr.reason == "timeout"


# ---------------------------------------------------------------------------
# max_iterations_exceeded
# ---------------------------------------------------------------------------


async def test_max_iterations_exceeded_mapped_to_delegation_result():
    """
    ToolLoopMaxIterationsError del use case hijo →
    reason exactamente "max_iterations_exceeded".
    """
    container = MagicMock()
    use_case = MagicMock()
    use_case.execute = AsyncMock(
        side_effect=ToolLoopMaxIterationsError(last_response="partial answer")
    )
    container.run_agent_one_shot = use_case

    tool = _make_tool(build_child=MagicMock(return_value=container.run_agent_one_shot))
    result = await tool.execute(wait=True, agent_id=_AGENT_ID, task="complex task")

    assert isinstance(result, ToolResult)
    assert result.success is False

    dr = DelegationResult.model_validate_json(result.output)
    assert dr.status == "failed"
    assert dr.reason == "max_iterations_exceeded"


# ---------------------------------------------------------------------------
# child_exception:<Type> — REQ-DG-8
# ---------------------------------------------------------------------------


async def test_child_runtime_error_mapped_to_delegation_result():
    """
    REQ-DG-8: Child execute lanza RuntimeError →
    reason "child_exception:RuntimeError", details contiene el mensaje.
    """
    container = MagicMock()
    use_case = MagicMock()
    use_case.execute = AsyncMock(side_effect=RuntimeError("boom"))
    container.run_agent_one_shot = use_case

    tool = _make_tool(build_child=MagicMock(return_value=container.run_agent_one_shot))
    result = await tool.execute(wait=True, agent_id=_AGENT_ID, task="risky task")

    assert isinstance(result, ToolResult)
    assert result.success is False

    dr = DelegationResult.model_validate_json(result.output)
    assert dr.status == "failed"
    assert dr.reason == "child_exception:RuntimeError"
    assert dr.details is not None
    assert "boom" in dr.details


async def test_child_value_error_includes_type_in_reason():
    """Cualquier tipo de excepción usa el nombre de clase en el reason."""
    container = MagicMock()
    use_case = MagicMock()
    use_case.execute = AsyncMock(side_effect=ValueError("bad value"))
    container.run_agent_one_shot = use_case

    tool = _make_tool(build_child=MagicMock(return_value=container.run_agent_one_shot))
    result = await tool.execute(wait=True, agent_id=_AGENT_ID, task="task")

    dr = DelegationResult.model_validate_json(result.output)
    assert dr.reason == "child_exception:ValueError"


# ---------------------------------------------------------------------------
# REQ-DG-8 — Never-raises guarantee
# ---------------------------------------------------------------------------


async def test_never_raises_target_not_allowed():
    """DelegateTool.execute no propaga cuando agent_id no está en allow-list."""
    tool = DelegateTool(
        allowed_targets=["only-this"],
        build_child=MagicMock(return_value=None),
        max_iterations_per_sub=5,
        timeout_seconds=10,
        caller_agent_id="caller",
        caller_container=MagicMock(),
        queue=MagicMock(),
    )
    result = await tool.execute(wait=True, agent_id="other", task="task")
    assert isinstance(result, ToolResult)


async def test_never_raises_unknown_agent():
    """DelegateTool.execute no propaga cuando el registry retorna None."""
    tool = _make_tool(build_child=MagicMock(return_value=None))
    # unknown_agent también en allow-list
    tool._allowed_targets = ["specialist"]
    result = await tool.execute(wait=True, agent_id=_AGENT_ID, task="task")
    assert isinstance(result, ToolResult)


async def test_never_raises_timeout():
    """DelegateTool.execute no propaga asyncio.TimeoutError."""
    container = MagicMock()
    use_case = MagicMock()
    use_case.execute = AsyncMock(side_effect=asyncio.TimeoutError())
    container.run_agent_one_shot = use_case
    tool = _make_tool(build_child=MagicMock(return_value=container.run_agent_one_shot))
    result = await tool.execute(wait=True, agent_id=_AGENT_ID, task="task")
    assert isinstance(result, ToolResult)


async def test_never_raises_max_iterations():
    """DelegateTool.execute no propaga ToolLoopMaxIterationsError."""
    container = MagicMock()
    use_case = MagicMock()
    use_case.execute = AsyncMock(side_effect=ToolLoopMaxIterationsError(last_response="x"))
    container.run_agent_one_shot = use_case
    tool = _make_tool(build_child=MagicMock(return_value=container.run_agent_one_shot))
    result = await tool.execute(wait=True, agent_id=_AGENT_ID, task="task")
    assert isinstance(result, ToolResult)


async def test_never_raises_child_exception():
    """DelegateTool.execute no propaga Exception genérica del child."""
    container = MagicMock()
    use_case = MagicMock()
    use_case.execute = AsyncMock(side_effect=RuntimeError("boom"))
    container.run_agent_one_shot = use_case
    tool = _make_tool(build_child=MagicMock(return_value=container.run_agent_one_shot))
    result = await tool.execute(wait=True, agent_id=_AGENT_ID, task="task")
    assert isinstance(result, ToolResult)


async def test_never_raises_parse_error():
    """DelegateTool.execute no propaga cuando el child retorna texto sin bloque JSON."""
    container = _make_child_container(one_shot_response="plain text, no json block")
    tool = _make_tool(build_child=MagicMock(return_value=container.run_agent_one_shot))
    result = await tool.execute(wait=True, agent_id=_AGENT_ID, task="task")
    assert isinstance(result, ToolResult)


# ---------------------------------------------------------------------------
# Wire check — args correctos pasados al child one-shot (ayuda a task 5.1)
# ---------------------------------------------------------------------------


async def test_passes_correct_args_to_child_one_shot():
    """
    Wire check (task 6.2): DelegateTool pasa task, max_iterations y timeout_seconds
    correctamente a child.run_agent_one_shot.execute. El system_prompt recibido debe
    contener el override del caller Y el footer de formato de resultado.
    """
    response = _valid_json_response()
    container = _make_child_container(one_shot_response=response)
    get_container = MagicMock(return_value=container.run_agent_one_shot)

    tool = DelegateTool(
        allowed_targets=["specialist"],
        build_child=get_container,
        max_iterations_per_sub=7,
        timeout_seconds=45,
        caller_agent_id="caller",
        caller_container=MagicMock(),
        queue=MagicMock(),
    )

    await tool.execute(
        wait=True,
        agent_id="specialist",
        task="Analyze this dataset",
        system_prompt="You are a data analyst",
    )

    container.run_agent_one_shot.execute.assert_awaited_once()
    call_kwargs = container.run_agent_one_shot.execute.await_args.kwargs
    assert call_kwargs["task"] == "Analyze this dataset"
    assert call_kwargs["max_iterations"] == 7
    assert call_kwargs["timeout_seconds"] == 45
    # system_prompt must contain both the caller's override and the footer
    effective = call_kwargs["system_prompt"]
    assert "You are a data analyst" in effective
    assert _RESULT_FORMAT_FOOTER in effective


async def test_passes_child_default_plus_footer_when_no_system_prompt_provided():
    """
    Task 6.2: cuando system_prompt no se pasa, el child recibe su default prompt
    + _RESULT_FORMAT_FOOTER como effective_system_prompt (no None).
    """
    response = _valid_json_response()
    container = _make_child_container(
        one_shot_response=response,
        default_system_prompt="Base child prompt.",
    )

    tool = DelegateTool(
        allowed_targets=["specialist"],
        build_child=MagicMock(return_value=container.run_agent_one_shot),
        max_iterations_per_sub=5,
        timeout_seconds=30,
        caller_agent_id="caller",
        caller_container=MagicMock(),
        queue=MagicMock(),
    )

    await tool.execute(wait=True, agent_id="specialist", task="Do something")

    container.run_agent_one_shot.execute.assert_awaited_once()
    call_kwargs = container.run_agent_one_shot.execute.await_args.kwargs
    effective = call_kwargs["system_prompt"]
    assert effective is not None
    assert "Base child prompt." in effective
    assert _RESULT_FORMAT_FOOTER in effective


# ---------------------------------------------------------------------------
# Return shape — round-trip JSON
# ---------------------------------------------------------------------------


async def test_output_is_valid_json_round_trip():
    """
    REQ-DG-4: ToolResult.output es un JSON string que DelegationResult.model_validate_json
    puede round-tripear correctamente.
    """
    response = _valid_json_response(
        status="success",
        summary="Task done",
        details="Detailed output here",
    )
    container = _make_child_container(one_shot_response=response)
    tool = _make_tool(build_child=MagicMock(return_value=container.run_agent_one_shot))

    result = await tool.execute(wait=True, agent_id=_AGENT_ID, task="task")

    # Debe ser JSON válido
    raw = json.loads(result.output)
    assert "status" in raw
    assert "summary" in raw

    # Round-trip via pydantic
    dr = DelegationResult.model_validate_json(result.output)
    assert dr.status == "success"
    assert dr.summary == "Task done"
    assert dr.details == "Detailed output here"


async def test_failed_result_output_is_valid_json():
    """ToolResult.output en paths de fallo también es JSON válido."""
    container = MagicMock()
    use_case = MagicMock()
    use_case.execute = AsyncMock(side_effect=asyncio.TimeoutError())
    container.run_agent_one_shot = use_case
    tool = _make_tool(build_child=MagicMock(return_value=container.run_agent_one_shot))

    result = await tool.execute(wait=True, agent_id=_AGENT_ID, task="task")

    dr = DelegationResult.model_validate_json(result.output)
    assert dr.status == "failed"
    assert dr.reason == "timeout"


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------


def test_tool_name_is_delegate():
    """El name de la tool es 'delegate' — referenciado por REQ-DG-9."""
    assert DelegateTool.name == "delegate"


def test_tool_schema_required_fields():
    """El schema de parámetros incluye agent_id y task como required."""
    schema = DelegateTool.parameters_schema
    assert "agent_id" in schema["required"]
    assert "task" in schema["required"]
    assert "system_prompt" not in schema["required"]  # opcional


def test_tool_schema_has_system_prompt_optional():
    """system_prompt está en properties pero NO en required."""
    schema = DelegateTool.parameters_schema
    assert "system_prompt" in schema["properties"]
    assert "system_prompt" not in schema.get("required", [])


# ---------------------------------------------------------------------------
# Canonical reason strings — verificación explícita (design table)
# ---------------------------------------------------------------------------


async def test_canonical_reason_strings_are_exact():
    """
    Verifica que los reason strings producidos coinciden EXACTAMENTE con la tabla
    del design doc. Ningún sinónimo, ninguna variación.

    Canonical table (design doc):
      target_not_allowed, unknown_agent, result_parse_error,
      timeout, max_iterations_exceeded, child_exception:<ExceptionType>
    """
    # target_not_allowed
    tool = DelegateTool(
        allowed_targets=["a"],
        build_child=MagicMock(),
        max_iterations_per_sub=5,
        timeout_seconds=10,
        caller_agent_id="caller",
        caller_container=MagicMock(),
        queue=MagicMock(),
    )
    r = await tool.execute(wait=True, agent_id="b", task="t")
    assert DelegationResult.model_validate_json(r.output).reason == "target_not_allowed"

    # unknown_agent
    tool2 = DelegateTool(
        allowed_targets=["b"],
        build_child=MagicMock(return_value=None),
        max_iterations_per_sub=5,
        timeout_seconds=10,
        caller_agent_id="caller",
        caller_container=MagicMock(),
        queue=MagicMock(),
    )
    r2 = await tool2.execute(wait=True, agent_id="b", task="t")
    assert DelegationResult.model_validate_json(r2.output).reason == "unknown_agent"

    # result_parse_error (no block)
    container_no_block = _make_child_container("plain text no json")
    tool3 = DelegateTool(
        allowed_targets=["b"],
        build_child=MagicMock(return_value=container_no_block.run_agent_one_shot),
        max_iterations_per_sub=5,
        timeout_seconds=10,
        caller_agent_id="caller",
        caller_container=MagicMock(),
        queue=MagicMock(),
    )
    r3 = await tool3.execute(wait=True, agent_id="b", task="t")
    assert DelegationResult.model_validate_json(r3.output).reason == "result_parse_error"

    # timeout
    container_timeout = MagicMock()
    uc_timeout = MagicMock()
    uc_timeout.execute = AsyncMock(side_effect=asyncio.TimeoutError())
    container_timeout.run_agent_one_shot = uc_timeout
    tool4 = DelegateTool(
        allowed_targets=["b"],
        build_child=MagicMock(return_value=container_timeout.run_agent_one_shot),
        max_iterations_per_sub=5,
        timeout_seconds=10,
        caller_agent_id="caller",
        caller_container=MagicMock(),
        queue=MagicMock(),
    )
    r4 = await tool4.execute(wait=True, agent_id="b", task="t")
    assert DelegationResult.model_validate_json(r4.output).reason == "timeout"

    # max_iterations_exceeded
    container_maxiter = MagicMock()
    uc_maxiter = MagicMock()
    uc_maxiter.execute = AsyncMock(side_effect=ToolLoopMaxIterationsError("x"))
    container_maxiter.run_agent_one_shot = uc_maxiter
    tool5 = DelegateTool(
        allowed_targets=["b"],
        build_child=MagicMock(return_value=container_maxiter.run_agent_one_shot),
        max_iterations_per_sub=5,
        timeout_seconds=10,
        caller_agent_id="caller",
        caller_container=MagicMock(),
        queue=MagicMock(),
    )
    r5 = await tool5.execute(wait=True, agent_id="b", task="t")
    assert DelegationResult.model_validate_json(r5.output).reason == "max_iterations_exceeded"

    # child_exception:<Type>
    container_exc = MagicMock()
    uc_exc = MagicMock()
    uc_exc.execute = AsyncMock(side_effect=KeyError("missing"))
    container_exc.run_agent_one_shot = uc_exc
    tool6 = DelegateTool(
        allowed_targets=["b"],
        build_child=MagicMock(return_value=container_exc.run_agent_one_shot),
        max_iterations_per_sub=5,
        timeout_seconds=10,
        caller_agent_id="caller",
        caller_container=MagicMock(),
        queue=MagicMock(),
    )
    r6 = await tool6.execute(wait=True, agent_id="b", task="t")
    assert DelegationResult.model_validate_json(r6.output).reason == "child_exception:KeyError"


# ---------------------------------------------------------------------------
# Task 6.2 — Result-format footer injection
# ---------------------------------------------------------------------------


async def test_footer_appended_when_system_prompt_is_none():
    """
    Task 6.2: cuando system_prompt=None, el child recibe
    child_default + '\\n\\n' + _RESULT_FORMAT_FOOTER.
    """
    response = _valid_json_response()
    container = _make_child_container(
        one_shot_response=response,
        default_system_prompt="Base child prompt.",
    )
    tool = _make_tool(build_child=MagicMock(return_value=container.run_agent_one_shot))

    await tool.execute(wait=True, agent_id=_AGENT_ID, task="do X")

    call_kwargs = container.run_agent_one_shot.execute.await_args.kwargs
    effective = call_kwargs["system_prompt"]
    assert "Base child prompt." in effective
    assert _RESULT_FORMAT_FOOTER in effective


async def test_footer_appended_when_system_prompt_is_provided():
    """
    Task 6.2: cuando system_prompt="Override prompt.", el child recibe
    override + '\\n\\n' + _RESULT_FORMAT_FOOTER.
    """
    response = _valid_json_response()
    container = _make_child_container(one_shot_response=response)
    tool = _make_tool(build_child=MagicMock(return_value=container.run_agent_one_shot))

    await tool.execute(
        wait=True,
        agent_id=_AGENT_ID,
        task="do X",
        system_prompt="Override prompt.",
    )

    call_kwargs = container.run_agent_one_shot.execute.await_args.kwargs
    effective = call_kwargs["system_prompt"]
    assert "Override prompt." in effective
    assert _RESULT_FORMAT_FOOTER in effective


async def test_footer_literal_substring_present():
    """
    Task 6.2: el footer contiene el substring literal que identifica el contrato
    de resultado. Esto hace auditable el texto del footer.
    """
    response = _valid_json_response()
    container = _make_child_container(one_shot_response=response)
    tool = _make_tool(build_child=MagicMock(return_value=container.run_agent_one_shot))

    await tool.execute(wait=True, agent_id=_AGENT_ID, task="do X")

    call_kwargs = container.run_agent_one_shot.execute.await_args.kwargs
    effective = call_kwargs["system_prompt"]
    assert "You MUST end your response with a fenced JSON block" in effective


@pytest.mark.parametrize("caller_prompt", [None, "Explicit override."])
async def test_footer_always_present_regardless_of_caller_prompt(caller_prompt: str | None):
    """
    Task 6.2 parametrizado: el footer siempre está presente, sea cual sea el
    valor de system_prompt que pasa el caller (None o un string explícito).
    """
    response = _valid_json_response()
    container = _make_child_container(
        one_shot_response=response,
        default_system_prompt="Default for parametrized test.",
    )
    tool = _make_tool(build_child=MagicMock(return_value=container.run_agent_one_shot))

    kwargs: dict = {"agent_id": _AGENT_ID, "task": "do X"}
    if caller_prompt is not None:
        kwargs["system_prompt"] = caller_prompt

    await tool.execute(wait=True, **kwargs)

    call_kwargs = container.run_agent_one_shot.execute.await_args.kwargs
    effective = call_kwargs["system_prompt"]
    assert _RESULT_FORMAT_FOOTER in effective


async def test_never_raises_on_child_build_error():
    """
    REQ-DG-8 (flujo C): si build_child lanza (config del sub rota → assemble_agent_config
    falla al resolver la instancia efímera), DelegateTool NO propaga — retorna un
    ToolResult failed con reason 'child_build_error:<Type>'.
    """

    def _boom(_agent_id):
        raise ValueError("config del sub inválida")

    tool = _make_tool(build_child=MagicMock(side_effect=_boom))
    result = await tool.execute(wait=True, agent_id=_AGENT_ID, task="task")

    assert isinstance(result, ToolResult)
    assert result.success is False
    dr = DelegationResult.model_validate_json(result.output)
    assert dr.status == "failed"
    assert dr.reason == "child_build_error:ValueError"
    assert dr.details is not None
    assert "inválida" in dr.details


# ---------------------------------------------------------------------------
# REQ-DG-10 — Async path (wait=false default) — Phase 4
# ---------------------------------------------------------------------------


def _make_async_tool(
    *,
    queue=None,
    caller_container=None,
    allowed_targets: list[str] | None = None,
) -> tuple[DelegateTool, MagicMock, MagicMock]:
    """Factory para tests del path async. Devuelve (tool, queue, caller_container)."""
    if queue is None:
        queue = MagicMock()
        queue.enqueue = AsyncMock(return_value="bg-1")
    if caller_container is None:
        caller_container = MagicMock()
        caller_container.get_channel_context = MagicMock(return_value=None)
    tool = DelegateTool(
        allowed_targets=allowed_targets if allowed_targets is not None else _ALLOWED_TARGETS,
        build_child=MagicMock(),
        max_iterations_per_sub=_MAX_ITERATIONS,
        timeout_seconds=_TIMEOUT_SECONDS,
        caller_agent_id="inaki",
        caller_container=caller_container,
        queue=queue,
    )
    return tool, queue, caller_container


async def test_default_async_invoca_queue_enqueue() -> None:
    """REQ-DG-10: sin `wait`, default async → `queue.enqueue` es invocado."""
    tool, queue, _ = _make_async_tool()

    await tool.execute(agent_id="researcher", task="investigá X")

    queue.enqueue.assert_awaited_once()
    kwargs = queue.enqueue.await_args.kwargs
    assert kwargs["caller_agent_id"] == "inaki"
    assert kwargs["target_agent_id"] == "researcher"
    assert kwargs["prompt"] == "investigá X"
    assert kwargs["system_prompt"] is None


async def test_default_async_devuelve_queued_tool_result() -> None:
    """REQ-DG-10: ToolResult tiene status=success, summary='Delegation queued', details=task_id."""
    queue = MagicMock()
    queue.enqueue = AsyncMock(return_value="bg-7")
    tool, _, _ = _make_async_tool(queue=queue)

    result = await tool.execute(agent_id="researcher", task="x")

    assert isinstance(result, ToolResult)
    assert result.success is True
    dr = DelegationResult.model_validate_json(result.output)
    assert dr.status == "success"
    assert dr.summary == "Delegation queued"
    assert dr.details == "bg-7"


async def test_default_async_completa_en_menos_de_50ms() -> None:
    """REQ-DG-10: latencia <50ms (el padre no espera al hijo)."""
    tool, _, _ = _make_async_tool()

    inicio = time.perf_counter()
    await tool.execute(agent_id="researcher", task="x")
    elapsed = time.perf_counter() - inicio

    assert elapsed < 0.050


async def test_async_propaga_canal_y_chat_del_channel_context() -> None:
    """Triangulación: cuando hay ChannelContext activo, sus campos se mapean a
    channel/chat_id del enqueue. ``ctx.channel_type → channel``."""
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="42")
    caller_container = MagicMock()
    caller_container.get_channel_context = MagicMock(return_value=ctx)

    tool, queue, _ = _make_async_tool(caller_container=caller_container)
    await tool.execute(agent_id="researcher", task="x")

    kwargs = queue.enqueue.await_args.kwargs
    assert kwargs["channel"] == "telegram"
    assert kwargs["chat_id"] == "42"


async def test_async_fallback_canal_vacio_sin_channel_context() -> None:
    """Triangulación: sin ChannelContext → channel='' chat_id='' (CLI/daemon)."""
    tool, queue, _ = _make_async_tool()  # default: get_channel_context → None

    await tool.execute(agent_id="researcher", task="x")

    kwargs = queue.enqueue.await_args.kwargs
    assert kwargs["channel"] == ""
    assert kwargs["chat_id"] == ""


async def test_async_aplica_allow_list_check() -> None:
    """REQ-DG-2 también aplica al path async: target fuera de allow-list → target_not_allowed."""
    tool, queue, _ = _make_async_tool(allowed_targets=["solo_este"])

    result = await tool.execute(agent_id="malicioso", task="x")

    queue.enqueue.assert_not_awaited()
    dr = DelegationResult.model_validate_json(result.output)
    assert dr.status == "failed"
    assert dr.reason == "target_not_allowed"


async def test_async_falla_grasiosamente_si_enqueue_lanza() -> None:
    """REQ-DG-8: nunca propaga. Si queue.enqueue lanza, retorna ToolResult failed."""
    queue = MagicMock()
    queue.enqueue = AsyncMock(side_effect=RuntimeError("cola rota"))
    tool, _, _ = _make_async_tool(queue=queue)

    result = await tool.execute(agent_id="researcher", task="x")

    assert isinstance(result, ToolResult)
    dr = DelegationResult.model_validate_json(result.output)
    assert dr.status == "failed"
    assert dr.reason == "enqueue_failed"
    assert "cola rota" in (dr.details or "")
