"""Tests para _cmd_clear del TelegramBot — verifica que usa la API pública clear_history().

Cubre tareas 7.1, 7.2:
  - _handle_clear llama run_agent.clear_history() (API pública)
  - NO accede a run_agent._history.clear(...) (privado)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_container() -> MagicMock:
    """Mock de AgentContainer con run_agent que expone clear_history()."""
    container = MagicMock()
    container.run_agent.clear_history = AsyncMock(return_value=None)
    return container


@pytest.fixture
def agent_cfg() -> MagicMock:
    """Mock de AgentConfig con id y channels.telegram."""
    cfg = MagicMock()
    cfg.id = "dev"
    cfg.name = "Iñaki"
    cfg.description = "Asistente"
    cfg.channels = {
        "telegram": {"token": "dummy-token", "allowed_user_ids": [], "reactions": False}
    }
    return cfg


@pytest.fixture
def bot(agent_cfg, mock_container):
    """TelegramBot con mocks, sin conexión real a Telegram."""
    from unittest.mock import patch

    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        return TelegramBot(agent_cfg=agent_cfg, container=mock_container)


async def test_cmd_clear_llama_clear_history_api_publica(bot, mock_container) -> None:
    """_cmd_clear llama run_agent.clear_history() — la API pública, no el atributo privado."""
    # Preparar update y context
    update = MagicMock()
    update.effective_user.id = 12345
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    await bot._cmd_clear(update, context)

    # Verificar que la API pública fue llamada
    mock_container.run_agent.clear_history.assert_awaited_once()
    update.message.reply_text.assert_awaited_once_with("Historial limpiado.")


async def test_cmd_clear_no_accede_a_historial_privado(bot, mock_container) -> None:
    """_cmd_clear NO llama _history.clear() directamente — solo la API pública."""
    update = MagicMock()
    update.effective_user.id = 12345
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    await bot._cmd_clear(update, context)

    # _history NO debe tener .clear() llamado directamente
    assert not mock_container.run_agent._history.clear.called


async def test_cmd_clear_maneja_excepcion(bot, mock_container) -> None:
    """_cmd_clear captura excepciones y envía mensaje de error."""
    mock_container.run_agent.clear_history.side_effect = RuntimeError("DB fallida")
    update = MagicMock()
    update.effective_user.id = 12345
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    await bot._cmd_clear(update, context)

    # Debe haber enviado un mensaje de error
    update.message.reply_text.assert_awaited()
    call_args = update.message.reply_text.call_args_list
    assert any("Error" in str(c) for c in call_args)
