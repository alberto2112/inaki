"""Tests para _cmd_reconcile del TelegramBot.

Cubre el comportamiento gemelo de _cmd_consolidate para ReconcileMemoryUseCase:
  - Ejecuta el use case y muestra el resultado.
  - Usuario no autorizado → no hace nada.
  - Use case None (memories.reconciliation.enabled=false) → mensaje de no disponible.
  - Excepción en el use case → muestra "Error: ...".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_reconcile_uc() -> MagicMock:
    """Mock de ReconcileMemoryUseCase."""
    uc = MagicMock()
    uc.execute = AsyncMock(return_value="Reconciliación completa.")
    return uc


@pytest.fixture
def mock_ports(mock_reconcile_uc) -> MagicMock:
    """Mock de TelegramBotPorts con reconcile_memory configurado."""
    ports = MagicMock()
    ports.reconcile_memory = mock_reconcile_uc
    return ports


@pytest.fixture
def mock_ports_sin_reconcile() -> MagicMock:
    """Mock de TelegramBotPorts sin reconcile_memory (None)."""
    ports = MagicMock()
    ports.reconcile_memory = None
    return ports


@pytest.fixture
def settings() -> MagicMock:
    """Mock de TelegramBotSettings."""
    cfg = MagicMock()
    cfg.id = "dev"
    cfg.name = "Inaki"
    cfg.description = "Asistente"
    cfg.telegram = {"token": "dummy-token", "allowed_user_ids": [12345], "reactions": False}
    return cfg


@pytest.fixture
def bot(settings, mock_ports):
    """TelegramBot con reconcile_memory configurado, sin conexión real a Telegram."""
    from unittest.mock import patch

    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        return TelegramBot(settings=settings, ports=mock_ports)


@pytest.fixture
def bot_sin_reconcile(settings, mock_ports_sin_reconcile):
    """TelegramBot con reconcile_memory=None."""
    from unittest.mock import patch

    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        return TelegramBot(settings=settings, ports=mock_ports_sin_reconcile)


async def test_cmd_reconcile_ejecuta_use_case_y_muestra_resultado(bot, mock_reconcile_uc) -> None:
    """_cmd_reconcile llama execute() y envía el resultado al usuario."""
    update = MagicMock()
    update.effective_user.id = 12345
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    await bot._cmd_reconcile(update, context)

    mock_reconcile_uc.execute.assert_awaited_once()
    calls = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("Reconciliando memoria" in c for c in calls)
    assert any("Reconciliación completa" in c for c in calls)


async def test_cmd_reconcile_no_autorizado_no_hace_nada(bot, mock_reconcile_uc) -> None:
    """_cmd_reconcile ignora la solicitud si el usuario no está en allowed_user_ids."""
    update = MagicMock()
    update.effective_user.id = 99999  # no está en la lista
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    await bot._cmd_reconcile(update, context)

    mock_reconcile_uc.execute.assert_not_called()
    update.message.reply_text.assert_not_called()


async def test_cmd_reconcile_use_case_none_avisa_no_disponible(bot_sin_reconcile) -> None:
    """_cmd_reconcile responde con mensaje de no disponible si reconcile_memory es None."""
    update = MagicMock()
    update.effective_user.id = 12345
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    await bot_sin_reconcile._cmd_reconcile(update, context)

    update.message.reply_text.assert_awaited_once_with(
        "La reconciliación de memoria no está disponible."
    )


async def test_cmd_reconcile_excepcion_muestra_error(bot, mock_reconcile_uc) -> None:
    """_cmd_reconcile captura excepciones del use case y envía 'Error: ...'."""
    mock_reconcile_uc.execute.side_effect = RuntimeError("fallo en reconcile")
    update = MagicMock()
    update.effective_user.id = 12345
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    await bot._cmd_reconcile(update, context)

    calls = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("Error" in c for c in calls)
    assert any("fallo en reconcile" in c for c in calls)
