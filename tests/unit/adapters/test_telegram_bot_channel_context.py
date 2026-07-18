"""
Unit tests para TelegramBot._handle_message — inyección de ChannelContext.

El contexto del turno viaja como parámetro ``ctx`` de ``run_agent.execute``
(ya no existe el slot mutable ``set_channel_context`` en el container — la
publicación/limpieza per-turno es responsabilidad de ``execute`` vía ContextVar).

Coverage:
- _handle_message pasa ctx=ChannelContext a run_agent.execute
- El ctx tiene channel_type="telegram", user_id y chat_id correctos del update
- El scope (channel, chat_id) NO se pasa explícito — se deriva de ctx
- Sin input no se llama a execute
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


from adapters.inbound.telegram.bot import TelegramBot
from core.domain.value_objects.channel_context import ChannelContext


# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------


def _make_update(user_id: int = 42, text: str = "hola") -> MagicMock:
    """Crea un Update de Telegram falso con los campos mínimos necesarios."""
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.message.set_reaction = AsyncMock()
    return update


def _make_context() -> MagicMock:
    return MagicMock()


def _make_bot(run_agent_response: str = "ok") -> tuple[TelegramBot, MagicMock]:
    """Crea un TelegramBot con container mockeado. Devuelve (bot, container_mock)."""
    agent_cfg = MagicMock()
    agent_cfg.id = "test-agent"
    agent_cfg.telegram = {
        "token": "fake-token",
        "allowed_user_ids": [],
        "reactions": False,
    }

    container = MagicMock()
    container.run_agent = MagicMock()
    container.run_agent.execute = AsyncMock(return_value=run_agent_response)
    container.run_agent.set_extra_system_sections = MagicMock()
    container.run_agent.record_user_message = AsyncMock(return_value=None)
    # scope_registry para in-flight-message-injection — try_mark_busy=True
    # significa "scope libre", el camino normal corre execute() como antes.
    container.scope_registry = MagicMock()
    container.scope_registry.try_mark_busy = AsyncMock(return_value=True)
    container.scope_registry.mark_idle = AsyncMock(return_value=None)

    # Parchear Application.builder() para no necesitar token real
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.connect_timeout.return_value.read_timeout.return_value.write_timeout.return_value.pool_timeout.return_value.build.return_value = mock_app
        bot = TelegramBot(agent_cfg, container)

    return bot, container


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_handle_message_pasa_ctx_a_execute():
    """run_agent.execute debe recibir un ChannelContext en el kwarg ``ctx``."""
    bot, container = _make_bot()
    update = _make_update(user_id=99)
    ctx = _make_context()

    with patch("adapters.inbound.telegram.bot.telegram_update_to_input", return_value="hola"):
        await bot._handle_message(update, ctx)

    container.run_agent.execute.assert_awaited_once()
    kwargs = container.run_agent.execute.await_args.kwargs
    assert isinstance(kwargs.get("ctx"), ChannelContext), (
        "execute debe recibir el ChannelContext del turno vía kwarg ctx"
    )


async def test_handle_message_ctx_tiene_datos_correctos():
    """El ctx inyectado debe tener channel_type='telegram', user_id y chat_id del update."""
    bot, container = _make_bot()
    user_id = 12345
    update = _make_update(user_id=user_id)
    ctx = _make_context()

    with patch("adapters.inbound.telegram.bot.telegram_update_to_input", return_value="texto"):
        await bot._handle_message(update, ctx)

    channel_ctx = container.run_agent.execute.await_args.kwargs["ctx"]
    assert channel_ctx.channel_type == "telegram"
    assert channel_ctx.user_id == str(user_id)
    assert channel_ctx.chat_id == str(update.effective_chat.id)


async def test_handle_message_no_pasa_scope_explicito():
    """El scope (channel, chat_id) se deriva de ctx dentro de execute — el adapter
    NO debe pasarlo explícito (una sola fuente de verdad)."""
    bot, container = _make_bot()
    update = _make_update(user_id=7)
    ctx = _make_context()

    with patch("adapters.inbound.telegram.bot.telegram_update_to_input", return_value="texto"):
        await bot._handle_message(update, ctx)

    kwargs = container.run_agent.execute.await_args.kwargs
    assert "channel" not in kwargs, "channel debe derivarse de ctx, no pasarse explícito"
    assert "chat_id" not in kwargs, "chat_id debe derivarse de ctx, no pasarse explícito"


async def test_handle_message_execute_falla_no_propaga():
    """Si execute lanza, el handler responde el error al chat sin propagar la excepción."""
    bot, container = _make_bot()
    container.run_agent.execute.side_effect = RuntimeError("fallo en LLM")
    update = _make_update(user_id=55)
    ctx = _make_context()

    with patch("adapters.inbound.telegram.bot.telegram_update_to_input", return_value="texto"):
        await bot._handle_message(update, ctx)

    update.message.reply_text.assert_awaited()
    # El slot del scope registry se libera incluso ante error
    container.scope_registry.mark_idle.assert_awaited_once()


async def test_handle_message_sin_input_no_llama_execute():
    """Si telegram_update_to_input devuelve None/vacío, no debe procesarse el mensaje."""
    bot, container = _make_bot()
    update = _make_update()
    ctx = _make_context()

    with patch("adapters.inbound.telegram.bot.telegram_update_to_input", return_value=None):
        await bot._handle_message(update, ctx)

    container.run_agent.execute.assert_not_awaited()
