"""
Tests unitarios para core/use_cases/run_agent_one_shot.py.

Cobertura de requisitos:
- REQ-OS-1: no carga ni persiste historial, no lee digest
- REQ-OS-2: system_prompt del caller se usa verbatim (o default del agente si None)
- REQ-OS-3: timeout (asyncio.TimeoutError) y max_iterations (ToolLoopMaxIterationsError) propagados
- REQ-OS-4: get_schemas() completo, sin RAG
- REQ-DG-9: tool "delegate" excluida del schema antes de pasarla al loop
"""

from __future__ import annotations

import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from core.domain.entities.message import Message, Role
from core.domain.errors import ToolLoopMaxIterationsError
from core.domain.value_objects.llm_response import LLMResponse
from core.ports.outbound.tool_port import ToolResult
from core.use_cases.run_agent_one_shot import RunAgentOneShotUseCase
from infrastructure.config import (
    AgentConfig,
    AgentDelegationConfig,
    ChatHistoryConfig,
    EmbeddingConfig,
    LLMConfig,
    MemoryConfig,
    ToolsConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_agent_config(agent_id: str = "test-child") -> AgentConfig:
    return AgentConfig(
        id=agent_id,
        name="Test Child",
        description="Agente child de test",
        system_prompt="Sos un agente de test.",
        llm=LLMConfig(provider="openrouter", model="test-model", api_key="test-key"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_path="models/test"),
        memory=MemoryConfig(db_path=":memory:", default_top_k=3),
        chat_history=ChatHistoryConfig(db_path="/tmp/inaki_test/history_oneshot.db"),
        tools=ToolsConfig(circuit_breaker_threshold=2),
        delegation=AgentDelegationConfig(enabled=True),
    )


def _make_llm(response: str = "Resultado final") -> AsyncMock:
    llm = AsyncMock()
    llm.complete.return_value = LLMResponse.of_text(response)
    return llm


def _make_tools(schemas: list[dict] | None = None) -> MagicMock:
    tools = MagicMock()
    tools.get_schemas.return_value = schemas if schemas is not None else []
    tools.execute = AsyncMock(
        return_value=ToolResult(tool_name="mytool", output="ok", success=True)
    )
    return tools


def _make_use_case(
    llm: AsyncMock | None = None,
    tools: MagicMock | None = None,
    agent_config: AgentConfig | None = None,
) -> RunAgentOneShotUseCase:
    return RunAgentOneShotUseCase(
        llm=llm or _make_llm(),
        tools=tools or _make_tools(),
        agent_config=agent_config or _make_agent_config(),
    )


# ---------------------------------------------------------------------------
# REQ-OS-1 — No carga ni persiste historial; no lee digest
# ---------------------------------------------------------------------------


async def test_req_os1_no_history_port_used():
    """
    REQ-OS-1: RunAgentOneShotUseCase no toca ningún port de historial.
    El use case no recibe IHistoryStore en su constructor — la ausencia del
    parámetro garantiza que no puede llamar a ningún método de persistencia.
    """
    uc = _make_use_case()

    # El use case no tiene atributo de historial — no se inyecta por construcción.
    assert not hasattr(uc, "_history"), (
        "RunAgentOneShotUseCase no debe tener un port de historial inyectado"
    )


async def test_req_os1_no_memory_digest_port_used():
    """REQ-OS-1: No se inyecta port de memoria vectorial (digest)."""
    uc = _make_use_case()
    assert not hasattr(uc, "_memory"), (
        "RunAgentOneShotUseCase no debe tener un port de memoria vectorial"
    )


async def test_req_os1_messages_start_clean_with_only_task():
    """
    REQ-OS-1: El primer (y único) mensaje enviado al loop de tools es el `task`
    del caller, sin historial previo cargado.
    """
    llm = _make_llm("respuesta")
    tools = _make_tools()
    cfg = _make_agent_config()
    uc = RunAgentOneShotUseCase(llm=llm, tools=tools, agent_config=cfg)

    with patch("core.use_cases.run_agent_one_shot.run_tool_loop", new_callable=AsyncMock) as mock_loop:
        mock_loop.return_value = "respuesta"

        await uc.execute(
            task="Hacé algo",
            system_prompt="Prompt de override",
            max_iterations=5,
            timeout_seconds=30,
        )

        _, kwargs = mock_loop.call_args
        messages_sent: list[Message] = kwargs["messages"]

        assert len(messages_sent) == 1, (
            f"Solo debe enviarse 1 mensaje (la tarea), pero se enviaron {len(messages_sent)}"
        )
        assert messages_sent[0].role == Role.USER
        assert messages_sent[0].content == "Hacé algo"


# ---------------------------------------------------------------------------
# REQ-OS-2 — system_prompt del caller usado verbatim
# ---------------------------------------------------------------------------


async def test_req_os2_override_prompt_used_verbatim():
    """
    REQ-OS-2: Cuando system_prompt no es None, se pasa al loop exactamente
    como lo entregó el caller — sin merges, wrappers ni modificaciones.
    """
    override = "Sos un clasificador especializado."
    cfg = _make_agent_config()
    uc = _make_use_case(agent_config=cfg)

    with patch("core.use_cases.run_agent_one_shot.run_tool_loop", new_callable=AsyncMock) as mock_loop:
        mock_loop.return_value = "ok"

        await uc.execute(
            task="Clasificá esto",
            system_prompt=override,
            max_iterations=3,
            timeout_seconds=10,
        )

        _, kwargs = mock_loop.call_args
        assert kwargs["system_prompt"] == override, (
            f"El system_prompt enviado al loop debe ser el override verbatim. "
            f"Recibido: {kwargs['system_prompt']!r}"
        )


async def test_req_os2_none_prompt_uses_agent_default():
    """
    REQ-OS-2: Cuando system_prompt es None, usa el system_prompt por defecto del agente.
    No debe aparecer el digest ni sections extra.
    """
    cfg = _make_agent_config()
    assert cfg.system_prompt == "Sos un agente de test."

    uc = _make_use_case(agent_config=cfg)

    with patch("core.use_cases.run_agent_one_shot.run_tool_loop", new_callable=AsyncMock) as mock_loop:
        mock_loop.return_value = "ok"

        await uc.execute(
            task="Tarea sin override",
            system_prompt=None,
            max_iterations=3,
            timeout_seconds=10,
        )

        _, kwargs = mock_loop.call_args
        assert kwargs["system_prompt"] == cfg.system_prompt, (
            "Con system_prompt=None debe usar el base system_prompt del agente"
        )


async def test_req_os2_override_does_not_contain_default_prompt():
    """
    REQ-OS-2: El override NO debe mezclarse con el prompt por defecto del agente.
    """
    cfg = _make_agent_config()
    override = "Override completamente diferente."
    uc = _make_use_case(agent_config=cfg)

    with patch("core.use_cases.run_agent_one_shot.run_tool_loop", new_callable=AsyncMock) as mock_loop:
        mock_loop.return_value = "ok"

        await uc.execute(
            task="tarea",
            system_prompt=override,
            max_iterations=3,
            timeout_seconds=10,
        )

        _, kwargs = mock_loop.call_args
        sent_prompt = kwargs["system_prompt"]

        assert cfg.system_prompt not in sent_prompt, (
            "El prompt por defecto del agente NO debe aparecer cuando hay override"
        )
        assert sent_prompt == override


# ---------------------------------------------------------------------------
# REQ-OS-3 — Propagación de límites
# ---------------------------------------------------------------------------


async def test_req_os3_timeout_propagates():
    """
    REQ-OS-3: Si run_tool_loop tarda más que timeout_seconds, se propaga
    asyncio.TimeoutError al caller sin capturarlo en este use case.
    """
    cfg = _make_agent_config()
    llm = _make_llm()
    tools = _make_tools()
    uc = RunAgentOneShotUseCase(llm=llm, tools=tools, agent_config=cfg)

    async def _slow_loop(**kwargs):
        await asyncio.sleep(10)  # duerme mucho más que el timeout

    with patch("core.use_cases.run_agent_one_shot.run_tool_loop", side_effect=_slow_loop):
        with pytest.raises(asyncio.TimeoutError):
            await uc.execute(
                task="tarea",
                system_prompt="prompt",
                max_iterations=5,
                timeout_seconds=1,
            )


async def test_req_os3_max_iterations_error_propagates():
    """
    REQ-OS-3: Si run_tool_loop lanza ToolLoopMaxIterationsError,
    el use case la propaga sin capturarla.
    """
    cfg = _make_agent_config()
    uc = _make_use_case(agent_config=cfg)

    with patch(
        "core.use_cases.run_agent_one_shot.run_tool_loop",
        new_callable=AsyncMock,
        side_effect=ToolLoopMaxIterationsError(last_response="última respuesta"),
    ):
        with pytest.raises(ToolLoopMaxIterationsError) as exc_info:
            await uc.execute(
                task="tarea",
                system_prompt="prompt",
                max_iterations=3,
                timeout_seconds=30,
            )

        assert exc_info.value.last_response == "última respuesta"


async def test_req_os3_max_iterations_passed_to_loop():
    """
    REQ-OS-3: El valor de max_iterations se pasa directamente a run_tool_loop.
    """
    cfg = _make_agent_config()
    uc = _make_use_case(agent_config=cfg)

    with patch("core.use_cases.run_agent_one_shot.run_tool_loop", new_callable=AsyncMock) as mock_loop:
        mock_loop.return_value = "ok"

        await uc.execute(
            task="tarea",
            system_prompt="prompt",
            max_iterations=7,
            timeout_seconds=30,
        )

        _, kwargs = mock_loop.call_args
        assert kwargs["max_iterations"] == 7


# ---------------------------------------------------------------------------
# REQ-OS-4 — Toolkit completo sin RAG
# ---------------------------------------------------------------------------


async def test_req_os4_full_schemas_no_rag():
    """
    REQ-OS-4: Se llama a get_schemas() (no a get_schemas_relevant).
    RAG no se invoca. Las schemas llegan completas al loop (asumiendo que
    ninguna se llama "delegate" en este test).

    Note: schemas use the ToolRegistry nested format:
    {"type": "function", "function": {"name": ..., "description": ...}}
    """
    schema_a = {"type": "function", "function": {"name": "read_file", "description": "Lee archivo"}}
    schema_b = {"type": "function", "function": {"name": "write_file", "description": "Escribe archivo"}}
    schema_c = {"type": "function", "function": {"name": "web_search", "description": "Busca en web"}}

    tools = _make_tools(schemas=[schema_a, schema_b, schema_c])
    uc = _make_use_case(tools=tools)

    with patch("core.use_cases.run_agent_one_shot.run_tool_loop", new_callable=AsyncMock) as mock_loop:
        mock_loop.return_value = "ok"

        await uc.execute(
            task="tarea",
            system_prompt="prompt",
            max_iterations=5,
            timeout_seconds=30,
        )

        # get_schemas llamado (no get_schemas_relevant)
        tools.get_schemas.assert_called_once()
        assert not hasattr(tools, "get_schemas_relevant") or (
            not tools.get_schemas_relevant.called
        ), "RAG (get_schemas_relevant) no debe invocarse en one-shot"

        # Las tres schemas llegan al loop (filter is a no-op since none is "delegate")
        _, kwargs = mock_loop.call_args
        schemas_sent = kwargs["tool_schemas"]
        sent_names = {s["function"]["name"] for s in schemas_sent}

        assert "read_file" in sent_names
        assert "write_file" in sent_names
        assert "web_search" in sent_names


# ---------------------------------------------------------------------------
# REQ-DG-9 — Recursión prevenida por construcción
# ---------------------------------------------------------------------------


async def test_req_dg9_delegate_tool_excluded_from_child_schemas():
    """
    REQ-DG-9: La tool "delegate" se excluye del schema antes de pasarlo al loop.
    El hijo no puede emitir una llamada a delegate aunque su registry la tenga.
    Este es el test crítico de prevención de recursión por construcción.

    Note: schemas use the ToolRegistry nested format:
    {"type": "function", "function": {"name": ..., "description": ...}}
    The filter in RunAgentOneShotUseCase uses s.get("function", {}).get("name").
    """
    schema_delegate = {"type": "function", "function": {"name": "delegate", "description": "Delega a otro agente"}}
    schema_other_a = {"type": "function", "function": {"name": "read_file", "description": "Lee archivo"}}
    schema_other_b = {"type": "function", "function": {"name": "git_tool", "description": "Git"}}

    tools = _make_tools(schemas=[schema_other_a, schema_delegate, schema_other_b])
    uc = _make_use_case(tools=tools)

    with patch("core.use_cases.run_agent_one_shot.run_tool_loop", new_callable=AsyncMock) as mock_loop:
        mock_loop.return_value = "ok"

        await uc.execute(
            task="tarea de delegación",
            system_prompt="prompt",
            max_iterations=5,
            timeout_seconds=30,
        )

        _, kwargs = mock_loop.call_args
        schemas_sent = kwargs["tool_schemas"]
        sent_names = [s["function"]["name"] for s in schemas_sent]

        assert "delegate" not in sent_names, (
            f"La tool 'delegate' NO debe aparecer en los schemas del hijo. "
            f"Schemas enviados: {sent_names}"
        )


async def test_req_dg9_non_delegate_tools_preserved():
    """
    REQ-DG-9 (corolario): Solo "delegate" se filtra; las demás tools llegan
    íntegras al loop del hijo.
    """
    schema_delegate = {"type": "function", "function": {"name": "delegate", "description": "Delegar"}}
    schema_a = {"type": "function", "function": {"name": "read_file", "description": "Leer"}}
    schema_b = {"type": "function", "function": {"name": "write_file", "description": "Escribir"}}

    tools = _make_tools(schemas=[schema_a, schema_delegate, schema_b])
    uc = _make_use_case(tools=tools)

    with patch("core.use_cases.run_agent_one_shot.run_tool_loop", new_callable=AsyncMock) as mock_loop:
        mock_loop.return_value = "ok"

        await uc.execute(
            task="tarea",
            system_prompt="prompt",
            max_iterations=5,
            timeout_seconds=30,
        )

        _, kwargs = mock_loop.call_args
        schemas_sent = kwargs["tool_schemas"]
        sent_names = {s["function"]["name"] for s in schemas_sent}

        assert sent_names == {"read_file", "write_file"}, (
            f"Solo 'delegate' debe filtrarse. Recibido: {sent_names}"
        )


async def test_req_dg9_no_delegate_in_registry_passes_all():
    """
    REQ-DG-9 (edge case): Si el registro del hijo no tiene "delegate",
    todas las schemas pasan íntegras (el filtro es un no-op).
    """
    schema_a = {"type": "function", "function": {"name": "read_file", "description": "Leer"}}
    schema_b = {"type": "function", "function": {"name": "patch_file", "description": "Parchear"}}

    tools = _make_tools(schemas=[schema_a, schema_b])
    uc = _make_use_case(tools=tools)

    with patch("core.use_cases.run_agent_one_shot.run_tool_loop", new_callable=AsyncMock) as mock_loop:
        mock_loop.return_value = "ok"

        await uc.execute(
            task="tarea sin delegate en registry",
            system_prompt="prompt",
            max_iterations=5,
            timeout_seconds=30,
        )

        _, kwargs = mock_loop.call_args
        schemas_sent = kwargs["tool_schemas"]
        sent_names = {s["function"]["name"] for s in schemas_sent}

        assert sent_names == {"read_file", "patch_file"}


# ---------------------------------------------------------------------------
# Smoke test — constructor injection shape
# ---------------------------------------------------------------------------


async def test_constructor_injection_shape():
    """Verifica que el use case se instancia correctamente con los tres args."""
    llm = _make_llm()
    tools = _make_tools()
    cfg = _make_agent_config()

    uc = RunAgentOneShotUseCase(llm=llm, tools=tools, agent_config=cfg)

    assert uc._llm is llm
    assert uc._tools is tools
    assert uc._cfg is cfg


async def test_execute_returns_string_response():
    """El método execute retorna el string que devuelve run_tool_loop."""
    uc = _make_use_case()

    with patch("core.use_cases.run_agent_one_shot.run_tool_loop", new_callable=AsyncMock) as mock_loop:
        mock_loop.return_value = "Resultado esperado del agente"

        result = await uc.execute(
            task="tarea",
            system_prompt="prompt",
            max_iterations=5,
            timeout_seconds=30,
        )

    assert result == "Resultado esperado del agente"


async def test_circuit_breaker_threshold_from_agent_config():
    """
    El circuit_breaker_threshold que se pasa al loop es el del AgentConfig
    del agente hijo (no un valor hardcodeado).
    """
    cfg = _make_agent_config()
    # ToolsConfig en _make_agent_config tiene circuit_breaker_threshold=2
    assert cfg.tools.circuit_breaker_threshold == 2

    uc = _make_use_case(agent_config=cfg)

    with patch("core.use_cases.run_agent_one_shot.run_tool_loop", new_callable=AsyncMock) as mock_loop:
        mock_loop.return_value = "ok"

        await uc.execute(
            task="tarea",
            system_prompt="prompt",
            max_iterations=5,
            timeout_seconds=30,
        )

        _, kwargs = mock_loop.call_args
        assert kwargs["circuit_breaker_threshold"] == 2


async def test_agent_id_passed_to_loop():
    """El agent_id pasado a run_tool_loop es el del agente hijo."""
    cfg = _make_agent_config(agent_id="child-specialist")
    uc = _make_use_case(agent_config=cfg)

    with patch("core.use_cases.run_agent_one_shot.run_tool_loop", new_callable=AsyncMock) as mock_loop:
        mock_loop.return_value = "ok"

        await uc.execute(
            task="tarea",
            system_prompt="prompt",
            max_iterations=5,
            timeout_seconds=30,
        )

        _, kwargs = mock_loop.call_args
        assert kwargs["agent_id"] == "child-specialist"
