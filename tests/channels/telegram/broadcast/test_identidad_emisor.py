"""Rótulo del emisor en el broadcast: un bot se rotula igual que un humano.

Un agente puede tener un username de Telegram raro y jugar otro rol en el grupo;
por eso su ``assistant_response`` viaja con ``@username (Nombre)`` en ``sender``
y los receptores lo persisten así. Un emisor viejo (``sender`` vacío) cae al
``agent_id``.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from inaki.channels.telegram.broadcast.buffer import _formatear_linea
from inaki.channels.telegram.broadcast.egress import BroadcastEgress
from inaki.channels.telegram.broadcast.ingress import _format_history_prefix
from inaki.channels.telegram.broadcast.port import BroadcastMessage
from inaki.channels.telegram.group_flow import GroupFlow
from inaki.channels.telegram.ports import TelegramEmitFlags


def _egress() -> tuple[BroadcastEgress, AsyncMock]:
    emitter = MagicMock()
    emitter.emit = AsyncMock()
    flags = TelegramEmitFlags(assistant_response=True, user_input_voice=True, user_input_photo=True)
    return BroadcastEgress(emitter, "anacleto", flags), emitter.emit


def _msg(event_type: str = "assistant_response", sender: str = "") -> BroadcastMessage:
    return BroadcastMessage(
        timestamp=time.time(),
        agent_id="anacleto",
        chat_id="-100123",
        event_type=event_type,  # type: ignore[arg-type]
        content="hola",
        sender=sender,
    )


# ---------------------------------------------------------------------------
# Egress
# ---------------------------------------------------------------------------


async def test_assistant_response_viaja_con_la_identidad_del_bot():
    egress, emit = _egress()
    egress.fijar_identidad("@xk_bot_17 (Anacleto)")

    await egress.emit(event_type="assistant_response", chat_id="-100123", content="hola")

    assert emit.await_args.args[0].sender == "@xk_bot_17 (Anacleto)"


async def test_assistant_response_sin_identidad_viaja_vacio():
    egress, emit = _egress()

    await egress.emit(event_type="assistant_response", chat_id="-100123", content="hola")

    assert emit.await_args.args[0].sender == ""


async def test_user_input_conserva_el_sender_humano():
    """La identidad del bot NO pisa al humano que habló."""
    egress, emit = _egress()
    egress.fijar_identidad("@xk_bot_17 (Anacleto)")

    await egress.emit(
        event_type="user_input_voice", chat_id="-100123", content="hola", sender="@juan (Juan)"
    )

    assert emit.await_args.args[0].sender == "@juan (Juan)"


# ---------------------------------------------------------------------------
# Ingress (historial) y buffer (prompt)
# ---------------------------------------------------------------------------


def test_ingress_persiste_assistant_response_con_el_rotulo_del_bot():
    msg = _msg(sender="@xk_bot_17 (Anacleto)")
    assert _format_history_prefix(msg) == "@xk_bot_17 (Anacleto) said: hola"


def test_ingress_emisor_viejo_sin_sender_cae_al_agent_id():
    assert _format_history_prefix(_msg()) == "anacleto said: hola"


def test_buffer_usa_el_rotulo_del_bot_y_cae_al_agent_id():
    assert _formatear_linea(_msg(sender="@xk_bot_17 (Anacleto)"), "10:00:00") == (
        "- [10:00:00] @xk_bot_17 (Anacleto): hola"
    )
    assert _formatear_linea(_msg(), "10:00:00") == "- [10:00:00] anacleto: hola"


# ---------------------------------------------------------------------------
# get_me(): de dónde sale la identidad
# ---------------------------------------------------------------------------


def _group_flow(bot_username: str | None) -> GroupFlow:
    return GroupFlow(
        history=MagicMock(),
        agent_id="anacleto",
        behavior="autonomous",
        bot_username=bot_username,
        min_delay=0,
        max_delay=0,
        reactions=MagicMock(),
        rate_limit=MagicMock(),
        turns=MagicMock(),
    )


async def test_resolve_bot_username_devuelve_el_user_del_bot():
    me = SimpleNamespace(username="xk_bot_17", first_name="Anacleto", last_name=None)
    bot = MagicMock()
    bot.get_me = AsyncMock(return_value=me)

    assert await _group_flow(None).resolve_bot_username(bot) is me
    assert await _group_flow("xk_bot_17").resolve_bot_username(bot) is me
    assert await _group_flow("otro_nombre").resolve_bot_username(bot) is me


async def test_resolve_bot_username_get_me_fallido_devuelve_none():
    bot = MagicMock()
    bot.get_me = AsyncMock(side_effect=RuntimeError("sin red"))

    assert await _group_flow(None).resolve_bot_username(bot) is None
