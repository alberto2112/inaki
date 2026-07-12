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


# ---------------------------------------------------------------------------
# incremental-persist — el rastro se escribe EN CALIENTE cuando el turno no
# puede terminar en __SKIP__ (skip_marker=None); batch legacy cuando sí puede.
# ---------------------------------------------------------------------------


def _tc_response(narration="narro"):
    return LLMResponse(
        text_blocks=[narration],
        tool_calls=[{"id": "call_1", "function": {"name": "write_file", "arguments": "{}"}}],
        raw="",
    )


async def test_incremental_persiste_durante_el_loop(agent_config, mocks):
    """Turno conversacional (skip_marker=None) con flag ON: cuando llega la
    SEGUNDA llamada al LLM, el par assistant+tool_calls ↔ tool result YA está
    en el historial — un crash en ese punto no pierde el trabajo narrado."""
    mock_llm, _, _, _, mock_history, _ = mocks
    persisted_before_second_call: list = []

    call_n = 0

    async def complete_side(messages, system_prompt, tools=None):  # noqa: ARG001
        nonlocal call_n
        call_n += 1
        if call_n == 1:
            return _tc_response()
        persisted_before_second_call.extend(
            c.args[1].role for c in mock_history.append.call_args_list
        )
        return LLMResponse.of_text("final")

    mock_llm.complete.side_effect = complete_side
    uc = _build_uc(agent_config, mocks, persist=True)

    await uc.execute("guardá esto")

    # user (pre-loop) + assistant(tool_calls) + tool result — ANTES del final.
    assert persisted_before_second_call == [Role.USER, Role.ASSISTANT, Role.TOOL]


async def test_turno_skip_capaz_mantiene_batch_al_final(agent_config, mocks):
    """Con skip_marker seteado (turno autónomo), la persistencia del rastro se
    difiere al final del turno: en la segunda llamada al LLM solo está el user."""
    mock_llm, _, _, _, mock_history, _ = mocks
    persisted_before_second_call: list = []

    call_n = 0

    async def complete_side(messages, system_prompt, tools=None):  # noqa: ARG001
        nonlocal call_n
        call_n += 1
        if call_n == 1:
            return _tc_response()
        persisted_before_second_call.extend(
            c.args[1].role for c in mock_history.append.call_args_list
        )
        return LLMResponse.of_text("final")

    mock_llm.complete.side_effect = complete_side
    uc = _build_uc(agent_config, mocks, persist=True)

    await uc.execute("chequeo autónomo", skip_marker="__SKIP__")

    assert persisted_before_second_call == [Role.USER]  # batch diferido
    # ...pero al terminar el turno el rastro completo quedó igual.
    roles = [r for r, _ in _persisted(mock_history)]
    assert roles == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]


async def test_turno_skip_capaz_que_skipea_no_persiste_rastro(agent_config, mocks):
    """Un turno autónomo que termina en __SKIP__ no deja rastro ni respuesta
    (la semántica original de skip, intacta)."""
    mock_llm, _, _, _, mock_history, _ = mocks
    mock_llm.complete.side_effect = [_tc_response(), LLMResponse.of_text("__SKIP__")]
    uc = _build_uc(agent_config, mocks, persist=True)

    await uc.execute("chequeo autónomo", skip_marker="__SKIP__")

    roles = [r for r, _ in _persisted(mock_history)]
    assert roles == [Role.USER]  # solo la durabilidad pre-loop del user


async def test_incremental_narracion_sin_flag_persiste_cada_emit(agent_config, mocks):
    """Flag persist_tool_calls OFF + turno conversacional: la narración que el
    sink entrega en vivo se persiste en caliente como assistant plano."""
    from core.ports.outbound.intermediate_sink_port import NullIntermediateSink

    mock_llm, _, _, _, mock_history, _ = mocks
    _tool_then_final_llm(mock_llm, narration="voy a escribir el archivo")
    uc = _build_uc(agent_config, mocks, persist=False)

    await uc.execute("guardá esto", intermediate_sink=NullIntermediateSink())

    roles_contents = [(r, m.content) for r, m in _persisted(mock_history)]
    assert (Role.ASSISTANT, "voy a escribir el archivo") in roles_contents
    # Sin flag no hay mensajes TOOL — solo narración plana + final.
    assert all(r != Role.TOOL for r, _ in roles_contents)
