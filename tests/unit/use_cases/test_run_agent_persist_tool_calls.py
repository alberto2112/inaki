"""Tests del feature persist-tool-calls a nivel RunAgentUseCase.

Verifican que, con ``chat_history.persist_tool_calls`` activo, el turno persiste
el rastro estructurado (assistant+tool_calls ↔ tool result, truncado) y que con
el flag apagado el comportamiento es idéntico al legacy.
"""

from unittest.mock import AsyncMock

import pytest

from core.domain.entities.message import Message, Role
from core.domain.value_objects.llm_response import LLMResponse
from core.ports.outbound.tool_port import ToolResult
from core.use_cases.run_agent import RunAgentUseCase
from infrastructure.container import build_run_agent_settings


def _tool_then_final_llm(mock_llm, narration="ok, escribo el archivo", final="Listo, quedó en /x"):
    """Configura el LLM mock: 1ª llamada emite un tool_call (write_file) con
    narración; 2ª llamada devuelve la respuesta final sin tool_calls."""
    tc = LLMResponse(
        text_blocks=[narration],
        tool_calls=[{"id": "call_1", "function": {"name": "write_file", "arguments": "{}"}}],
        raw="",
    )
    mock_llm.complete.side_effect = [tc, LLMResponse.of_text(final)]
    return mock_llm


def _build_uc(agent_config, mocks, *, persist: bool, max_chars: int = 2000, tool_output="/x"):
    mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools = mocks
    mock_tools.execute = AsyncMock(
        return_value=ToolResult(tool_name="write_file", output=tool_output, success=True)
    )
    settings = build_run_agent_settings(agent_config).model_copy(
        update={"persist_tool_calls": persist, "persist_tool_result_max_chars": max_chars}
    )
    return RunAgentUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        settings=settings,
    )


@pytest.fixture
def mocks(mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools):
    return (mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools)


def _persisted(mock_history):
    """Mensajes persistidos vía append, en orden: (role, message)."""
    return [(c.args[1].role, c.args[1]) for c in mock_history.append.call_args_list]


async def test_persist_on_guarda_el_rastro_de_tool_calls(agent_config, mocks):
    _tool_then_final_llm(mocks[0])
    uc = _build_uc(agent_config, mocks, persist=True)

    await uc.execute("guardá esto")

    roles = [r for r, _ in _persisted(mocks[4])]
    assert roles == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]
    _, assistant_tc = _persisted(mocks[4])[1]
    assert assistant_tc.tool_calls  # lleva los tool_calls
    assert assistant_tc.content == "ok, escribo el archivo"  # narración estructurada
    _, tool_msg = _persisted(mocks[4])[2]
    assert tool_msg.tool_call_id == "call_1"
    assert tool_msg.content == "/x"


async def test_persist_on_trunca_tool_result_largo(agent_config, mocks):
    _tool_then_final_llm(mocks[0])
    uc = _build_uc(agent_config, mocks, persist=True, max_chars=20, tool_output="X" * 100)

    await uc.execute("guardá esto")

    _, tool_msg = _persisted(mocks[4])[2]
    assert tool_msg.content == "X" * 20 + "\n…[truncado]"


async def test_persist_off_es_legacy_sin_mensajes_tool(agent_config, mocks):
    _tool_then_final_llm(mocks[0])
    uc = _build_uc(agent_config, mocks, persist=False)

    await uc.execute("guardá esto")

    roles = [r for r, _ in _persisted(mocks[4])]
    # Solo user + respuesta final; ningún mensaje role=tool.
    assert roles == [Role.USER, Role.ASSISTANT]
    assert Role.TOOL not in roles


async def test_get_history_oculta_mensajes_tool(agent_config, mocks):
    uc = _build_uc(agent_config, mocks, persist=True)
    mocks[4].load.return_value = [
        Message(role=Role.USER, content="guardá"),
        Message(role=Role.ASSISTANT, content="ok", tool_calls=[{"id": "c"}]),
        Message(role=Role.TOOL, content="{}", tool_call_id="c"),
        Message(role=Role.ASSISTANT, content="listo"),
    ]

    visible = await uc.get_history()
    assert [m.role for m in visible] == [Role.USER, Role.ASSISTANT, Role.ASSISTANT]
    assert all(m.role != Role.TOOL for m in visible)
