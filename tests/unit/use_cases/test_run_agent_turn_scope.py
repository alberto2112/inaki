"""``RunAgentUseCase.execute`` publica el scope de historial del turno.

FIX bg-scope-del-turno: el turno que procesa un ``[bg-N]`` (y un ``agent_send``)
corre con ``ctx=None`` pero dentro de una conversación. Si ``delegate`` derivaba
el scope del ctx, una delegación relanzada desde ahí se encolaba con ``("", "")``
y su resultado acababa en un historial que nadie lee. El scope viaja aparte.
"""

from unittest.mock import patch

import pytest

from inaki.app.settings import build_run_agent_settings
from inaki.kernel.run_agent import RunAgentUseCase
from inaki.shared.channel_context import ChannelContext, TurnScope, current_turn_scope


@pytest.fixture
def uc(agent_config, mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools):
    return RunAgentUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        settings=build_run_agent_settings(agent_config),
    )


async def _scope_visto_por_el_tool_loop(uc: RunAgentUseCase, **kwargs) -> TurnScope | None:
    visto: list[TurnScope | None] = []

    async def loop_espia(*_args, **_kwargs) -> str:
        visto.append(current_turn_scope())
        return "ok"

    with patch("inaki.kernel.run_agent.run_tool_loop", new=loop_espia):
        await uc.execute("hola", **kwargs)
    assert len(visto) == 1
    return visto[0]


async def test_turno_sin_ctx_publica_el_scope_explicito(uc):
    """El caso del bug: el turno de un ``[bg-N]`` llega sin ctx y con scope."""
    scope = await _scope_visto_por_el_tool_loop(
        uc, ctx=None, channel="telegram", chat_id="-5391232271"
    )

    assert scope == TurnScope("telegram", "-5391232271")


async def test_turno_con_ctx_publica_el_scope_derivado(uc):
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")

    scope = await _scope_visto_por_el_tool_loop(uc, ctx=ctx)

    assert scope == TurnScope("telegram", "-100")


async def test_el_override_explicito_gana_sobre_el_ctx(uc):
    """El admin REST fuerza ``("", "")`` aunque tenga ctx: el scope es el del
    historial, no el del remitente."""
    ctx = ChannelContext(channel_type="rest", user_id="local")

    scope = await _scope_visto_por_el_tool_loop(uc, ctx=ctx, channel="", chat_id="")

    assert scope == TurnScope("", "")


async def test_el_scope_se_limpia_al_terminar_el_turno(uc):
    await _scope_visto_por_el_tool_loop(uc, channel="telegram", chat_id="1")

    assert current_turn_scope() is None
