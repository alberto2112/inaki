"""Tests para _cmd_new del TelegramBot — alias de /consolidate + /clear.

Verifica lo esencial del feature:
  - Orden: consolida ANTES de limpiar (consolidar extrae recuerdos del historial).
  - Si la consolidación FALLA, se aborta el clear (no se pierde el historial).
  - El clear es scopeado al chat actual (channel="telegram", chat_id=...).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_container() -> MagicMock:
    """Mock de AgentContainer con consolidate_memory y run_agent.clear_history()."""
    container = MagicMock()
    container.consolidate_memory.execute = AsyncMock(return_value="2 recuerdos extraídos")
    container.run_agent.clear_history = AsyncMock(return_value=None)
    return container


@pytest.fixture
def agent_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.id = "dev"
    cfg.name = "Inaki"
    cfg.description = "Asistente"
    cfg.telegram = {"token": "dummy-token", "allowed_user_ids": [], "reactions": False}
    return cfg


@pytest.fixture
def bot(agent_cfg, mock_container):
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        return TelegramBot(settings=agent_cfg, ports=mock_container)


def _make_update() -> MagicMock:
    update = MagicMock()
    update.effective_user.id = 12345
    update.effective_chat.id = -100999
    update.message.reply_text = AsyncMock()
    return update


async def test_cmd_new_consolida_antes_de_limpiar(bot, mock_container) -> None:
    """El orden es consolidar → limpiar; ambos se ejecutan en el happy path."""
    order: list[str] = []

    def _consolidate() -> str:
        order.append("consolidate")
        return "ok"

    def _clear(**_: object) -> None:
        order.append("clear")

    mock_container.consolidate_memory.execute.side_effect = _consolidate
    mock_container.run_agent.clear_history.side_effect = _clear

    await bot._cmd_new(_make_update(), MagicMock())

    assert order == ["consolidate", "clear"]


async def test_cmd_new_limpia_scope_del_chat(bot, mock_container) -> None:
    """El clear apunta al chat actual, no a todo el agente."""
    await bot._cmd_new(_make_update(), MagicMock())

    mock_container.run_agent.clear_history.assert_awaited_once_with(
        channel="telegram",
        chat_id="-100999",
    )


async def test_cmd_new_aborta_clear_si_consolidacion_falla(bot, mock_container) -> None:
    """Si consolidate explota, NO se limpia el historial (protección de datos)."""
    mock_container.consolidate_memory.execute.side_effect = RuntimeError("LLM caído")

    await bot._cmd_new(_make_update(), MagicMock())

    mock_container.run_agent.clear_history.assert_not_awaited()


async def test_cmd_new_sin_consolidador_limpia_igual(bot, mock_container) -> None:
    """Sin use case de consolidación disponible, igual limpia el chat."""
    mock_container.consolidate_memory = None

    await bot._cmd_new(_make_update(), MagicMock())

    mock_container.run_agent.clear_history.assert_awaited_once()
