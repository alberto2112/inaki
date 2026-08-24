"""Tests para el formateo de la salida de ``TelegramBot`` (texto y captions).

``send_message`` es la ÚNICA salida de texto de todo lo que NO es una respuesta
conversacional: el sink del scheduler (``channel_send``), los intermedios en vivo
del tool loop, los resultados de delegación en background y
``TelegramChannelOutbound``. Los cuatro resuelven el bot vía ``get_telegram_bot``
y terminan en este método.

Durante un tiempo el renderizado markdown → HTML vivía SOLO en los tres
call-sites conversacionales (``bot.py``, ``group_flow.py``, ``media.py``), así que
todos esos caminos entregaban el markdown crudo: el usuario veía ``**negrita**``
con los asteriscos a la vista, y un mensaje de más de 4096 chars rebotaba con
"message is too long". Estos tests fijan el contrato en el borde del transporte.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.constants import ParseMode
from telegram.error import BadRequest


@pytest.fixture
def agent_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.id = "inaki"
    cfg.name = "Inaki"
    cfg.description = "Asistente"
    cfg.telegram = {"token": "dummy-token", "allowed_user_ids": []}
    return cfg


def _build_bot(agent_cfg: MagicMock) -> tuple[Any, Any]:
    """Devuelve ``(bot, mock_app)`` con ``mock_app.bot.send_message`` espiable."""
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app.bot.send_message = AsyncMock()
        builder = mock_app_cls.builder.return_value
        builder.token.return_value.concurrent_updates.return_value.connect_timeout.return_value.read_timeout.return_value.write_timeout.return_value.pool_timeout.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        return TelegramBot(settings=agent_cfg, ports=MagicMock()), mock_app


class _FakeHandle:
    """Handle de fichero mínimo: registra sus rebobinados sin tocar disco."""

    def __init__(self) -> None:
        self.seeks: list[int] = []

    def seek(self, pos: int) -> None:
        self.seeks.append(pos)


async def test_send_message_renderiza_markdown_a_html(agent_cfg) -> None:
    """El markdown del LLM debe llegar como HTML de Telegram, no con asteriscos crudos."""
    bot, mock_app = _build_bot(agent_cfg)

    await bot.send_message(123, "Conclusión: **POLKA** es el ensayo *NCT05898399*.")

    mock_app.bot.send_message.assert_awaited_once()
    kwargs = mock_app.bot.send_message.await_args.kwargs
    assert kwargs["parse_mode"] == ParseMode.HTML, "Debe enviarse con parse_mode HTML"
    assert "<b>POLKA</b>" in kwargs["text"], "El bold markdown debe bajar a <b>"
    assert "<i>NCT05898399</i>" in kwargs["text"], "El italic markdown debe bajar a <i>"
    assert "**" not in kwargs["text"], "No deben quedar asteriscos crudos"


async def test_send_message_fallback_a_texto_plano_si_telegram_rechaza_el_html(
    agent_cfg,
) -> None:
    """Si Telegram no puede parsear el HTML, el usuario recibe el texto igual.

    Vale más leer el contenido crudo que perder la respuesta detrás de un 400.
    """
    bot, mock_app = _build_bot(agent_cfg)
    mock_app.bot.send_message.side_effect = [
        BadRequest("Can't parse entities: unsupported start tag"),
        None,
    ]

    await bot.send_message(123, "texto **con** formato")

    assert mock_app.bot.send_message.await_count == 2, "Debe reintentar una vez"
    reintento = mock_app.bot.send_message.await_args.kwargs
    assert reintento["parse_mode"] is None, "El reintento va sin parse_mode"
    assert reintento["text"] == "texto **con** formato", "El reintento manda el crudo"


async def test_send_message_no_atrapa_badrequest_ajeno_al_parseo(agent_cfg) -> None:
    """Un ``BadRequest`` que no es de parseo (chat inexistente) se propaga."""
    bot, mock_app = _build_bot(agent_cfg)
    mock_app.bot.send_message.side_effect = BadRequest("Chat not found")

    with pytest.raises(BadRequest, match="Chat not found"):
        await bot.send_message(123, "hola")

    assert mock_app.bot.send_message.await_count == 1, "No debe reintentar"


async def test_send_message_trocea_respuestas_largas(agent_cfg) -> None:
    """Un texto de más de 4096 chars debe salir en varios mensajes, no rebotar."""
    bot, mock_app = _build_bot(agent_cfg)
    largo = "\n".join(f"linea {i} de la investigación" for i in range(400))
    assert len(largo) > 4096, "Fixture inválida: el texto debe exceder el límite"

    await bot.send_message(123, largo)

    assert mock_app.bot.send_message.await_count > 1, "Debe partirse en fragmentos"
    for call in mock_app.bot.send_message.await_args_list:
        assert len(call.kwargs["text"]) <= 4096, "Ningún fragmento puede pasar el límite"


async def test_send_message_respeta_el_chat_id_en_cada_fragmento(agent_cfg) -> None:
    """El troceo no debe perder el destino: todos los fragmentos van al mismo chat."""
    bot, mock_app = _build_bot(agent_cfg)
    largo = "\n".join(f"linea {i} de la investigación" for i in range(400))

    await bot.send_message(-1001582404077, largo)

    destinos = {c.kwargs["chat_id"] for c in mock_app.bot.send_message.await_args_list}
    assert destinos == {-1001582404077}


# ---------------------------------------------------------------------------
# Captions de media — mismo bug que el texto, pero con límite de 1024
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metodo", "kwarg", "spy"),
    [
        ("send_photo", "photo", "send_photo"),
        ("send_audio", "audio", "send_audio"),
        ("send_video", "video", "send_video"),
        ("send_document", "document", "send_document"),
    ],
)
async def test_media_renderiza_el_caption_a_html(agent_cfg, metodo, kwarg, spy) -> None:
    """Los cuatro envíos de media deben formatear su caption, no mandarlo crudo."""
    bot, mock_app = _build_bot(agent_cfg)
    setattr(mock_app.bot, spy, AsyncMock())

    await getattr(bot, metodo)(123, _FakeHandle(), caption="mirá el **gráfico**")

    kwargs = getattr(mock_app.bot, spy).await_args.kwargs
    assert kwargs["caption"] == "mirá el <b>gráfico</b>"
    assert kwargs["parse_mode"] == ParseMode.HTML
    assert kwarg in kwargs, f"El media debe seguir viajando como '{kwarg}'"


async def test_send_photo_sin_caption_no_manda_parse_mode(agent_cfg) -> None:
    """Una foto pelada no debe inventar un caption ni un parse_mode."""
    bot, mock_app = _build_bot(agent_cfg)
    spy: Any = AsyncMock()
    mock_app.bot.send_photo = spy

    await bot.send_photo(123, _FakeHandle())

    kwargs = spy.await_args.kwargs
    assert kwargs["caption"] is None
    assert kwargs["parse_mode"] is None


async def test_send_photo_fallback_rebobina_el_handle(agent_cfg) -> None:
    """Ante un caption HTML rechazado, el reintento debe rebobinar el fichero.

    Sin el ``seek(0)`` la foto se subiría VACÍA: ptb ya consumió el stream.
    """
    bot, mock_app = _build_bot(agent_cfg)
    handle = _FakeHandle()
    spy: Any = AsyncMock(
        side_effect=[BadRequest("Can't parse entities: unsupported start tag"), None]
    )
    mock_app.bot.send_photo = spy

    await bot.send_photo(123, handle, caption="roto **abierto")

    assert spy.await_count == 2
    reintento = spy.await_args.kwargs
    assert reintento["caption"] == "roto **abierto", "El reintento manda el crudo"
    assert reintento["parse_mode"] is None
    assert handle.seeks == [0], "El handle debe rebobinarse antes del reintento"


async def test_send_photo_caption_largo_degrada_a_crudo(agent_cfg) -> None:
    """Un caption cuyo HTML se pasa de 1024 va crudo — no se puede trocear."""
    bot, mock_app = _build_bot(agent_cfg)
    spy: Any = AsyncMock()
    mock_app.bot.send_photo = spy
    crudo = "<" * 300  # 300 crudos, 1200 escapados

    await bot.send_photo(123, _FakeHandle(), caption=crudo)

    kwargs = spy.await_args.kwargs
    assert kwargs["caption"] == crudo
    assert kwargs["parse_mode"] is None
