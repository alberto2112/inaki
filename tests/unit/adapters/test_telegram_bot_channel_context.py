"""
Unit tests para TelegramBot._handle_message — inyección de ChannelContext.

Coverage:
- _handle_message setea channel_context ANTES de llamar run_agent.execute
- _handle_message limpia channel_context en bloque finally (éxito)
- _handle_message limpia channel_context en bloque finally (excepción)
- ChannelContext tiene channel_type="telegram" y user_id correcto del update
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

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
    agent_cfg.channels.get.return_value = {
        "token": "fake-token",
        "allowed_user_ids": [],
        "reactions": False,
    }

    container = MagicMock()
    container.run_agent = AsyncMock()
    container.run_agent.execute = AsyncMock(return_value=run_agent_response)
    container.set_channel_context = MagicMock()

    # Parchear Application.builder() para no necesitar token real
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        bot = TelegramBot(agent_cfg, container)

    return bot, container


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_handle_message_setea_channel_context_antes_de_execute():
    """set_channel_context debe llamarse ANTES de run_agent.execute."""
    bot, container = _make_bot()
    update = _make_update(user_id=99)
    ctx = _make_context()

    # Verificar orden de llamadas usando side_effect
    call_order: list[str] = []

    def _set_ctx(c):
        call_order.append(f"set_channel_context({c})")

    async def _execute(inp):
        call_order.append("execute")
        return "respuesta"

    container.set_channel_context.side_effect = _set_ctx
    container.run_agent.execute.side_effect = _execute

    with patch("adapters.inbound.telegram.bot.telegram_update_to_input", return_value="hola"):
        await bot._handle_message(update, ctx)

    # La primera llamada a set_channel_context debe ser con un ChannelContext válido
    assert call_order[0].startswith("set_channel_context(")
    assert "execute" in call_order
    # set primero, execute después
    assert call_order.index("execute") > 0


async def test_handle_message_limpia_channel_context_despues_de_execute():
    """set_channel_context(None) debe llamarse en finally, después de execute exitoso."""
    bot, container = _make_bot()
    update = _make_update(user_id=7)
    ctx = _make_context()

    with patch("adapters.inbound.telegram.bot.telegram_update_to_input", return_value="texto"):
        await bot._handle_message(update, ctx)

    # Debe haber exactamente dos llamadas: set con contexto, luego set con None
    assert container.set_channel_context.call_count == 2
    calls = container.set_channel_context.call_args_list
    # Primera llamada: ChannelContext real
    first_arg = calls[0][0][0]
    assert isinstance(first_arg, ChannelContext)
    # Segunda llamada: None
    assert calls[1] == call(None)


async def test_handle_message_limpia_channel_context_aunque_execute_falle():
    """set_channel_context(None) se debe llamar en finally incluso si execute lanza excepción."""
    bot, container = _make_bot()
    container.run_agent.execute.side_effect = RuntimeError("fallo en LLM")
    update = _make_update(user_id=55)
    ctx = _make_context()

    with patch("adapters.inbound.telegram.bot.telegram_update_to_input", return_value="texto"):
        await bot._handle_message(update, ctx)

    # Debe haberse limpiado el contexto a pesar del error
    assert container.set_channel_context.call_count == 2
    calls = container.set_channel_context.call_args_list
    assert calls[1] == call(None)


async def test_handle_message_channel_context_tiene_datos_correctos():
    """El ChannelContext inyectado debe tener channel_type='telegram' y user_id correcto."""
    bot, container = _make_bot()
    user_id = 12345
    update = _make_update(user_id=user_id)
    ctx = _make_context()

    captured: list[ChannelContext] = []

    def _capture(c):
        if c is not None:
            captured.append(c)

    container.set_channel_context.side_effect = _capture

    with patch("adapters.inbound.telegram.bot.telegram_update_to_input", return_value="texto"):
        await bot._handle_message(update, ctx)

    assert len(captured) == 1
    channel_ctx = captured[0]
    assert channel_ctx.channel_type == "telegram"
    assert channel_ctx.user_id == str(user_id)


async def test_handle_message_sin_input_no_llama_set_channel_context():
    """Si telegram_update_to_input devuelve None/vacío, no debe procesarse el mensaje."""
    bot, container = _make_bot()
    update = _make_update()
    ctx = _make_context()

    with patch("adapters.inbound.telegram.bot.telegram_update_to_input", return_value=None):
        await bot._handle_message(update, ctx)

    # Sin input no hay que setear el contexto de canal
    container.set_channel_context.assert_not_called()
