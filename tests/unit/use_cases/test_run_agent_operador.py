"""``RunAgentUseCase.execute(es_operador=)`` — la señal de operador entra por ``execute()``.

La señal es un kwarg explícito (default False), NO un campo de ``ChannelContext`` ni un
ContextVar: así un hijo delegado (one-shot) no hereda el privilegio del turno padre.
"""

from unittest.mock import AsyncMock, patch

import pytest

from inaki.app.settings import build_run_agent_settings
from inaki.kernel.domain.llm_response import LLMResponse
from inaki.kernel.ports.turn_tracer_port import ITurnTracer
from inaki.kernel.run_agent import RunAgentUseCase


class _TracerEspia(ITurnTracer):
    def __init__(self) -> None:
        self.eventos: list[tuple[str, dict]] = []

    def bind(self, **context: object) -> ITurnTracer:
        return self

    def trace(self, event: str, **fields: object) -> None:
        self.eventos.append((event, fields))


@pytest.fixture
def armar(
    agent_config, mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools
):
    def _armar(tracer: ITurnTracer | None = None) -> RunAgentUseCase:
        return RunAgentUseCase(
            llm=mock_llm,
            memory=mock_memory,
            embedder=mock_embedder,
            skills=mock_skills,
            history=mock_history,
            tools=mock_tools,
            settings=build_run_agent_settings(agent_config),
            tracer=tracer,
        )

    return _armar


async def test_es_operador_true_llega_al_tool_loop(armar):
    uc = armar()
    with patch("inaki.kernel.run_agent.run_tool_loop", new=AsyncMock(return_value="ok")) as loop:
        await uc.execute("hola", es_operador=True)

    assert loop.call_args.kwargs["turno_de_operador"] is True


async def test_es_operador_por_defecto_es_false(armar):
    uc = armar()
    with patch("inaki.kernel.run_agent.run_tool_loop", new=AsyncMock(return_value="ok")) as loop:
        await uc.execute("hola")

    assert loop.call_args.kwargs["turno_de_operador"] is False


async def test_turn_start_traza_es_operador(armar, mock_llm):
    mock_llm.complete.return_value = LLMResponse.of_text("ok")
    espia = _TracerEspia()
    uc = armar(espia)

    await uc.execute("hola", es_operador=True)
    await uc.execute("hola")

    inicios = [campos for evento, campos in espia.eventos if evento == "turn.start"]
    assert [i["es_operador"] for i in inicios] == [True, False]
