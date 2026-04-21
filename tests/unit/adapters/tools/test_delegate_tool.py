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

import pytest
from unittest.mock import AsyncMock, MagicMock

from adapters.outbound.tools.delegate_tool import DelegateTool, _RESULT_FORMAT_FOOTER
from core.domain.errors import ToolLoopMaxIterationsError
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

    Task 6.2: también mockeamos container.agent_config.system_prompt para que
    DelegateTool pueda construir el effective_system_prompt cuando el caller
    no proporciona system_prompt.
    """
    container = MagicMock()
    use_case = MagicMock()
    use_case.execute = AsyncMock(return_value=one_shot_response)
    container.run_agent_one_shot = use_case
    container.agent_config.system_prompt = default_system_prompt
    return container


def _make_tool(
    allowed_targets: list[str] | None = None,
    get_agent_container=None,
) -> DelegateTool:
    """Factory de DelegateTool con defaults razonables para tests."""
    if allowed_targets is None:
        allowed_targets = _ALLOWED_TARGETS

    if get_agent_container is None:
        container = _make_child_container()
        get_agent_container = MagicMock(return_value=container)

    return DelegateTool(
        allowed_targets=allowed_targets,
        get_agent_container=get_agent_container,
        max_iterations_per_sub=_MAX_ITERATIONS,
        timeout_seconds=_TIMEOUT_SECONDS,
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
    get_container = MagicMock(return_value=container)

    tool = _make_tool(get_agent_container=get_container)
    result = await tool.execute(agent_id=_AGENT_ID, task="Do the thing")

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
        get_agent_container=get_container,
        max_iterations_per_sub=_MAX_ITERATIONS,
        timeout_seconds=_TIMEOUT_SECONDS,
    )

    result = await tool.execute(agent_id="evil", task="do something malicious")

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
    get_container = MagicMock(return_value=container)

    tool = DelegateTool(
        allowed_targets=[],  # vacío = sin restricción
        get_agent_container=get_container,
        max_iterations_per_sub=_MAX_ITERATIONS,
        timeout_seconds=_TIMEOUT_SECONDS,
    )

    result = await tool.execute(agent_id="any-agent-id", task="some task")
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
        get_agent_container=get_container,
        max_iterations_per_sub=_MAX_ITERATIONS,
        timeout_seconds=_TIMEOUT_SECONDS,
    )

    result = await tool.execute(agent_id="ghost", task="haunt something")

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

    tool = _make_tool(get_agent_container=MagicMock(return_value=container))
    result = await tool.execute(agent_id=_AGENT_ID, task="task")

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

    tool = _make_tool(get_agent_container=MagicMock(return_value=container))
    result = await tool.execute(agent_id=_AGENT_ID, task="task")

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

    tool = _make_tool(get_agent_container=MagicMock(return_value=container))
    result = await tool.execute(agent_id=_AGENT_ID, task="slow task")

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

    tool = _make_tool(get_agent_container=MagicMock(return_value=container))
    result = await tool.execute(agent_id=_AGENT_ID, task="complex task")

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

    tool = _make_tool(get_agent_container=MagicMock(return_value=container))
    result = await tool.execute(agent_id=_AGENT_ID, task="risky task")

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

    tool = _make_tool(get_agent_container=MagicMock(return_value=container))
    result = await tool.execute(agent_id=_AGENT_ID, task="task")

    dr = DelegationResult.model_validate_json(result.output)
    assert dr.reason == "child_exception:ValueError"


# ---------------------------------------------------------------------------
# REQ-DG-8 — Never-raises guarantee
# ---------------------------------------------------------------------------


async def test_never_raises_target_not_allowed():
    """DelegateTool.execute no propaga cuando agent_id no está en allow-list."""
    tool = DelegateTool(
        allowed_targets=["only-this"],
        get_agent_container=MagicMock(return_value=None),
        max_iterations_per_sub=5,
        timeout_seconds=10,
    )
    result = await tool.execute(agent_id="other", task="task")
    assert isinstance(result, ToolResult)


async def test_never_raises_unknown_agent():
    """DelegateTool.execute no propaga cuando el registry retorna None."""
    tool = _make_tool(get_agent_container=MagicMock(return_value=None))
    # unknown_agent también en allow-list
    tool._allowed_targets = ["specialist"]
    result = await tool.execute(agent_id=_AGENT_ID, task="task")
    assert isinstance(result, ToolResult)


async def test_never_raises_timeout():
    """DelegateTool.execute no propaga asyncio.TimeoutError."""
    container = MagicMock()
    use_case = MagicMock()
    use_case.execute = AsyncMock(side_effect=asyncio.TimeoutError())
    container.run_agent_one_shot = use_case
    tool = _make_tool(get_agent_container=MagicMock(return_value=container))
    result = await tool.execute(agent_id=_AGENT_ID, task="task")
    assert isinstance(result, ToolResult)


async def test_never_raises_max_iterations():
    """DelegateTool.execute no propaga ToolLoopMaxIterationsError."""
    container = MagicMock()
    use_case = MagicMock()
    use_case.execute = AsyncMock(side_effect=ToolLoopMaxIterationsError(last_response="x"))
    container.run_agent_one_shot = use_case
    tool = _make_tool(get_agent_container=MagicMock(return_value=container))
    result = await tool.execute(agent_id=_AGENT_ID, task="task")
    assert isinstance(result, ToolResult)


async def test_never_raises_child_exception():
    """DelegateTool.execute no propaga Exception genérica del child."""
    container = MagicMock()
    use_case = MagicMock()
    use_case.execute = AsyncMock(side_effect=RuntimeError("boom"))
    container.run_agent_one_shot = use_case
    tool = _make_tool(get_agent_container=MagicMock(return_value=container))
    result = await tool.execute(agent_id=_AGENT_ID, task="task")
    assert isinstance(result, ToolResult)


async def test_never_raises_parse_error():
    """DelegateTool.execute no propaga cuando el child retorna texto sin bloque JSON."""
    container = _make_child_container(one_shot_response="plain text, no json block")
    tool = _make_tool(get_agent_container=MagicMock(return_value=container))
    result = await tool.execute(agent_id=_AGENT_ID, task="task")
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
    get_container = MagicMock(return_value=container)

    tool = DelegateTool(
        allowed_targets=["specialist"],
        get_agent_container=get_container,
        max_iterations_per_sub=7,
        timeout_seconds=45,
    )

    await tool.execute(
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
        get_agent_container=MagicMock(return_value=container),
        max_iterations_per_sub=5,
        timeout_seconds=30,
    )

    await tool.execute(agent_id="specialist", task="Do something")

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
    tool = _make_tool(get_agent_container=MagicMock(return_value=container))

    result = await tool.execute(agent_id=_AGENT_ID, task="task")

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
    tool = _make_tool(get_agent_container=MagicMock(return_value=container))

    result = await tool.execute(agent_id=_AGENT_ID, task="task")

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
        get_agent_container=MagicMock(),
        max_iterations_per_sub=5,
        timeout_seconds=10,
    )
    r = await tool.execute(agent_id="b", task="t")
    assert DelegationResult.model_validate_json(r.output).reason == "target_not_allowed"

    # unknown_agent
    tool2 = DelegateTool(
        allowed_targets=["b"],
        get_agent_container=MagicMock(return_value=None),
        max_iterations_per_sub=5,
        timeout_seconds=10,
    )
    r2 = await tool2.execute(agent_id="b", task="t")
    assert DelegationResult.model_validate_json(r2.output).reason == "unknown_agent"

    # result_parse_error (no block)
    container_no_block = _make_child_container("plain text no json")
    tool3 = DelegateTool(
        allowed_targets=["b"],
        get_agent_container=MagicMock(return_value=container_no_block),
        max_iterations_per_sub=5,
        timeout_seconds=10,
    )
    r3 = await tool3.execute(agent_id="b", task="t")
    assert DelegationResult.model_validate_json(r3.output).reason == "result_parse_error"

    # timeout
    container_timeout = MagicMock()
    uc_timeout = MagicMock()
    uc_timeout.execute = AsyncMock(side_effect=asyncio.TimeoutError())
    container_timeout.run_agent_one_shot = uc_timeout
    tool4 = DelegateTool(
        allowed_targets=["b"],
        get_agent_container=MagicMock(return_value=container_timeout),
        max_iterations_per_sub=5,
        timeout_seconds=10,
    )
    r4 = await tool4.execute(agent_id="b", task="t")
    assert DelegationResult.model_validate_json(r4.output).reason == "timeout"

    # max_iterations_exceeded
    container_maxiter = MagicMock()
    uc_maxiter = MagicMock()
    uc_maxiter.execute = AsyncMock(side_effect=ToolLoopMaxIterationsError("x"))
    container_maxiter.run_agent_one_shot = uc_maxiter
    tool5 = DelegateTool(
        allowed_targets=["b"],
        get_agent_container=MagicMock(return_value=container_maxiter),
        max_iterations_per_sub=5,
        timeout_seconds=10,
    )
    r5 = await tool5.execute(agent_id="b", task="t")
    assert DelegationResult.model_validate_json(r5.output).reason == "max_iterations_exceeded"

    # child_exception:<Type>
    container_exc = MagicMock()
    uc_exc = MagicMock()
    uc_exc.execute = AsyncMock(side_effect=KeyError("missing"))
    container_exc.run_agent_one_shot = uc_exc
    tool6 = DelegateTool(
        allowed_targets=["b"],
        get_agent_container=MagicMock(return_value=container_exc),
        max_iterations_per_sub=5,
        timeout_seconds=10,
    )
    r6 = await tool6.execute(agent_id="b", task="t")
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
    tool = _make_tool(get_agent_container=MagicMock(return_value=container))

    await tool.execute(agent_id=_AGENT_ID, task="do X")

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
    tool = _make_tool(get_agent_container=MagicMock(return_value=container))

    await tool.execute(
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
    tool = _make_tool(get_agent_container=MagicMock(return_value=container))

    await tool.execute(agent_id=_AGENT_ID, task="do X")

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
    tool = _make_tool(get_agent_container=MagicMock(return_value=container))

    kwargs: dict = {"agent_id": _AGENT_ID, "task": "do X"}
    if caller_prompt is not None:
        kwargs["system_prompt"] = caller_prompt

    await tool.execute(**kwargs)

    call_kwargs = container.run_agent_one_shot.execute.await_args.kwargs
    effective = call_kwargs["system_prompt"]
    assert _RESULT_FORMAT_FOOTER in effective


async def test_never_raises_when_agent_config_attribute_missing():
    """
    Task 6.2 / REQ-DG-8: si container.agent_config no tiene system_prompt
    (AttributeError), DelegateTool NO propaga — retorna un ToolResult de todos modos.
    El fallback usa el footer solo.
    """
    container = MagicMock()
    use_case = MagicMock()
    use_case.execute = AsyncMock(return_value=_valid_json_response())
    container.run_agent_one_shot = use_case
    # Simular ausencia del atributo system_prompt en agent_config
    type(container.agent_config).system_prompt = property(
        lambda self: (_ for _ in ()).throw(AttributeError("no system_prompt"))
    )

    tool = _make_tool(get_agent_container=MagicMock(return_value=container))
    result = await tool.execute(agent_id=_AGENT_ID, task="task")

    # Must not raise — must return a ToolResult
    assert isinstance(result, ToolResult)
    # The footer alone should have been passed; child returned a valid JSON block
    dr = DelegationResult.model_validate_json(result.output)
    assert dr.status in ("success", "failed")
