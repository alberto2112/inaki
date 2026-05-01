"""Tests para el helper de emisión de eventos broadcast en TelegramBot.

Cubre la lógica de gating por flag y la construcción del BroadcastMessage
para los 3 event_types: assistant_response, user_input_voice, user_input_photo.

El helper ``_emit_event`` centraliza la decisión de emitir o no según
``emit.{event_type}`` y delega la construcción + serialización al adapter TCP.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mk_agent_cfg(
    *,
    emit_assistant_response: bool = True,
    emit_user_input_voice: bool = False,
    emit_user_input_photo: bool = False,
) -> MagicMock:
    """Construye un AgentConfig mock con flags emit configurables.

    El bloque telegram.broadcast.emit usa un MagicMock con model_dump() para
    simular el Pydantic BroadcastConfig real.
    """
    cfg = MagicMock()
    cfg.id = "agente_a"
    cfg.name = "Iñaki"
    cfg.description = "Asistente"
    cfg.channels = {
        "telegram": {
            "token": "dummy-token",
            "allowed_user_ids": [],
            "voice_enabled": True,
            "broadcast": {
                "behavior": "mention",
                "rate_limiter": 5,
                "emit": {
                    "assistant_response": emit_assistant_response,
                    "user_input_voice": emit_user_input_voice,
                    "user_input_photo": emit_user_input_photo,
                },
            },
        }
    }
    cfg.transcription = MagicMock()
    cfg.transcription.max_audio_mb = 25
    cfg.transcription.language = None
    return cfg


def _build_bot(agent_cfg, *, emitter=None):
    """Instancia TelegramBot mockeando Application + container."""
    container = MagicMock()
    container.transcription = MagicMock()
    container.run_agent = MagicMock()
    container.set_channel_context = MagicMock()

    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        return TelegramBot(
            agent_cfg=agent_cfg,
            container=container,
            broadcast_emitter=emitter,
        )


# ---------------------------------------------------------------------------
# emit.assistant_response gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_assistant_response_con_flag_true_emite():
    """Con emit.assistant_response=true, _emit_event emite al adapter."""
    cfg = _mk_agent_cfg(emit_assistant_response=True)
    emitter = MagicMock()
    emitter.emit = AsyncMock()
    bot = _build_bot(cfg, emitter=emitter)

    await bot._emit_event(
        event_type="assistant_response",
        chat_id="-100123",
        content="hola humano",
        sender="",
    )

    emitter.emit.assert_called_once()
    msg = emitter.emit.call_args.args[0]
    assert msg.event_type == "assistant_response"
    assert msg.content == "hola humano"
    assert msg.sender == ""
    assert msg.agent_id == "agente_a"
    assert msg.chat_id == "-100123"


@pytest.mark.asyncio
async def test_emit_assistant_response_con_flag_false_no_emite():
    """Con emit.assistant_response=false, _emit_event NO llama al adapter."""
    cfg = _mk_agent_cfg(emit_assistant_response=False)
    emitter = MagicMock()
    emitter.emit = AsyncMock()
    bot = _build_bot(cfg, emitter=emitter)

    await bot._emit_event(
        event_type="assistant_response",
        chat_id="-100123",
        content="hola",
        sender="",
    )

    emitter.emit.assert_not_called()


# ---------------------------------------------------------------------------
# emit.user_input_voice gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_user_input_voice_con_flag_true_emite_con_sender():
    """Con flag voice=true, emite con event_type='user_input_voice' y sender humano."""
    cfg = _mk_agent_cfg(emit_user_input_voice=True)
    emitter = MagicMock()
    emitter.emit = AsyncMock()
    bot = _build_bot(cfg, emitter=emitter)

    await bot._emit_event(
        event_type="user_input_voice",
        chat_id="-100123",
        content="cuánto es 5+5",
        sender="alberto",
    )

    emitter.emit.assert_called_once()
    msg = emitter.emit.call_args.args[0]
    assert msg.event_type == "user_input_voice"
    assert msg.sender == "alberto"
    assert msg.content == "cuánto es 5+5"


@pytest.mark.asyncio
async def test_emit_user_input_voice_con_flag_false_no_emite():
    """Con flag voice=false, _emit_event NO emite el evento voice."""
    cfg = _mk_agent_cfg(emit_user_input_voice=False)
    emitter = MagicMock()
    emitter.emit = AsyncMock()
    bot = _build_bot(cfg, emitter=emitter)

    await bot._emit_event(
        event_type="user_input_voice",
        chat_id="-100123",
        content="hola",
        sender="alberto",
    )

    emitter.emit.assert_not_called()


# ---------------------------------------------------------------------------
# emit.user_input_photo gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_user_input_photo_con_flag_true_emite():
    """Con flag photo=true, emite con event_type='user_input_photo'."""
    cfg = _mk_agent_cfg(emit_user_input_photo=True)
    emitter = MagicMock()
    emitter.emit = AsyncMock()
    bot = _build_bot(cfg, emitter=emitter)

    await bot._emit_event(
        event_type="user_input_photo",
        chat_id="-100123",
        content="persona caminando",
        sender="alberto",
    )

    emitter.emit.assert_called_once()
    msg = emitter.emit.call_args.args[0]
    assert msg.event_type == "user_input_photo"
    assert msg.sender == "alberto"
    assert msg.content == "persona caminando"


@pytest.mark.asyncio
async def test_emit_user_input_photo_con_flag_false_no_emite():
    """Con flag photo=false, _emit_event NO emite el evento photo."""
    cfg = _mk_agent_cfg(emit_user_input_photo=False)
    emitter = MagicMock()
    emitter.emit = AsyncMock()
    bot = _build_bot(cfg, emitter=emitter)

    await bot._emit_event(
        event_type="user_input_photo",
        chat_id="-100123",
        content="x",
        sender="alberto",
    )

    emitter.emit.assert_not_called()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_content_vacio_post_strip_no_emite():
    """Si content es vacío o solo whitespace, _emit_event NO emite (silencioso)."""
    cfg = _mk_agent_cfg(emit_assistant_response=True)
    emitter = MagicMock()
    emitter.emit = AsyncMock()
    bot = _build_bot(cfg, emitter=emitter)

    await bot._emit_event(
        event_type="assistant_response",
        chat_id="-100123",
        content="   ",
        sender="",
    )

    emitter.emit.assert_not_called()


@pytest.mark.asyncio
async def test_emit_sin_emitter_no_falla():
    """Si broadcast_emitter es None, _emit_event no falla (early return)."""
    cfg = _mk_agent_cfg(emit_assistant_response=True)
    bot = _build_bot(cfg, emitter=None)

    # No debe lanzar excepción
    await bot._emit_event(
        event_type="assistant_response",
        chat_id="-100123",
        content="hola",
        sender="",
    )
