"""Tests para el error handler global del TelegramBot (_on_error).

Decisión de diseño cubierta:
  - Error de RED con Telegram (TimedOut / NetworkError) → se loguea a WARNING y
    se ignora el update; NO se intenta responder por el canal caído ni se
    propaga el traceback crudo. El bot no "se queda bobo".
  - BadRequest (hereda de NetworkError pero es un request malformado) → se
    loguea a ERROR completo, NO se trata como blip de red.
  - Cualquier otra excepción inesperada → ERROR completo con traceback.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import BadRequest, NetworkError, TimedOut


@pytest.fixture
def settings() -> MagicMock:
    cfg = MagicMock()
    cfg.id = "dev"
    cfg.name = "Inaki"
    cfg.description = "Asistente"
    cfg.telegram = {"token": "dummy-token", "allowed_user_ids": [12345], "reactions": False}
    return cfg


@pytest.fixture
def bot(settings):
    """TelegramBot sin conexión real a Telegram."""
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.connect_timeout.return_value.read_timeout.return_value.write_timeout.return_value.pool_timeout.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        return TelegramBot(settings=settings, ports=MagicMock())


def _ctx(error: Exception) -> MagicMock:
    context = MagicMock()
    context.error = error
    return context


async def test_error_handler_registrado_en_application(settings) -> None:
    """El bot registra _on_error como error handler del Application."""
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.connect_timeout.return_value.read_timeout.return_value.write_timeout.return_value.pool_timeout.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        b = TelegramBot(settings=settings, ports=MagicMock())

    mock_app.add_error_handler.assert_called_once_with(b._on_error)


async def test_timed_out_se_loguea_warning_y_no_propaga(bot, caplog) -> None:
    """Un TimedOut (red) se loguea a WARNING y NO se re-lanza."""
    with caplog.at_level(logging.WARNING):
        await bot._on_error(MagicMock(), _ctx(TimedOut()))

    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert any("red transitorio" in r.getMessage() for r in caplog.records)
    # No debe haber ningún ERROR para un blip de red.
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


async def test_network_error_generico_se_loguea_warning(bot, caplog) -> None:
    """Cualquier NetworkError (no BadRequest) se trata como blip de red."""
    with caplog.at_level(logging.WARNING):
        await bot._on_error(MagicMock(), _ctx(NetworkError("connection reset")))

    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


async def test_bad_request_se_loguea_error(bot, caplog) -> None:
    """BadRequest hereda de NetworkError pero es bug nuestro → ERROR completo."""
    with caplog.at_level(logging.WARNING):
        await bot._on_error(MagicMock(), _ctx(BadRequest("chat not found")))

    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert not any(r.levelno == logging.WARNING for r in caplog.records)


async def test_excepcion_inesperada_se_loguea_error(bot, caplog) -> None:
    """Una excepción no-Telegram se loguea a ERROR con traceback."""
    with caplog.at_level(logging.WARNING):
        await bot._on_error(MagicMock(), _ctx(RuntimeError("boom")))

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert error_records
    assert error_records[0].exc_info is not None


# ---------------------------------------------------------------------------
# _run_pipeline — Defecto 3: TimedOut al entregar NO debe mostrar "Error: ..."
# ---------------------------------------------------------------------------


def _pipeline_update() -> MagicMock:
    update = MagicMock()
    update.effective_chat.id = 123
    update.effective_user.id = 12345
    update.message.from_user = None  # salta la resolución de identidad
    update.message.reply_text = AsyncMock()
    return update


async def test_run_pipeline_timed_out_no_muestra_error_al_usuario(bot, caplog) -> None:
    """Un TimedOut entregando la respuesta (ya persistida) se loguea WARNING y se traga.

    NO se le manda "Error: Timed out" al usuario: el turno corrió y la respuesta
    ya está en history.db; mostrar un error sobre contenido guardado confunde.
    """
    update = _pipeline_update()
    bot._ports.run_agent.get_agent_info.return_value.id = "dev"

    with (
        patch(
            "adapters.inbound.telegram.bot.dispatch_inbound_turn",
            new=AsyncMock(return_value=MagicMock(executed=True, reply="respuesta larga")),
        ),
        patch("adapters.inbound.telegram.bot.compose_sender_identity", return_value=None),
        patch(
            "adapters.inbound.telegram.bot.send_html_or_plain",
            new=AsyncMock(side_effect=TimedOut()),
        ),
        caplog.at_level(logging.WARNING),
    ):
        await bot._run_pipeline(update, user_input="hola", chat_type="private")

    # Se logueó como blip de red, sin ERROR ni traceback.
    assert any("error de red entregando" in r.getMessage() for r in caplog.records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)
    # Lo crucial: NO se intentó mandar "Error: ..." al usuario.
    update.message.reply_text.assert_not_called()


async def test_run_pipeline_error_real_si_muestra_error(bot, caplog) -> None:
    """Una excepción NO de red sigue avisándole al usuario con 'Error: ...'."""
    update = _pipeline_update()
    bot._ports.run_agent.get_agent_info.return_value.id = "dev"
    bot._set_reaction = AsyncMock()

    with (
        patch(
            "adapters.inbound.telegram.bot.dispatch_inbound_turn",
            new=AsyncMock(return_value=MagicMock(executed=True, reply="r")),
        ),
        patch("adapters.inbound.telegram.bot.compose_sender_identity", return_value=None),
        patch(
            "adapters.inbound.telegram.bot.send_html_or_plain",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
        caplog.at_level(logging.WARNING),
    ):
        await bot._run_pipeline(update, user_input="hola", chat_type="private")

    assert any(r.levelno == logging.ERROR for r in caplog.records)
    update.message.reply_text.assert_awaited_once()
    assert "Error" in update.message.reply_text.await_args.args[0]
