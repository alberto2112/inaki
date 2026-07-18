"""
Unit tests para TelegramBot._announce_back_online.

Cuando el daemon estuvo caído, Telegram acumula los updates no confirmados.
Reproducir un turno de LLM por cada uno dispararía hasta 256 turnos concurrentes
(``concurrent_updates``) sin rate-limit global del provider — ráfaga capaz de
degradar el servicio en cada reinicio. En vez de eso, al arrancar (hook
``post_init``) el bot solo avisa 'online' (cero LLM) a cada chat PRIVADO
autorizado que le escribió mientras dormía, y deja que el usuario reenvíe.

Coverage:
- Un chat con varios mensajes pendientes → un solo aviso (deduplicado).
- Varios chats privados → un aviso a cada uno.
- Grupos excluidos (anunciarse ahí sería ruido para los miembros).
- Privado no autorizado (``allowed_user_ids``) → no recibe aviso.
- El texto es ``BACK_ONLINE_NOTICE`` (universal, solo emojis).
- Se confirma el offset del último pendiente (el updater no los re-entrega).
- Cola vacía → no avisa ni confirma offset.
- Excepción de red al drenar no aborta el arranque.
- Un chat que falla al enviar no impide avisar a los demás.
- Throttle: envío uno a uno espaciado 1s (entre envíos), 1 chat no espera.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.inbound.telegram.bot import BACK_ONLINE_NOTICE, TelegramBot


# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fast_throttle():
    """Anula la espera real de 1s entre avisos para que los tests no duerman."""
    with patch("adapters.inbound.telegram.bot.BACK_ONLINE_NOTICE_DELAY_SEC", 0):
        yield


def _make_bot(allowed_user_ids: list | None = None) -> TelegramBot:
    """Construye un TelegramBot con Application parcheada (sin token real)."""
    settings = MagicMock()
    settings.id = "test-agent"
    settings.telegram = {
        "token": "fake-token",
        "allowed_user_ids": allowed_user_ids or [],
        "reactions": False,
    }
    ports = MagicMock()

    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.connect_timeout.return_value.read_timeout.return_value.write_timeout.return_value.pool_timeout.return_value.build.return_value = mock_app
        bot = TelegramBot(settings, ports)

    return bot


def _upd(
    update_id: int,
    chat_id: int,
    *,
    user_id: int = 7,
    chat_type: str = "private",
) -> SimpleNamespace:
    """Update de Telegram falso con los campos que lee el aviso de arranque."""
    return SimpleNamespace(
        update_id=update_id,
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        effective_user=SimpleNamespace(id=user_id),
    )


def _fake_app(pending: list) -> MagicMock:
    """App falsa: get_updates devuelve ``pending`` y luego [] (confirmación de offset)."""
    app = MagicMock()
    app.bot.get_updates = AsyncMock(side_effect=[list(pending), []])
    app.bot.send_message = AsyncMock()
    return app


def _avisados(app: MagicMock) -> list[int]:
    return sorted(call.kwargs["chat_id"] for call in app.bot.send_message.await_args_list)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_un_aviso_por_chat_con_varios_mensajes():
    """Tres mensajes del mismo chat → un solo aviso (deduplicado)."""
    bot = _make_bot()
    app = _fake_app([_upd(1, 100), _upd(2, 100), _upd(3, 100)])

    await bot._announce_back_online(app)

    assert _avisados(app) == [100]


async def test_avisa_a_cada_chat_privado():
    """Cada chat privado distinto recibe su aviso."""
    bot = _make_bot()
    app = _fake_app([_upd(1, 100), _upd(2, 200)])

    await bot._announce_back_online(app)

    assert _avisados(app) == [100, 200]


async def test_excluye_grupos():
    """Un grupo con actividad pendiente NO recibe aviso; el privado sí."""
    bot = _make_bot()
    app = _fake_app([_upd(1, -555, chat_type="supergroup"), _upd(2, 100, chat_type="private")])

    await bot._announce_back_online(app)

    assert _avisados(app) == [100]


async def test_excluye_privado_no_autorizado():
    """Si el emisor no está en allowed_user_ids, no recibe aviso."""
    bot = _make_bot(allowed_user_ids=[999])
    app = _fake_app([_upd(1, 100, user_id=42)])

    await bot._announce_back_online(app)

    app.bot.send_message.assert_not_awaited()


async def test_texto_del_aviso_es_universal():
    """El aviso usa BACK_ONLINE_NOTICE (solo emojis, sin idioma)."""
    bot = _make_bot()
    app = _fake_app([_upd(1, 100)])

    await bot._announce_back_online(app)

    assert app.bot.send_message.await_args.kwargs["text"] == BACK_ONLINE_NOTICE


async def test_confirma_offset_del_ultimo_pendiente():
    """La segunda llamada a get_updates confirma offset = último update_id + 1."""
    bot = _make_bot()
    app = _fake_app([_upd(5, 100), _upd(9, 100)])

    await bot._announce_back_online(app)

    assert app.bot.get_updates.await_count == 2
    assert app.bot.get_updates.await_args_list[1].kwargs["offset"] == 10


async def test_cola_vacia_no_avisa():
    """Sin pendientes: no avisa y no confirma offset (una sola llamada)."""
    bot = _make_bot()
    app = MagicMock()
    app.bot.get_updates = AsyncMock(return_value=[])
    app.bot.send_message = AsyncMock()

    await bot._announce_back_online(app)

    app.bot.send_message.assert_not_awaited()
    assert app.bot.get_updates.await_count == 1


async def test_excepcion_en_get_updates_no_aborta_el_arranque():
    """Si get_updates falla (red caída), no propaga y no avisa."""
    bot = _make_bot()
    app = MagicMock()
    app.bot.get_updates = AsyncMock(side_effect=RuntimeError("telegram down"))
    app.bot.send_message = AsyncMock()

    await bot._announce_back_online(app)  # no debe levantar

    app.bot.send_message.assert_not_awaited()


async def test_un_chat_que_falla_no_aborta_los_demas():
    """Si el envío a un chat falla (bloqueado/borrado), los demás se intentan igual."""
    bot = _make_bot()
    app = _fake_app([_upd(1, 100), _upd(2, 200)])
    app.bot.send_message = AsyncMock(side_effect=[RuntimeError("blocked"), None])

    await bot._announce_back_online(app)  # no debe levantar

    assert app.bot.send_message.await_count == 2


async def test_throttle_entre_envios():
    """N chats → N-1 esperas (el sleep va entre envíos, no después del último)."""
    bot = _make_bot()
    app = _fake_app([_upd(1, 100), _upd(2, 200), _upd(3, 300)])

    with patch("adapters.inbound.telegram.bot.asyncio.sleep", new=AsyncMock()) as mock_sleep:
        await bot._announce_back_online(app)

    assert app.bot.send_message.await_count == 3
    assert mock_sleep.await_count == 2


async def test_un_solo_chat_no_espera():
    """Un único chat no agrega latencia de throttle al arranque."""
    bot = _make_bot()
    app = _fake_app([_upd(1, 100)])

    with patch("adapters.inbound.telegram.bot.asyncio.sleep", new=AsyncMock()) as mock_sleep:
        await bot._announce_back_online(app)

    app.bot.send_message.assert_awaited_once()
    mock_sleep.assert_not_awaited()
