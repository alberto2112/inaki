"""El rate limit de grupos visto desde ``GroupFlow``: quién gasta presupuesto y quién lo re-arma.

Cierra la pinza de ``test_rate_limit.py`` (la política aislada) con el flujo que
la usa: el presupuesto se gasta al EMITIR (``flush_buffer``), no al recibir, y el
gate del cooldown corta el flush ANTES de programarlo.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from inaki.channels.telegram.group_flow import GroupFlow
from inaki.channels.telegram.rate_limit import GroupRateLimit
from inaki.channels.telegram.reactions import Reactions


class _TurnsFake:
    """``TurnRunner`` de mentira: devuelve si el turno emitió o no."""

    def __init__(self, respondio: bool) -> None:
        self._respondio = respondio
        self.llamadas = 0

    async def run_group(self, chat_id: str, chat_type: str, last_sender: dict) -> bool:
        self.llamadas += 1
        return self._respondio


def _flow(rate_limit: GroupRateLimit, *, respondio: bool = True) -> tuple[GroupFlow, _TurnsFake]:
    turns = _TurnsFake(respondio)
    flow = GroupFlow(
        history=MagicMock(record_user_message=AsyncMock()),
        agent_id="inaki",
        behavior="autonomous",
        bot_username=None,
        min_delay=0.0,
        max_delay=0.0,
        reactions=Reactions(private=False, groups=False),
        rate_limit=rate_limit,
        turns=turns,  # type: ignore[arg-type]
    )
    return flow, turns


def _politica(max_count: int = 2) -> GroupRateLimit:
    return GroupRateLimit(enabled=True, agent_id="inaki", max_count=max_count, window_seconds=300)


def _update_humano(chat_id: int = -100123, *, es_bot: bool = False) -> MagicMock:
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_chat.type = "supergroup"
    msg = update.message
    msg.text = "hola grupo"
    msg.chat.id = chat_id
    msg.from_user.is_bot = es_bot
    msg.reply_to_message = None
    msg.entities = []
    msg.set_reaction = AsyncMock()
    return update


async def test_una_respuesta_emitida_gasta_una_intervencion() -> None:
    """N mensajes coalescidos en un flush = UNA intervención."""
    politica = _politica(max_count=2)
    flow, turns = _flow(politica)

    await flow.flush_buffer("-100123", "supergroup")
    assert politica.cooldown("-100123") is None
    await flow.flush_buffer("-100123", "supergroup")

    assert turns.llamadas == 2
    enfriando = politica.cooldown("-100123")
    assert enfriando is not None and enfriando.consecutive == 2


async def test_un_turno_que_skipea_no_gasta_presupuesto() -> None:
    """``__SKIP__`` es silencio: el freno sano del modo autónomo no se cobra."""
    politica = _politica(max_count=1)
    flow, _ = _flow(politica, respondio=False)

    for _ in range(5):
        await flow.flush_buffer("-100123", "supergroup")

    assert politica.cooldown("-100123") is None


async def test_en_cooldown_no_se_programa_flush() -> None:
    politica = _politica(max_count=1)
    politica.record_response("-100123")
    historial = MagicMock(record_user_message=AsyncMock())
    flow, _ = _flow(politica)
    flow._history = historial

    update = _update_humano(es_bot=True)  # otro bot: no re-arma
    with patch(
        "inaki.channels.telegram.group_flow.format_group_message", return_value="x said: hola"
    ):
        await flow.handle_message(update, "hola", "supergroup")

    assert flow.pending_tasks == {}, "enfriando: el mensaje se persiste pero no se responde"
    historial.record_user_message.assert_awaited_once()


async def test_un_humano_levanta_el_cooldown_en_el_mismo_mensaje() -> None:
    """El re-armado va ANTES del gate: el mensaje que lo levanta ya se contesta."""
    politica = _politica(max_count=1)
    politica.record_response("-100123")
    flow, _ = _flow(politica)
    flow.schedule_flush = MagicMock()  # type: ignore[method-assign]

    with patch(
        "inaki.channels.telegram.group_flow.format_group_message", return_value="juan said: hola"
    ):
        await flow.handle_message(_update_humano(), "hola", "supergroup")

    flow.schedule_flush.assert_called_once_with("-100123", "supergroup")
    assert politica.cooldown("-100123") is None
