"""Tests para TelegramChannelOutbound."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from adapters.outbound.messaging.telegram_channel_outbound import TelegramChannelOutbound
from core.domain.entities.message import Role
from core.domain.value_objects.outbound_kind import OutboundKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_bot() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def fake_history() -> AsyncMock:
    return AsyncMock()


def _adapter(
    bot,
    history: AsyncMock,
    agent_id: str = "agente-x",
) -> TelegramChannelOutbound:
    return TelegramChannelOutbound(
        get_telegram_bot=lambda: bot,
        history=history,
        agent_id=agent_id,
    )


def _archivo(tmp_path: Path, nombre: str = "foto.jpg") -> Path:
    p = tmp_path / nombre
    p.write_bytes(b"\xff\xd8\xff")  # magic JPEG mínimo
    return p


# ---------------------------------------------------------------------------
# TEXT — happy path + persistencia
# ---------------------------------------------------------------------------


async def test_text_llama_send_message_y_persiste(fake_bot, fake_history):
    adapter = _adapter(fake_bot, fake_history)

    await adapter.send(chat_id="-100", kind=OutboundKind.TEXT, text="hola che")

    fake_bot.send_message.assert_awaited_once_with(chat_id=-100, text="hola che")

    # Verifica que se persistió en historial
    fake_history.append.assert_awaited_once()
    args = fake_history.append.call_args
    assert args.args[0] == "agente-x"
    msg = args.args[1]
    assert msg.role == Role.ASSISTANT
    assert msg.content == "hola che"
    assert args.kwargs["channel"] == "telegram"
    assert args.kwargs["chat_id"] == "-100"


async def test_text_vacio_lanza_value_error(fake_bot, fake_history):
    adapter = _adapter(fake_bot, fake_history)

    with pytest.raises(ValueError, match="vacío"):
        await adapter.send(chat_id="42", kind=OutboundKind.TEXT, text="   ")

    fake_bot.send_message.assert_not_awaited()
    fake_history.append.assert_not_awaited()


async def test_text_none_lanza_value_error(fake_bot, fake_history):
    adapter = _adapter(fake_bot, fake_history)

    with pytest.raises(ValueError, match="vacío"):
        await adapter.send(chat_id="42", kind=OutboundKind.TEXT, text=None)


# ---------------------------------------------------------------------------
# PHOTO — happy path + persistencia
# ---------------------------------------------------------------------------


async def test_photo_llama_send_photo_y_persiste(fake_bot, fake_history, tmp_path):
    adapter = _adapter(fake_bot, fake_history)
    foto = _archivo(tmp_path)

    await adapter.send(
        chat_id="-100",
        kind=OutboundKind.PHOTO,
        sources=[foto],
        caption="linda foto",
    )

    fake_bot.send_photo.assert_awaited_once()
    kwargs = fake_bot.send_photo.call_args.kwargs
    assert kwargs["chat_id"] == -100
    assert kwargs["caption"] == "linda foto"
    # El handle debe haberse cerrado
    assert kwargs["photo"].closed is True

    # Historial con caption
    fake_history.append.assert_awaited_once()
    msg = fake_history.append.call_args.args[1]
    assert msg.content == "linda foto"


async def test_photo_sin_caption_persiste_string_vacio(fake_bot, fake_history, tmp_path):
    adapter = _adapter(fake_bot, fake_history)
    foto = _archivo(tmp_path)

    await adapter.send(chat_id="42", kind=OutboundKind.PHOTO, sources=[foto])

    msg = fake_history.append.call_args.args[1]
    assert msg.content == ""


async def test_photo_multiples_sources_lanza_value_error(fake_bot, fake_history, tmp_path):
    adapter = _adapter(fake_bot, fake_history)
    a = _archivo(tmp_path, "a.jpg")
    b = _archivo(tmp_path, "b.jpg")

    with pytest.raises(ValueError, match="exactamente 1 source"):
        await adapter.send(chat_id="42", kind=OutboundKind.PHOTO, sources=[a, b])

    fake_bot.send_photo.assert_not_awaited()


async def test_photo_archivo_inexistente_lanza_file_not_found(fake_bot, fake_history, tmp_path):
    adapter = _adapter(fake_bot, fake_history)

    with pytest.raises(FileNotFoundError):
        await adapter.send(
            chat_id="42", kind=OutboundKind.PHOTO, sources=[tmp_path / "no-existe.jpg"]
        )

    fake_history.append.assert_not_awaited()


# ---------------------------------------------------------------------------
# ALBUM con 1 foto — delega a PHOTO
# ---------------------------------------------------------------------------


async def test_album_un_solo_archivo_delega_a_photo(fake_bot, fake_history, tmp_path):
    adapter = _adapter(fake_bot, fake_history)
    foto = _archivo(tmp_path, "una.jpg")

    await adapter.send(
        chat_id="-100",
        kind=OutboundKind.ALBUM,
        sources=[foto],
        caption="sola",
    )

    # Debe haber usado send_photo, no send_media_group
    fake_bot.send_photo.assert_awaited_once()
    fake_bot.send_media_group.assert_not_awaited()
    fake_history.append.assert_awaited_once()


# ---------------------------------------------------------------------------
# ALBUM con 3 fotos — send_media_group
# ---------------------------------------------------------------------------


async def test_album_multiples_llama_send_media_group(fake_bot, fake_history, tmp_path):
    adapter = _adapter(fake_bot, fake_history)
    fotos = [_archivo(tmp_path, f"{i}.jpg") for i in range(3)]

    await adapter.send(
        chat_id="-100",
        kind=OutboundKind.ALBUM,
        sources=fotos,
        caption="las tres",
    )

    fake_bot.send_media_group.assert_awaited_once()
    kwargs = fake_bot.send_media_group.call_args.kwargs
    assert kwargs["chat_id"] == -100
    media = kwargs["media"]
    assert len(media) == 3
    # El caption va en la primera foto
    assert media[0].caption == "las tres"
    assert getattr(media[1], "caption", None) is None

    # Historial con caption
    msg = fake_history.append.call_args.args[1]
    assert msg.content == "las tres"


async def test_album_vacio_lanza_value_error(fake_bot, fake_history):
    adapter = _adapter(fake_bot, fake_history)

    with pytest.raises(ValueError, match="al menos 1 source"):
        await adapter.send(chat_id="42", kind=OutboundKind.ALBUM, sources=[])

    fake_history.append.assert_not_awaited()


async def test_album_archivo_inexistente_lanza_file_not_found(fake_bot, fake_history, tmp_path):
    adapter = _adapter(fake_bot, fake_history)
    a = _archivo(tmp_path, "a.jpg")

    with pytest.raises(FileNotFoundError):
        await adapter.send(
            chat_id="42",
            kind=OutboundKind.ALBUM,
            sources=[a, tmp_path / "no-existe.jpg"],
        )

    fake_bot.send_media_group.assert_not_awaited()
    fake_history.append.assert_not_awaited()


# ---------------------------------------------------------------------------
# Validación: kind no soportado
# ---------------------------------------------------------------------------


async def test_kind_no_soportado_lanza_value_error(fake_bot, fake_history):
    """Verificación defensiva — OutboundKind tiene todos los values soportados,
    pero el adapter debe rechazar kinds futuros que no estén en capabilities()."""
    adapter = _adapter(fake_bot, fake_history)

    # Creamos un kind "fantasma" que no está en capabilities
    # usando un mock que simule el comportamiento
    from unittest.mock import MagicMock

    kind_invalido = MagicMock(spec=OutboundKind)
    kind_invalido.__class__ = OutboundKind

    # Workaround: parcheamos capabilities para devolver un set vacío
    original_capabilities = adapter.capabilities

    def capabilities_vacias():
        return set()

    adapter.capabilities = capabilities_vacias  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="no soporta"):
        await adapter.send(chat_id="42", kind=OutboundKind.TEXT, text="hola")

    # Restaurar
    adapter.capabilities = original_capabilities  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Bot None
# ---------------------------------------------------------------------------


async def test_bot_none_lanza_runtime_error(fake_history, tmp_path):
    adapter = TelegramChannelOutbound(
        get_telegram_bot=lambda: None,
        history=fake_history,
        agent_id="agente-x",
    )

    with pytest.raises(RuntimeError, match="Telegram no está disponible"):
        await adapter.send(chat_id="42", kind=OutboundKind.TEXT, text="hola")

    fake_history.append.assert_not_awaited()


async def test_bot_none_en_photo_lanza_runtime_error(fake_history, tmp_path):
    adapter = TelegramChannelOutbound(
        get_telegram_bot=lambda: None,
        history=fake_history,
        agent_id="agente-x",
    )
    foto = _archivo(tmp_path)

    with pytest.raises(RuntimeError, match="Telegram no está disponible"):
        await adapter.send(chat_id="42", kind=OutboundKind.PHOTO, sources=[foto])

    fake_history.append.assert_not_awaited()


# ---------------------------------------------------------------------------
# chat_id inválido
# ---------------------------------------------------------------------------


async def test_chat_id_no_entero_lanza_value_error(fake_bot, fake_history):
    adapter = _adapter(fake_bot, fake_history)

    with pytest.raises(ValueError, match="entero"):
        await adapter.send(chat_id="no-es-entero", kind=OutboundKind.TEXT, text="hola")


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------


def test_capabilities_incluye_todos_los_kinds(fake_bot, fake_history):
    adapter = _adapter(fake_bot, fake_history)
    caps = adapter.capabilities()

    for kind in OutboundKind:
        assert kind in caps, f"Se esperaba {kind} en capabilities()"
