"""Tests para la resolución de ``{{CHANNEL.SENDER}}/USERNAME/FIRST_NAME/LAST_NAME``
en chats de Telegram.

Cubre los dos paths que pueblan el ``ChannelContext.sender_*``:

  1. ``_run_pipeline`` con grupo (voice/foto que disparan inmediato): se toma
     del ``update.message.from_user`` actual.
  2. ``_run_group_pipeline`` (autonomous flush, texto plano en grupo): se toma
     del snapshot ``self._last_group_sender[chat_id]``, que ``_handle_group_message``
     actualiza con cada mensaje humano entrante. Heurística: el más reciente del
     batch gana.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.domain.value_objects.channel_context import ChannelContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_container() -> MagicMock:
    container = MagicMock()
    container.run_agent.record_user_message = AsyncMock()
    container.run_agent.execute = AsyncMock(return_value="respuesta")
    container.run_agent.set_extra_system_sections = MagicMock()
    return container


@pytest.fixture
def agent_cfg_autonomous() -> MagicMock:
    cfg = MagicMock()
    cfg.id = "inaki"
    cfg.name = "Inaki"
    cfg.description = "Asistente"
    cfg.telegram = {
        "token": "dummy-token",
        "allowed_user_ids": [],
        "reactions": False,
        "groups": {
            "behavior": "autonomous",
            "bot_username": "inaki_bot",
        },
    }
    return cfg


def _build_bot(agent_cfg, container):
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app.bot.send_message = AsyncMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        return TelegramBot(settings=agent_cfg, ports=container)


def _human_update(
    *,
    chat_id: int = -100999,
    user_id: int = 42,
    username: str | None = "juan",
    first_name: str | None = "Juan",
    last_name: str | None = "Pérez",
    text: str = "hola grupo",
) -> MagicMock:
    """Construye un Update con ``from_user`` humano poblado."""
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_chat.type = "supergroup"
    update.effective_user.id = user_id
    msg = update.message
    msg.text = text
    msg.chat.type = "supergroup"
    msg.chat.id = chat_id
    msg.from_user.id = user_id
    msg.from_user.is_bot = False
    msg.from_user.username = username
    msg.from_user.first_name = first_name
    msg.from_user.last_name = last_name
    msg.reply_to_message = None
    msg.entities = []
    msg.set_reaction = AsyncMock()
    return update


# ---------------------------------------------------------------------------
# _handle_group_message snapshot del último sender
# ---------------------------------------------------------------------------


async def test_handle_group_message_snapshot_sender_humano(agent_cfg_autonomous, mock_container):
    """``_handle_group_message`` debe poblar ``_last_group_sender[chat_id]`` con
    sender_name/username/first_name/last_name cuando llega un humano."""
    bot = _build_bot(agent_cfg_autonomous, mock_container)
    bot._bot_username = "inaki_bot"
    bot._set_group_reaction = AsyncMock()

    # Evitamos el flush real para foco del test: stub el scheduler.
    bot._schedule_group_flush = MagicMock()

    update = _human_update(chat_id=-100123, user_id=42, username="juan")
    with patch(
        "adapters.inbound.telegram.group_flow.format_group_message", return_value="juan said: hola"
    ):
        await bot._handle_group_message(update, "hola", "supergroup")

    snap = bot._last_group_sender["-100123"]
    assert snap["username"] == "juan"
    assert snap["first_name"] == "Juan"
    assert snap["last_name"] == "Pérez"
    assert snap["sender_name"] is not None  # compose_sender_identity arma algo no vacío


async def test_handle_group_message_no_snapshot_si_remitente_es_bot(
    agent_cfg_autonomous, mock_container
):
    """Bots no actualizan ``_last_group_sender`` — la heurística es "última persona humana"."""
    bot = _build_bot(agent_cfg_autonomous, mock_container)
    bot._bot_username = "inaki_bot"
    bot._set_group_reaction = AsyncMock()
    bot._schedule_group_flush = MagicMock()

    # Pre-poblar con un humano previo.
    bot._last_group_sender["-100123"] = {
        "sender_name": "Maria",
        "username": "maria",
        "first_name": "Maria",
        "last_name": None,
    }

    update = _human_update(chat_id=-100123, user_id=99, username="otro_bot")
    update.message.from_user.is_bot = True
    with patch("adapters.inbound.telegram.group_flow.format_group_message", return_value="x"):
        await bot._handle_group_message(update, "msg de bot", "supergroup")

    # El snapshot del bot NO sobrescribe el humano previo.
    snap = bot._last_group_sender["-100123"]
    assert snap["username"] == "maria"


async def test_handle_group_message_ultimo_sobrescribe(agent_cfg_autonomous, mock_container):
    """Llegan dos humanos consecutivos: el último gana."""
    bot = _build_bot(agent_cfg_autonomous, mock_container)
    bot._bot_username = "inaki_bot"
    bot._set_group_reaction = AsyncMock()
    bot._schedule_group_flush = MagicMock()

    with patch("adapters.inbound.telegram.group_flow.format_group_message", return_value="x"):
        await bot._handle_group_message(
            _human_update(chat_id=-100123, user_id=1, username="juan"), "1", "supergroup"
        )
        await bot._handle_group_message(
            _human_update(chat_id=-100123, user_id=2, username="maria"), "2", "supergroup"
        )

    assert bot._last_group_sender["-100123"]["username"] == "maria"


# ---------------------------------------------------------------------------
# _run_group_pipeline lee del snapshot
# ---------------------------------------------------------------------------


async def test_run_group_pipeline_inyecta_sender_desde_snapshot(
    agent_cfg_autonomous, mock_container
):
    """``_run_group_pipeline`` debe leer ``_last_group_sender`` y armar el
    ``ChannelContext`` con los 4 campos sender_* poblados."""
    bot = _build_bot(agent_cfg_autonomous, mock_container)
    bot._last_group_sender["-100123"] = {
        "sender_name": "Juan Pérez (@juan)",
        "username": "juan",
        "first_name": "Juan",
        "last_name": "Pérez",
    }
    bot._broadcast_receiver = None

    await bot._run_group_pipeline("-100123", "supergroup")

    mock_container.run_agent.execute.assert_awaited_once()
    ctx = mock_container.run_agent.execute.await_args.kwargs["ctx"]
    assert isinstance(ctx, ChannelContext)
    assert ctx.channel_type == "telegram"
    assert ctx.chat_id == "-100123"
    assert ctx.sender_name == "Juan Pérez (@juan)"
    assert ctx.username == "juan"
    assert ctx.first_name == "Juan"
    assert ctx.last_name == "Pérez"


async def test_run_group_pipeline_sin_snapshot_deja_sender_none(
    agent_cfg_autonomous, mock_container
):
    """Si ``_last_group_sender`` no tiene entrada para el chat, los 4 sender_*
    quedan en None y las variables ``{{CHANNEL.*}}`` se dejan literales."""
    bot = _build_bot(agent_cfg_autonomous, mock_container)
    bot._broadcast_receiver = None
    # _last_group_sender vacío deliberadamente.

    await bot._run_group_pipeline("-100999", "supergroup")

    mock_container.run_agent.execute.assert_awaited_once()
    ctx = mock_container.run_agent.execute.await_args.kwargs["ctx"]
    assert isinstance(ctx, ChannelContext)
    assert ctx.sender_name is None
    assert ctx.username is None
    assert ctx.first_name is None
    assert ctx.last_name is None
