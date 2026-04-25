"""Tests para el trigger broadcast bot-to-bot del TelegramBot.

Cubre:
  - subscribe_broadcast_trigger solo registra en modo autonomous con receiver.
  - _on_broadcast_received dispara el pipeline ante CUALQUIER mensaje broadcast
    (sin filtro por mención — el LLM decide vía __SKIP__).
  - _on_broadcast_received respeta rate limiter y silencia al superarlo.
  - _respond_to_broadcast ejecuta run_agent.execute, envía texto y re-emite broadcast.
  - _respond_to_broadcast honra el marcador __SKIP__.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.ports.outbound.broadcast_port import BroadcastMessage


@pytest.fixture(autouse=True)
def _sin_jitter(monkeypatch):
    """Elimina el jitter 1-3s del trigger broadcast para que los tests sean inmediatos."""
    monkeypatch.setattr(
        "adapters.inbound.telegram.bot.BROADCAST_TRIGGER_JITTER_MIN_SEC", 0.0
    )
    monkeypatch.setattr(
        "adapters.inbound.telegram.bot.BROADCAST_TRIGGER_JITTER_MAX_SEC", 0.0
    )


@pytest.fixture
def mock_container() -> MagicMock:
    container = MagicMock()
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
# _on_broadcast_received — todo broadcast dispara el pipeline
# ---------------------------------------------------------------------------


def _msg(text: str, chat_id: str = "-100123", agent_id: str = "anacleto") -> BroadcastMessage:
    return BroadcastMessage(
        timestamp=time.time(),
        agent_id=agent_id,
        chat_id=chat_id,
        message=text,
    )


async def test_on_broadcast_dispara_pipeline_sin_filtro(
    agent_cfg_autonomous, mock_container, mock_receiver, mock_emitter, mock_rate_limiter
):
    """Cualquier mensaje broadcast dispara el pipeline — el LLM decide vía __SKIP__."""
    bot = _build_bot(
        agent_cfg_autonomous,
        mock_container,
        receiver=mock_receiver,
        emitter=mock_emitter,
        rate_limiter=mock_rate_limiter,
    )
    await bot._on_broadcast_received(_msg("comentario sobre el clima"))
    mock_container.run_agent.execute.assert_awaited_once()


async def test_on_broadcast_aplica_jitter_aleatorio(
    agent_cfg_autonomous,
    mock_container,
    mock_receiver,
    mock_emitter,
    mock_rate_limiter,
    monkeypatch,
):
    """Antes de disparar, el callback duerme un jitter aleatorio en el rango configurado."""
    monkeypatch.setattr(
        "adapters.inbound.telegram.bot.BROADCAST_TRIGGER_JITTER_MIN_SEC", 1.0
    )
    monkeypatch.setattr(
        "adapters.inbound.telegram.bot.BROADCAST_TRIGGER_JITTER_MAX_SEC", 3.0
    )

    capturado: dict[str, float] = {}

    def _fake_uniform(a: float, b: float) -> float:
        capturado["a"] = a
        capturado["b"] = b
        return 2.0

    sleeps: list[float] = []

    async def _fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("adapters.inbound.telegram.bot.random.uniform", _fake_uniform)
    monkeypatch.setattr("adapters.inbound.telegram.bot.asyncio.sleep", _fake_sleep)

    bot = _build_bot(
        agent_cfg_autonomous,
        mock_container,
        receiver=mock_receiver,
        emitter=mock_emitter,
        rate_limiter=mock_rate_limiter,
    )
    await bot._on_broadcast_received(_msg("hola"))

    assert capturado == {"a": 1.0, "b": 3.0}
    assert 2.0 in sleeps
    mock_container.run_agent.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# _on_broadcast_received — rate limiter
# ---------------------------------------------------------------------------


async def test_on_broadcast_respeta_rate_limiter(
    agent_cfg_autonomous, mock_container, mock_receiver, mock_emitter
):
    """Si rate limiter hace breach → no dispara el pipeline."""
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
    mock_container.run_agent.execute.assert_not_awaited()
    rl.check_and_increment.assert_called_once_with("inaki", "-100123", 5)


# ---------------------------------------------------------------------------
# _respond_to_broadcast — envío y __SKIP__
# ---------------------------------------------------------------------------


async def test_respond_to_broadcast_envia_y_emite_broadcast(
    agent_cfg_autonomous, mock_container, mock_receiver, mock_emitter, mock_rate_limiter
):
    """Respuesta normal → send_message y emit del broadcast re-emitido."""
    bot = _build_bot(
        agent_cfg_autonomous,
        mock_container,
        receiver=mock_receiver,
        emitter=mock_emitter,
        rate_limiter=mock_rate_limiter,
    )
    msg = _msg("inaki, dame la hora")
    await bot._respond_to_broadcast(msg)

    mock_container.run_agent.execute.assert_awaited_once()
    # El input incluye el prefijo de origen
    call_args = mock_container.run_agent.execute.await_args
    assert "anacleto dijo:" in call_args.args[0]
    assert call_args.kwargs.get("channel") == "telegram"
    assert call_args.kwargs.get("chat_id") == "-100123"

    bot._app.bot.send_message.assert_awaited_once()
    # emit fue programado (fire-and-forget) — esperamos un tick del loop
    import asyncio

    await asyncio.sleep(0)
    mock_emitter.emit.assert_awaited()


async def test_respond_to_broadcast_skip_no_envia_ni_emite(
    agent_cfg_autonomous, mock_container, mock_receiver, mock_emitter, mock_rate_limiter
):
    """Respuesta '__SKIP__' → ni send_message ni emit."""
    mock_container.run_agent.execute.return_value = "__SKIP__"
    bot = _build_bot(
        agent_cfg_autonomous,
        mock_container,
        receiver=mock_receiver,
        emitter=mock_emitter,
        rate_limiter=mock_rate_limiter,
    )
    msg = _msg("inaki, cualquier cosa")
    await bot._respond_to_broadcast(msg)

    bot._app.bot.send_message.assert_not_awaited()
    mock_emitter.emit.assert_not_awaited()


async def test_respond_to_broadcast_limpia_extra_sections(
    agent_cfg_autonomous, mock_container, mock_receiver, mock_emitter, mock_rate_limiter
):
    """set_extra_system_sections se limpia al final aunque haya excepción."""
    mock_container.run_agent.execute.side_effect = RuntimeError("boom")
    bot = _build_bot(
        agent_cfg_autonomous,
        mock_container,
        receiver=mock_receiver,
        emitter=mock_emitter,
        rate_limiter=mock_rate_limiter,
    )
    with pytest.raises(RuntimeError):
        await bot._respond_to_broadcast(_msg("inaki test"))

    # La última llamada a set_extra_system_sections debe ser con []
    llamadas = mock_container.run_agent.set_extra_system_sections.call_args_list
    assert llamadas[-1].args[0] == []
    # Channel context también se limpia
    assert mock_container.set_channel_context.call_args_list[-1].args[0] is None
