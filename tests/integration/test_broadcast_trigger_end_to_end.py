"""
Test de integración end-to-end del broadcast-as-trigger bot-to-bot.

Levanta dos TcpBroadcastAdapter (server + client) en localhost, arma dos TelegramBot
con `behavior: autonomous` y verifica que:
  - Cuando el bot A emite un broadcast, B dispara el pipeline, responde vía
    ``send_message`` y re-emite su respuesta. A no reacciona a su propio broadcast
    (anti-loop por agent_id en el adapter TCP).
  - El marcador ``__SKIP__`` suprime ``send_message`` y la re-emisión, pero el
    pipeline igualmente se ejecuta (el LLM decide).
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.broadcast.tcp import TcpBroadcastAdapter
from adapters.inbound.telegram.bot import TelegramBot
from core.domain.services.broadcast_buffer import BroadcastBuffer
from core.domain.services.rate_limiter import FixedWindowRateLimiter
from core.ports.outbound.broadcast_port import BroadcastMessage


AUTH = "secreto-trigger-e2e"
CHAT_ID = "-100987"
HOST = "127.0.0.1"
TICK = 0.05


@pytest.fixture(autouse=True)
def _sin_jitter(monkeypatch):
    """Elimina el jitter 1-3s del trigger broadcast para que los tests sean inmediatos."""
    monkeypatch.setattr(
        "adapters.inbound.telegram.bot.BROADCAST_TRIGGER_JITTER_MIN_SEC", 0.0
    )
    monkeypatch.setattr(
        "adapters.inbound.telegram.bot.BROADCAST_TRIGGER_JITTER_MAX_SEC", 0.0
    )


def _agent_cfg(agent_id: str, bot_username: str) -> MagicMock:
    cfg = MagicMock()
    cfg.id = agent_id
    cfg.name = agent_id.capitalize()
    cfg.description = ""
    cfg.channels = {
        "telegram": {
            "token": "fake-token",
            "allowed_user_ids": [],
            "reactions": False,
            "broadcast": {
                "behavior": "autonomous",
                "bot_username": bot_username,
                "rate_limiter": 5,
            },
        }
    }
    return cfg


def _container(respuesta: str) -> MagicMock:
    c = MagicMock()
    c.run_agent.execute = AsyncMock(return_value=respuesta)
    c.run_agent.set_extra_system_sections = MagicMock()
    c.set_channel_context = MagicMock()
    return c


def _build_bot(agent_cfg, container, emitter, receiver, rate_limiter):
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app.bot.send_message = AsyncMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        return TelegramBot(
            agent_cfg=agent_cfg,
            container=container,
            broadcast_emitter=emitter,
            broadcast_receiver=receiver,
            rate_limiter=rate_limiter,
        )


@pytest.fixture
async def par_bots():
    """Par de bots (A=server, B=client) conectados por TCP broadcast real.

    - A: bot_username='anacleto_bot'
    - B: bot_username='inaki_bot'
    B responde 'Hola desde Iñaki' cuando run_agent.execute es llamado.
    A responde 'Hola desde Anacleto'.
    """
    buf_a = BroadcastBuffer(_now=time.time)
    buf_b = BroadcastBuffer(_now=time.time)

    adapter_a = TcpBroadcastAdapter(
        agent_id="anacleto",
        role="server",
        host=HOST,
        port=0,
        auth=AUTH,
        buffer=buf_a,
    )
    await adapter_a.start()
    for _ in range(10):
        await asyncio.sleep(0)
        if adapter_a._server_obj is not None:
            break
    port = adapter_a._server_obj.sockets[0].getsockname()[1]

    adapter_b = TcpBroadcastAdapter(
        agent_id="inaki",
        role="client",
        host=HOST,
        port=port,
        auth=AUTH,
        buffer=buf_b,
        reconnect_max_backoff=1.0,
    )
    await adapter_b.start()
    await asyncio.sleep(TICK)

    rl = FixedWindowRateLimiter()

    # A responde __SKIP__ por defecto para romper el loop: cuando B re-emite su
    # respuesta, A la recibe pero no vuelve a emitir. Simula un LLM sensato.
    bot_a = _build_bot(
        _agent_cfg("anacleto", "anacleto_bot"),
        _container("__SKIP__"),
        emitter=adapter_a,
        receiver=adapter_a,
        rate_limiter=rl,
    )
    bot_b = _build_bot(
        _agent_cfg("inaki", "inaki_bot"),
        _container("Hola desde Iñaki"),
        emitter=adapter_b,
        receiver=adapter_b,
        rate_limiter=rl,
    )

    await bot_a.subscribe_broadcast_trigger()
    await bot_b.subscribe_broadcast_trigger()

    yield bot_a, bot_b, adapter_a, adapter_b

    await adapter_b.stop()
    await adapter_a.stop()


async def test_broadcast_trigger_dispara_pipeline_del_otro_bot(par_bots):
    """A emite un broadcast → B dispara pipeline, responde y re-emite."""
    bot_a, bot_b, adapter_a, adapter_b = par_bots

    msg_a = BroadcastMessage(
        timestamp=time.time(),
        agent_id="anacleto",
        chat_id=CHAT_ID,
        message="che inaki, qué hora es?",
    )
    await adapter_a.emit(msg_a)
    # Esperar varios ticks para TCP → callback → pipeline → send_message
    for _ in range(20):
        await asyncio.sleep(TICK)
        if bot_b._app.bot.send_message.await_count > 0:
            break

    # B corrió el pipeline
    bot_b._container.run_agent.execute.assert_awaited_once()
    call_args = bot_b._container.run_agent.execute.await_args
    assert "anacleto dijo:" in call_args.args[0]
    assert "che inaki, qué hora es?" in call_args.args[0]
    assert call_args.kwargs.get("channel") == "telegram"
    assert call_args.kwargs.get("chat_id") == CHAT_ID

    # B mandó el mensaje a Telegram (aunque sea mock)
    bot_b._app.bot.send_message.assert_awaited_once()

    # A NO reaccionó a su propio broadcast (anti-loop por agent_id en el adapter).
    # Sí procesó la respuesta de B (retornó __SKIP__ por fixture, sin send_message).
    bot_a._app.bot.send_message.assert_not_awaited()


async def test_broadcast_sin_mencion_igualmente_dispara(par_bots):
    """Cualquier broadcast (aun sin mencionar a B) dispara el pipeline — LLM decide vía __SKIP__."""
    bot_a, bot_b, adapter_a, adapter_b = par_bots

    msg = BroadcastMessage(
        timestamp=time.time(),
        agent_id="anacleto",
        chat_id=CHAT_ID,
        message="hablando solo del clima sin nombrar a nadie",
    )
    await adapter_a.emit(msg)
    for _ in range(20):
        await asyncio.sleep(TICK)
        if bot_b._container.run_agent.execute.await_count > 0:
            break

    # B fire el pipeline aunque no haya mención explícita — el LLM decide
    bot_b._container.run_agent.execute.assert_awaited()
    # A no reaccionó a su propio broadcast
    bot_a._app.bot.send_message.assert_not_awaited()


async def test_broadcast_skip_no_envia(par_bots):
    """B responde '__SKIP__' → ni send_message ni re-emit."""
    bot_a, bot_b, adapter_a, adapter_b = par_bots

    # Sobrescribimos la respuesta de B para que sea __SKIP__
    bot_b._container.run_agent.execute.return_value = "__SKIP__"

    msg = BroadcastMessage(
        timestamp=time.time(),
        agent_id="anacleto",
        chat_id=CHAT_ID,
        message="inaki, cualquier cosa",
    )
    await adapter_a.emit(msg)
    for _ in range(15):
        await asyncio.sleep(TICK)
        if bot_b._container.run_agent.execute.await_count > 0:
            break

    bot_b._container.run_agent.execute.assert_awaited_once()
    bot_b._app.bot.send_message.assert_not_awaited()
