"""Tests para el trigger broadcast bot-to-bot del TelegramBot.

Cubre el flujo unificado post-refactor:
  - subscribe_broadcast_trigger solo registra en modo autonomous con receiver.
  - _on_broadcast_received persiste el broadcast en el historial vía
    ``record_user_message`` y programa un flush task.
  - Rate limiter sigue siendo el gate de entrada — si breach → no se persiste.
  - El response al broadcast vive ahora en ``_run_group_pipeline`` (flush),
    no en un método dedicado.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.ports.outbound.broadcast_port import BroadcastMessage


@pytest.fixture
def mock_container() -> MagicMock:
    container = MagicMock()
    container.run_agent.record_user_message = AsyncMock()
    container.run_agent.execute = AsyncMock(return_value="respuesta del llm")
    container.run_agent.set_extra_system_sections = MagicMock()
    container.set_channel_context = MagicMock()
    return container


@pytest.fixture
def agent_cfg_autonomous() -> MagicMock:
    cfg = MagicMock()
    cfg.id = "inaki"
    cfg.name = "Iñaki"
    cfg.description = "Asistente"
    cfg.channels = {
        "telegram": {
            "token": "dummy-token",
            "allowed_user_ids": [],
            "reactions": False,
            "broadcast": {
                "behavior": "autonomous",
                "bot_username": "inaki_bot",
                "rate_limiter": 5,
            },
        }
    }
    return cfg


@pytest.fixture
def mock_receiver() -> MagicMock:
    recv = MagicMock()
    recv.subscribe = AsyncMock()
    recv.render = MagicMock(return_value=None)
    return recv


@pytest.fixture
def mock_emitter() -> MagicMock:
    em = MagicMock()
    em.emit = AsyncMock()
    return em


@pytest.fixture
def mock_rate_limiter() -> MagicMock:
    """Rate limiter que nunca hace breach por defecto."""
    rl = MagicMock()
    rl.check_and_increment = MagicMock(return_value=None)
    return rl


def _build_bot(agent_cfg, container, receiver=None, emitter=None, rate_limiter=None):
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app.bot.send_message = AsyncMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        return TelegramBot(
            agent_cfg=agent_cfg,
            container=container,
            broadcast_emitter=emitter,
            broadcast_receiver=receiver,
            rate_limiter=rate_limiter,
        )


def _msg(text: str, chat_id: str = "-100123", agent_id: str = "anacleto") -> BroadcastMessage:
    return BroadcastMessage(
        timestamp=time.time(),
        agent_id=agent_id,
        chat_id=chat_id,
        message=text,
    )


# ---------------------------------------------------------------------------
# subscribe_broadcast_trigger
# ---------------------------------------------------------------------------


async def test_subscribe_broadcast_trigger_registra_en_autonomous(
    agent_cfg_autonomous, mock_container, mock_receiver
):
    """autonomous + receiver → se registra el callback."""
    bot = _build_bot(agent_cfg_autonomous, mock_container, receiver=mock_receiver)
    await bot.subscribe_broadcast_trigger()
    mock_receiver.subscribe.assert_awaited_once_with(bot._on_broadcast_received)


async def test_subscribe_broadcast_trigger_sin_receiver_noop(
    agent_cfg_autonomous, mock_container
):
    """Sin receiver no hace nada (no explota)."""
    bot = _build_bot(agent_cfg_autonomous, mock_container, receiver=None)
    await bot.subscribe_broadcast_trigger()  # no raises


async def test_subscribe_broadcast_trigger_mention_noop(mock_container, mock_receiver):
    """behavior=mention no registra el trigger (no tiene sentido sin entities reales)."""
    cfg = MagicMock()
    cfg.id = "inaki"
    cfg.name = "Iñaki"
    cfg.description = ""
    cfg.channels = {
        "telegram": {
            "token": "dummy-token",
            "allowed_user_ids": [],
            "broadcast": {
                "behavior": "mention",
                "bot_username": "inaki_bot",
            },
        }
    }
    bot = _build_bot(cfg, mock_container, receiver=mock_receiver)
    await bot.subscribe_broadcast_trigger()
    mock_receiver.subscribe.assert_not_awaited()


# ---------------------------------------------------------------------------
# _on_broadcast_received — flujo unificado: record + schedule flush
# ---------------------------------------------------------------------------


async def test_on_broadcast_persiste_en_historial_y_programa_flush(
    agent_cfg_autonomous, mock_container, mock_receiver, mock_emitter, mock_rate_limiter
):
    """Un broadcast válido se persiste vía record_user_message y se crea un flush task."""
    bot = _build_bot(
        agent_cfg_autonomous,
        mock_container,
        receiver=mock_receiver,
        emitter=mock_emitter,
        rate_limiter=mock_rate_limiter,
    )
    msg = _msg("comentario sobre el clima")
    await bot._on_broadcast_received(msg)

    mock_container.run_agent.record_user_message.assert_awaited_once()
    call = mock_container.run_agent.record_user_message.await_args
    assert "anacleto dijo:" in call.args[0]
    assert "comentario sobre el clima" in call.args[0]
    assert call.kwargs.get("channel") == "telegram"
    assert call.kwargs.get("chat_id") == "-100123"

    # Un flush task fue creado para este chat
    assert "-100123" in bot._pending_tasks
    # Cancelamos para no dejar tasks colgadas en el loop de tests
    bot._pending_tasks["-100123"].cancel()
    try:
        await bot._pending_tasks["-100123"]
    except (asyncio.CancelledError, BaseException):
        pass


async def test_on_broadcast_no_invoca_llm_directamente(
    agent_cfg_autonomous, mock_container, mock_receiver, mock_emitter, mock_rate_limiter
):
    """El callback NO llama a execute() — eso lo hace el flush task tras el delay."""
    bot = _build_bot(
        agent_cfg_autonomous,
        mock_container,
        receiver=mock_receiver,
        emitter=mock_emitter,
        rate_limiter=mock_rate_limiter,
    )
    await bot._on_broadcast_received(_msg("hola"))
    mock_container.run_agent.execute.assert_not_awaited()

    # Limpiamos el task pendiente
    for task in bot._pending_tasks.values():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, BaseException):
            pass


async def test_on_broadcast_respeta_rate_limiter(
    agent_cfg_autonomous, mock_container, mock_receiver, mock_emitter
):
    """Si rate limiter hace breach → no persiste ni programa flush."""
    rl = MagicMock()
    rl.check_and_increment = MagicMock(return_value=MagicMock(counter=6))
    bot = _build_bot(
        agent_cfg_autonomous,
        mock_container,
        receiver=mock_receiver,
        emitter=mock_emitter,
        rate_limiter=rl,
    )
    await bot._on_broadcast_received(_msg("cualquier cosa"))

    mock_container.run_agent.record_user_message.assert_not_awaited()
    assert bot._pending_tasks == {}
    rl.check_and_increment.assert_called_once_with("inaki", "-100123", 5)


async def test_on_broadcast_es_idempotente_si_hay_flush_activo(
    agent_cfg_autonomous, mock_container, mock_receiver, mock_emitter, mock_rate_limiter
):
    """Si ya hay un flush task corriendo para el chat, los broadcasts se acumulan en
    el historial pero NO se crea un nuevo task."""
    bot = _build_bot(
        agent_cfg_autonomous,
        mock_container,
        receiver=mock_receiver,
        emitter=mock_emitter,
        rate_limiter=mock_rate_limiter,
    )
    # Forzamos que el delay no termine durante el test
    import adapters.inbound.telegram.bot as bot_module

    await bot._on_broadcast_received(_msg("primer broadcast"))
    primer_task = bot._pending_tasks["-100123"]

    await bot._on_broadcast_received(_msg("segundo broadcast"))
    # Mismo task — no se reemplazó
    assert bot._pending_tasks["-100123"] is primer_task
    # Pero ambos broadcasts fueron persistidos
    assert mock_container.run_agent.record_user_message.await_count == 2

    # Cleanup
    primer_task.cancel()
    try:
        await primer_task
    except (asyncio.CancelledError, BaseException):
        pass
    _ = bot_module  # silenciar unused import warning si hubiera
