"""
Test de integración: egress de broadcast en _run_pipeline del TelegramBot.

Usa mocks para las dependencias externas (Telegram, LLM) y objetos reales
para BroadcastBuffer y FixedWindowRateLimiter. Ejercita el flujo completo
de _run_pipeline para un chat de grupo.

Cubre:
- emit() es llamado DESPUÉS de reply_text() (orden de operaciones).
- Fallo en emit() no impide que reply_text() haya sido llamada.
- [SKIP] como respuesta del LLM suprime tanto reply_text como emit.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.inbound.telegram.bot import TelegramBot
from core.domain.services.rate_limiter import FixedWindowRateLimiter


# ---------------------------------------------------------------------------
# Helpers para construir el bot con mocks
# ---------------------------------------------------------------------------


def _make_agent_cfg(behavior: str = "mention") -> MagicMock:
    """Mock de AgentConfig con config de telegram y broadcast."""
    cfg = MagicMock()
    cfg.id = "agente_test"
    cfg.name = "Iñaki Test"
    cfg.description = "Asistente de test"
    cfg.channels = {
        "telegram": {
            "token": "fake-token",
            "allowed_user_ids": [],
            "reactions": False,
            "allowed_chat_ids": [],
            "broadcast": {
                "behavior": behavior,
                "bot_username": "inaki_test_bot",
                "rate_limiter": 5,
            },
        }
    }
    return cfg


def _make_container(respuesta_llm: str = "Respuesta del LLM") -> MagicMock:
    """Mock de AgentContainer con run_agent que devuelve respuesta controlada."""
    container = MagicMock()
    container.run_agent = MagicMock()
    container.run_agent.execute = AsyncMock(return_value=respuesta_llm)
    container.run_agent.set_extra_system_sections = MagicMock()
    container.set_channel_context = MagicMock()
    return container


def _make_update(chat_type: str = "supergroup", chat_id: int = -1001234) -> MagicMock:
    """Mock de telegram.Update para un mensaje de grupo."""
    update = MagicMock()
    update.effective_user.id = 12345
    update.effective_chat.id = chat_id
    update.effective_chat.type = chat_type
    update.message.chat.type = chat_type
    update.message.reply_text = AsyncMock(return_value=None)
    update.message.set_reaction = AsyncMock(return_value=None)
    return update


# ---------------------------------------------------------------------------
# Fixture principal: bot con broadcast emitter mock
# ---------------------------------------------------------------------------


@pytest.fixture
def emit_mock() -> AsyncMock:
    """Mock del BroadcastEmitter.emit — captura llamadas y permite fallos controlados."""
    return AsyncMock(return_value=None)


@pytest.fixture
def bot_fixture(emit_mock):
    """TelegramBot real con mocks inyectados. Retorna (bot, emit_mock, container)."""
    agent_cfg = _make_agent_cfg(behavior="mention")
    container = _make_container()

    rate_limiter = FixedWindowRateLimiter()

    # Mock del emitter con la interface correcta
    emitter = MagicMock()
    emitter.emit = emit_mock

    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app

        bot = TelegramBot(
            agent_cfg=agent_cfg,
            container=container,
            broadcast_emitter=emitter,
            broadcast_receiver=None,
            rate_limiter=rate_limiter,
        )

    return bot, emit_mock, container


# ---------------------------------------------------------------------------
# Test: emit llamado DESPUÉS de reply_text
# ---------------------------------------------------------------------------


async def test_emit_llamado_despues_de_reply_text(bot_fixture):
    """emit() es invocado como fire-and-forget DESPUÉS de que reply_text() completa."""
    bot, emit_mock, container = bot_fixture
    update = _make_update(chat_type="supergroup")

    llamadas: list[str] = []

    # Capturar orden de llamadas
    async def reply_capturado(text, parse_mode=None):
        llamadas.append("reply_text")

    async def emit_capturado(msg):
        llamadas.append("emit")

    update.message.reply_text = AsyncMock(side_effect=reply_capturado)
    emit_mock.side_effect = emit_capturado

    await bot._run_pipeline(update, "hola bot", chat_type="supergroup")

    # Dar tiempo al ensure_future para que el emit corra
    await asyncio.sleep(0.05)

    assert "reply_text" in llamadas
    assert "emit" in llamadas
    assert llamadas.index("reply_text") < llamadas.index("emit")


# ---------------------------------------------------------------------------
# Test: fallo en emit no impide reply_text
# ---------------------------------------------------------------------------


async def test_emit_fallo_no_previene_reply_text(bot_fixture):
    """Si emit() lanza una excepción, reply_text() ya fue invocado (no se cancela)."""
    bot, emit_mock, container = bot_fixture
    update = _make_update(chat_type="supergroup")

    # El emitter falla
    emit_mock.side_effect = RuntimeError("Error de red simulado")

    # No debe propagar la excepción
    await bot._run_pipeline(update, "hola bot", chat_type="supergroup")
    await asyncio.sleep(0.05)

    # reply_text debe haber sido llamado aunque emit falle
    update.message.reply_text.assert_called_once()
    # La excepción del emit no debió propagarse al caller
    # (el test pasó sin lanzar excepción — eso es la verificación)


# ---------------------------------------------------------------------------
# Test: [SKIP] suprime reply_text Y emit
# ---------------------------------------------------------------------------


async def test_skip_marker_suprime_reply_y_emit(emit_mock):
    """Si el LLM responde '[SKIP]', no se llama reply_text ni emit."""
    agent_cfg = _make_agent_cfg(behavior="autonomous")
    container = _make_container(respuesta_llm="[SKIP]")
    emitter = MagicMock()
    emitter.emit = emit_mock

    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app_cls.builder.return_value.token.return_value.build.return_value = MagicMock()

        bot = TelegramBot(
            agent_cfg=agent_cfg,
            container=container,
            broadcast_emitter=emitter,
            broadcast_receiver=None,
        )

    update = _make_update(chat_type="supergroup")

    await bot._run_pipeline(update, "mensaje de grupo", chat_type="supergroup")
    await asyncio.sleep(0.05)

    # Ni reply_text ni emit deben haber sido llamados
    update.message.reply_text.assert_not_called()
    emit_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Test: [SKIP] con espacios también es detectado
# ---------------------------------------------------------------------------


async def test_skip_marker_con_whitespace(emit_mock):
    """'  [SKIP]  ' (con espacios) también activa el marcador."""
    agent_cfg = _make_agent_cfg(behavior="autonomous")
    container = _make_container(respuesta_llm="  [SKIP]  ")
    emitter = MagicMock()
    emitter.emit = emit_mock

    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app_cls.builder.return_value.token.return_value.build.return_value = MagicMock()

        bot = TelegramBot(
            agent_cfg=agent_cfg,
            container=container,
            broadcast_emitter=emitter,
            broadcast_receiver=None,
        )

    update = _make_update(chat_type="supergroup")

    await bot._run_pipeline(update, "pregunta al grupo", chat_type="supergroup")
    await asyncio.sleep(0.05)

    update.message.reply_text.assert_not_called()
    emit_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Test: chat privado NO emite broadcast
# ---------------------------------------------------------------------------


async def test_chat_privado_no_emite_broadcast(bot_fixture):
    """En chats privados, emit() nunca es llamado (broadcast solo en grupos)."""
    bot, emit_mock, container = bot_fixture
    update = _make_update(chat_type="private")

    await bot._run_pipeline(update, "consulta privada", chat_type="private")
    await asyncio.sleep(0.05)

    # reply_text sí, emit no
    update.message.reply_text.assert_called_once()
    emit_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Test: broadcast emitter ausente no falla
# ---------------------------------------------------------------------------


async def test_sin_broadcast_emitter_no_falla():
    """Si broadcast_emitter=None, el pipeline corre sin emitir (no AttributeError)."""
    agent_cfg = _make_agent_cfg(behavior="mention")
    container = _make_container(respuesta_llm="respuesta normal")

    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app_cls.builder.return_value.token.return_value.build.return_value = MagicMock()

        bot = TelegramBot(
            agent_cfg=agent_cfg,
            container=container,
            broadcast_emitter=None,
        )

    update = _make_update(chat_type="supergroup")

    # No debe lanzar excepción
    await bot._run_pipeline(update, "hola", chat_type="supergroup")
    await asyncio.sleep(0.05)

    update.message.reply_text.assert_called_once()
