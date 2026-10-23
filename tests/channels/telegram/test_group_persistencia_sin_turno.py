"""Persistir y disparar son dos decisiones: lo que se dice en un grupo es contexto.

En ``listen`` y en ``mention`` (sin mención) los mensajes se persisten en el
historial SIN turno, para que cuando por fin se le hable al agente tenga el
contexto del grupo. La query del turno es solo lo que se le dirigió.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from inaki.channels.telegram.group_flow import GroupFlow
from inaki.channels.telegram.message_mapper import detect_mention, dirigido_a
from inaki.channels.telegram.rate_limit import GroupRateLimit
from inaki.channels.telegram.reactions import Reactions

_CHAT = -100123


class _TurnsFake:
    def __init__(self) -> None:
        self.consultas: list[str | None] = []

    async def run_group(
        self, chat_id: str, chat_type: str, last_sender: dict, *, consulta: str | None = None
    ) -> bool:
        self.consultas.append(consulta)
        return True


def _flow(behavior: str) -> tuple[GroupFlow, _TurnsFake, AsyncMock, MagicMock]:
    turns = _TurnsFake()
    record = AsyncMock()
    flow = GroupFlow(
        history=MagicMock(record_user_message=record),
        agent_id="inaki",
        behavior=behavior,
        bot_username="inaki_bot",
        min_delay=0.0,
        max_delay=0.0,
        reactions=Reactions(private=False, groups=True),
        rate_limit=GroupRateLimit(enabled=False, agent_id="inaki", max_count=5, window_seconds=1),
        turns=turns,  # type: ignore[arg-type]
    )
    schedule_flush = MagicMock()
    flow.schedule_flush = schedule_flush  # type: ignore[method-assign]
    return flow, turns, record, schedule_flush


def _update(texto: str, *, mencion: str | None = None) -> MagicMock:
    update = MagicMock()
    update.effective_chat.id = _CHAT
    msg = update.message
    msg.text = texto
    msg.caption = None
    msg.chat.id = _CHAT
    msg.from_user.is_bot = False
    msg.from_user.username = "marta"
    msg.from_user.first_name = "Marta"
    msg.reply_to_message = None
    msg.entities = []
    if mencion is not None:
        offset = texto.index(mencion)
        msg.entities = [SimpleNamespace(type="mention", offset=offset, length=len(mencion))]
    msg.set_reaction = AsyncMock()
    return update


async def test_mention_sin_mencion_persiste_sin_turno() -> None:
    flow, _, record, schedule_flush = _flow("mention")
    update = _update("che, ¿alguien vio el partido?")

    await flow.handle_message(update, "", "supergroup")

    record.assert_awaited_once()
    assert record.await_args is not None
    assert "alguien vio el partido" in record.await_args.args[0]
    schedule_flush.assert_not_called()
    update.message.set_reaction.assert_not_awaited()
    # El emisor del contexto no pisa a quien le habla al agente.
    assert str(_CHAT) not in flow.last_sender


async def test_mention_con_mencion_dispara_y_la_consulta_es_solo_lo_dirigido() -> None:
    flow, turns, record, schedule_flush = _flow("mention")
    await flow.handle_message(_update("ganó River 2 a 0"), "", "supergroup")
    dirigido = _update("@inaki_bot ¿quién ganó?", mencion="@inaki_bot")

    await flow.handle_message(dirigido, "", "supergroup")

    assert record.await_count == 2
    schedule_flush.assert_called_once_with(str(_CHAT), "supergroup")
    dirigido.message.set_reaction.assert_awaited()
    assert flow.last_sender[str(_CHAT)]["username"] == "marta"

    await flow.flush_buffer(str(_CHAT), "supergroup")
    assert len(turns.consultas) == 1
    consulta = turns.consultas[0]
    assert consulta is not None
    assert "quién ganó" in consulta
    assert "River" not in consulta
    # El flush consume las consultas: el próximo arranca de cero.
    assert str(_CHAT) not in flow.consultas


async def test_listen_persiste_y_nunca_dispara() -> None:
    flow, _, record, schedule_flush = _flow("listen")

    await flow.handle_message(_update("@inaki_bot hola", mencion="@inaki_bot"), "", "supergroup")

    record.assert_awaited_once()
    schedule_flush.assert_not_called()


async def test_dirigido_a_otro_no_se_persiste() -> None:
    flow, _, record, schedule_flush = _flow("mention")

    await flow.handle_message(_update("@otro_bot hola", mencion="@otro_bot"), "", "supergroup")

    record.assert_not_awaited()
    schedule_flush.assert_not_called()


async def test_autonomous_usa_el_trailing_batch_entero() -> None:
    """En autónomo todo el batch dispara: no hay consulta acotada (``None``)."""
    flow, turns, _, _ = _flow("autonomous")
    await flow.handle_message(_update("hola grupo"), "", "supergroup")

    await flow.flush_buffer(str(_CHAT), "supergroup")

    assert turns.consultas == [None]


def test_mencion_en_el_caption_de_un_media() -> None:
    caption = "@inaki_bot ¿qué es esto?"
    msg = SimpleNamespace(
        text=None,
        entities=None,
        caption=caption,
        caption_entities=[SimpleNamespace(type="mention", offset=0, length=10)],
        reply_to_message=None,
    )

    assert detect_mention(msg, "inaki_bot")
    assert dirigido_a(msg, "inaki_bot")
