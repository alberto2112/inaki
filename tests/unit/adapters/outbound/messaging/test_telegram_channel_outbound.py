"""Tests para TelegramChannelOutbound."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from telegram.constants import ParseMode
from telegram.error import BadRequest

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
# record_history — dueño único del rastro (outbound-send-single-owner)
# ---------------------------------------------------------------------------


async def test_record_history_false_envia_pero_no_persiste(fake_bot, fake_history, tmp_path):
    """El caller ya es dueño del rastro (el tool loop persiste la tool call):
    el adapter envía igual pero NO agrega su fila — duplicaría, y caería DENTRO
    del grupo protocolar del turno."""
    adapter = _adapter(fake_bot, fake_history)
    foto = _archivo(tmp_path)

    await adapter.send(
        chat_id="42",
        kind=OutboundKind.PHOTO,
        sources=[foto],
        caption="mirá esto",
        record_history=False,
    )

    fake_bot.send_photo.assert_awaited_once()
    fake_history.append.assert_not_awaited()


async def test_record_history_true_es_el_default(fake_bot, fake_history):
    """Fuera de un turno (scheduler, REST admin) nadie más registra: el adapter
    sigue siendo el dueño sin que el caller tenga que pedirlo."""
    adapter = _adapter(fake_bot, fake_history)

    await adapter.send(chat_id="42", kind=OutboundKind.TEXT, text="hola")

    fake_history.append.assert_awaited_once()


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------


def test_capabilities_incluye_todos_los_kinds(fake_bot, fake_history):
    adapter = _adapter(fake_bot, fake_history)
    caps = adapter.capabilities()

    for kind in OutboundKind:
        assert kind in caps, f"Se esperaba {kind} en capabilities()"


# ---------------------------------------------------------------------------
# ALBUM — formateo del caption (el único que no pasa por bot.send_photo)
# ---------------------------------------------------------------------------


async def test_album_renderiza_el_caption_a_html(fake_bot, fake_history, tmp_path):
    """El caption del álbum viaja en el primer InputMediaPhoto, no como kwarg.

    Por eso este camino repite el renderizado en vez de heredarlo de
    ``TelegramBot.send_photo``: si no, el álbum sería el único que sigue
    mostrando los asteriscos crudos.
    """
    adapter = _adapter(fake_bot, fake_history)
    fotos = [_archivo(tmp_path, f"{i}.jpg") for i in range(3)]

    await adapter.send(
        chat_id="-100",
        kind=OutboundKind.ALBUM,
        sources=fotos,
        caption="las **tres** fotos",
    )

    media = fake_bot.send_media_group.call_args.kwargs["media"]
    assert media[0].caption == "las <b>tres</b> fotos"
    assert media[0].parse_mode == ParseMode.HTML
    assert getattr(media[1], "caption", None) is None, "Solo la primera lleva caption"

    # El historial guarda el caption CRUDO: es el texto del dominio, no HTML de transporte.
    msg = fake_history.append.call_args.args[1]
    assert msg.content == "las **tres** fotos"


async def test_album_fallback_rebobina_todos_los_handles(fake_bot, fake_history, tmp_path):
    """Si Telegram rechaza el caption HTML, el reintento va crudo y con los ficheros enteros.

    ``InputMediaPhoto`` lee el handle al CONSTRUIRSE (lo envuelve en un
    ``InputFile``), no al enviarse. Sin el ``seek(0)`` previo, el álbum del
    reintento se armaría con tres ficheros VACÍOS: el usuario recibiría el
    caption bien formateado y las fotos rotas.
    """
    adapter = _adapter(fake_bot, fake_history)
    fotos = [_archivo(tmp_path, f"{i}.jpg") for i in range(3)]
    subidos: list[list[bytes]] = []

    async def rechazar_una_vez(*, chat_id, media):
        subidos.append([m.media.input_file_content for m in media])
        if len(subidos) == 1:
            raise BadRequest("Can't parse entities: unsupported start tag")

    fake_bot.send_media_group = AsyncMock(side_effect=rechazar_una_vez)

    await adapter.send(
        chat_id="-100",
        kind=OutboundKind.ALBUM,
        sources=fotos,
        caption="roto **abierto",
    )

    assert fake_bot.send_media_group.await_count == 2
    media = fake_bot.send_media_group.call_args.kwargs["media"]
    assert media[0].caption == "roto **abierto", "El reintento manda el caption crudo"
    assert media[0].parse_mode is None
    assert subidos[1] == subidos[0], "El reintento debe subir los mismos bytes"
    assert all(b for b in subidos[1]), "Ningún fichero puede subirse vacío"


async def test_album_badrequest_ajeno_al_parseo_se_propaga(fake_bot, fake_history, tmp_path):
    """Un chat inexistente no se disfraza de problema de formato."""
    adapter = _adapter(fake_bot, fake_history)
    fotos = [_archivo(tmp_path, f"{i}.jpg") for i in range(3)]
    fake_bot.send_media_group = AsyncMock(side_effect=BadRequest("Chat not found"))

    with pytest.raises(BadRequest, match="Chat not found"):
        await adapter.send(
            chat_id="-100",
            kind=OutboundKind.ALBUM,
            sources=fotos,
            caption="las **tres**",
        )

    assert fake_bot.send_media_group.await_count == 1, "No debe reintentar"
    fake_history.append.assert_not_awaited(), "Un envío fallido no se persiste"
