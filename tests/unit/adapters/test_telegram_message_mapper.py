"""Tests para el renderer markdown → Telegram HTML."""

from __future__ import annotations

import httpx
import pytest
from telegram.constants import ParseMode
from telegram.error import BadRequest, TimedOut

from adapters.inbound.telegram.message_mapper import (
    _envio_no_llego,
    format_caption,
    format_response,
    send_caption_or_plain,
    send_html_or_plain,
    split_message,
)


def test_empty_response_returns_empty():
    assert format_response("") == ""


def test_plain_text_passthrough():
    assert format_response("hola mundo") == "hola mundo"


def test_escapes_html_entities_in_plain_text():
    assert format_response("1 < 2 && 3 > 2") == "1 &lt; 2 &amp;&amp; 3 &gt; 2"


def test_bold_maps_to_b_tag():
    assert format_response("**hola**") == "<b>hola</b>"


def test_italic_maps_to_i_tag():
    assert format_response("*hola*") == "<i>hola</i>"


def test_strikethrough_maps_to_s_tag():
    assert format_response("~~hola~~") == "<s>hola</s>"


def test_inline_code_maps_to_code_tag():
    assert format_response("usá `print()` nomás") == "usá <code>print()</code> nomás"


def test_inline_code_escapes_entities():
    assert format_response("`a<b>`") == "<code>a&lt;b&gt;</code>"


def test_fenced_code_block_with_language():
    md = "```python\nprint('hi')\n```"
    assert format_response(md) == "<pre><code class=\"language-python\">print('hi')</code></pre>"


def test_fenced_code_block_without_language():
    md = "```\nfoo\n```"
    assert format_response(md) == "<pre>foo</pre>"


def test_fenced_code_block_escapes_entities():
    md = "```\n<tag>\n```"
    assert format_response(md) == "<pre>&lt;tag&gt;</pre>"


def test_link_maps_to_a_tag():
    assert format_response("[click](https://ex.com)") == '<a href="https://ex.com">click</a>'


def test_link_escapes_href():
    result = format_response("[x](https://ex.com?a=1&b=2)")
    assert '<a href="https://ex.com?a=1&amp;b=2">x</a>' == result


def test_heading_becomes_bold():
    result = format_response("# Título")
    assert result == "<b>Título</b>"


def test_multiple_headings_and_paragraphs():
    md = "# H1\n\nparrafo uno\n\n## H2\n\nparrafo dos"
    result = format_response(md)
    assert "<b>H1</b>" in result
    assert "<b>H2</b>" in result
    assert "parrafo uno" in result
    assert "parrafo dos" in result


def test_unordered_list():
    md = "- uno\n- dos\n- tres"
    result = format_response(md)
    assert "• uno" in result
    assert "• dos" in result
    assert "• tres" in result


def test_ordered_list():
    md = "1. primero\n2. segundo"
    result = format_response(md)
    assert "1. primero" in result
    assert "2. segundo" in result


def test_nested_unordered_list():
    md = "- padre\n  - hijo"
    result = format_response(md)
    assert "• padre" in result
    assert "  • hijo" in result


def test_blockquote_maps_to_blockquote_tag():
    result = format_response("> una cita")
    assert result == "<blockquote>una cita</blockquote>"


def test_horizontal_rule_becomes_separator():
    result = format_response("arriba\n\n---\n\nabajo")
    assert "arriba" in result
    assert "──────────" in result
    assert "abajo" in result


def test_mixed_inline_formatting():
    md = "un **bold** y un *italic* y `code`"
    result = format_response(md)
    assert result == "un <b>bold</b> y un <i>italic</i> y <code>code</code>"


def test_no_unescaped_angle_brackets_from_llm_content():
    """El LLM puede devolver texto con < > que no son HTML — deben escaparse."""
    md = "if a < b and b > c: return"
    result = format_response(md)
    assert "<" not in result.replace("&lt;", "")
    assert ">" not in result.replace("&gt;", "")


def test_realistic_llm_response():
    md = (
        "# Resumen\n\n"
        "Encontré **3 bugs** en el módulo `auth`:\n\n"
        "1. Token expirado\n"
        "2. Race condition\n"
        "3. SQL injection\n\n"
        "Ver [docs](https://docs.ex.com) para más info."
    )
    result = format_response(md)
    assert "<b>Resumen</b>" in result
    assert "<b>3 bugs</b>" in result
    assert "<code>auth</code>" in result
    assert "1. Token expirado" in result
    assert '<a href="https://docs.ex.com">docs</a>' in result


# ---------------------------------------------------------------------------
# blockquote expandable — citas largas se vuelven colapsables
# ---------------------------------------------------------------------------


def test_blockquote_corto_no_expande():
    """Una cita breve queda como <blockquote> normal (el caso ya cubierto arriba,
    explícito acá para fijar el borde inferior del umbral)."""
    result = format_response("> dato breve")
    assert result == "<blockquote>dato breve</blockquote>"
    assert "expandable" not in result


def test_blockquote_largo_por_lineas_es_expandable():
    """Cita de 4+ líneas → <blockquote expandable> (cierre sigue siendo </blockquote>)."""
    md = "> linea uno\n> linea dos\n> linea tres\n> linea cuatro"
    result = format_response(md)
    assert result.startswith("<blockquote expandable>")
    assert result.endswith("</blockquote>")


def test_blockquote_multilinea_corto_no_expande():
    """Dos líneas no alcanzan el umbral de 4 → cita normal."""
    md = "> linea uno\n> linea dos"
    result = format_response(md)
    assert result.startswith("<blockquote>")
    assert "expandable" not in result


def test_blockquote_largo_por_chars_es_expandable():
    """Una sola línea pero muy larga (>= 280 chars) también colapsa."""
    cita = ("palabra " * 40).strip()  # ~319 chars
    result = format_response(f"> {cita}")
    assert "<blockquote expandable>" in result


# ---------------------------------------------------------------------------
# send_html_or_plain — fallback a texto plano ante HTML inválido
# ---------------------------------------------------------------------------


async def test_send_html_or_plain_happy_envia_html():
    """Sin error: un solo envío, con HTML formateado y ParseMode.HTML."""
    llamadas: list[tuple[str, ParseMode | None]] = []

    async def send(text: str, pm: ParseMode | None) -> None:
        llamadas.append((text, pm))

    await send_html_or_plain(send, "**hola**")

    assert llamadas == [("<b>hola</b>", ParseMode.HTML)]


async def test_send_html_or_plain_fallback_a_texto_plano():
    """Si Telegram rechaza el parseo, reintenta con el markdown CRUDO sin parse_mode."""
    llamadas: list[tuple[str, ParseMode | None]] = []

    async def send(text: str, pm: ParseMode | None) -> None:
        llamadas.append((text, pm))
        if len(llamadas) == 1:
            raise BadRequest("Can't parse entities: unsupported start tag")

    await send_html_or_plain(send, "roto < sin cerrar")

    assert len(llamadas) == 2
    assert llamadas[0][1] == ParseMode.HTML  # 1er intento: HTML
    assert llamadas[1] == ("roto < sin cerrar", None)  # 2do: crudo, sin parse_mode


async def test_send_html_or_plain_reraise_si_no_es_error_de_parseo():
    """Un BadRequest ajeno al parseo (chat inexistente, etc.) se re-lanza sin fallback."""
    llamadas: list[tuple[str, ParseMode | None]] = []

    async def send(text: str, pm: ParseMode | None) -> None:
        llamadas.append((text, pm))
        raise BadRequest("Chat not found")

    with pytest.raises(BadRequest, match="Chat not found"):
        await send_html_or_plain(send, "hola")

    assert len(llamadas) == 1  # NO hubo segundo intento


# ---------------------------------------------------------------------------
# _envio_no_llego + reintento seguro ante timeouts de red (no duplicar mensajes)
# ---------------------------------------------------------------------------


def _timed_out_from(causa: BaseException) -> TimedOut:
    """TimedOut con ``__cause__`` = causa, como lo arma ptb con ``raise ... from err``."""
    exc = TimedOut()
    exc.__cause__ = causa
    return exc


def test_envio_no_llego_true_para_connect_y_pool():
    """Conexión no establecida / request no salió del pool → el mensaje NO llegó."""
    assert _envio_no_llego(_timed_out_from(httpx.ConnectTimeout("x"))) is True
    assert _envio_no_llego(_timed_out_from(httpx.PoolTimeout("x"))) is True
    assert _envio_no_llego(_timed_out_from(httpx.ConnectError("x"))) is True


def test_envio_no_llego_false_para_read_y_write():
    """ReadTimeout/WriteTimeout: el request PUDO entregarse → reintentar duplicaría."""
    assert _envio_no_llego(_timed_out_from(httpx.ReadTimeout("x"))) is False
    assert _envio_no_llego(_timed_out_from(httpx.WriteTimeout("x"))) is False


def test_envio_no_llego_detecta_pool_por_mensaje_sin_cause():
    """Defensa: ptb marca el PoolTimeout con este texto aunque se pierda __cause__."""
    exc = TimedOut(
        message="Pool timeout: All connections in the connection pool are occupied. "
        "Request was *not* sent to Telegram. Consider adjusting the connection pool size."
    )
    assert _envio_no_llego(exc) is True


async def test_send_reintenta_ante_timeout_seguro_y_entrega(monkeypatch):
    """Dos ConnectTimeout y al 3er intento entrega: 3 llamadas, sin excepción."""
    monkeypatch.setattr("adapters.inbound.telegram.message_mapper._SEND_RETRY_BASE_DELAY", 0.0)
    intentos = 0

    async def send(text: str, pm: ParseMode | None) -> None:
        nonlocal intentos
        intentos += 1
        if intentos < 3:
            raise _timed_out_from(httpx.ConnectTimeout("boom"))

    await send_html_or_plain(send, "hola")

    assert intentos == 3


async def test_send_agota_reintentos_y_propaga(monkeypatch):
    """Timeout seguro persistente: reintenta 3 veces y luego re-lanza el TimedOut."""
    monkeypatch.setattr("adapters.inbound.telegram.message_mapper._SEND_RETRY_BASE_DELAY", 0.0)
    intentos = 0

    async def send(text: str, pm: ParseMode | None) -> None:
        nonlocal intentos
        intentos += 1
        raise _timed_out_from(httpx.ConnectTimeout("boom"))

    with pytest.raises(TimedOut):
        await send_html_or_plain(send, "hola")

    assert intentos == 3


async def test_send_no_reintenta_ante_read_timeout(monkeypatch):
    """ReadTimeout (posible entrega): un solo intento, sin reintento (no duplicar)."""
    monkeypatch.setattr("adapters.inbound.telegram.message_mapper._SEND_RETRY_BASE_DELAY", 0.0)
    intentos = 0

    async def send(text: str, pm: ParseMode | None) -> None:
        nonlocal intentos
        intentos += 1
        raise _timed_out_from(httpx.ReadTimeout("boom"))

    with pytest.raises(TimedOut):
        await send_html_or_plain(send, "hola")

    assert intentos == 1


# ---------------------------------------------------------------------------
# split_message — troceo de respuestas largas (Defecto 3: TimedOut/too-long)
# ---------------------------------------------------------------------------


def test_split_message_texto_corto_intacto():
    """Texto que entra en un mensaje → se devuelve igual, sin trocear."""
    assert split_message("hola mundo", limit=100) == ["hola mundo"]


def test_split_message_corta_por_lineas():
    """Trocea respetando límites de línea, cada fragmento ≤ limit."""
    texto = "\n".join(f"linea {i}" for i in range(20))  # ~140 chars
    fragmentos = split_message(texto, limit=40)

    assert len(fragmentos) > 1
    assert all(len(f) <= 40 for f in fragmentos)
    # No se pierde ni se duplica contenido: reconstruir devuelve el original.
    assert "\n".join(fragmentos) == texto


def test_split_message_linea_mas_larga_que_el_limite_corta_duro():
    """Una sola línea que excede el límite se parte en pedazos ≤ limit."""
    texto = "x" * 250
    fragmentos = split_message(texto, limit=100)

    assert len(fragmentos) == 3
    assert [len(f) for f in fragmentos] == [100, 100, 50]
    assert "".join(fragmentos) == texto


def test_split_message_usa_limite_default_grande():
    """Con el límite real (~3500) un texto chico nunca se trocea."""
    assert split_message("respuesta normal") == ["respuesta normal"]


async def test_send_html_or_plain_trocea_respuesta_larga():
    """Una respuesta larga se envía como varios mensajes independientes."""
    llamadas: list[tuple[str, ParseMode | None]] = []

    async def send(text: str, pm: ParseMode | None) -> None:
        llamadas.append((text, pm))

    larga = "\n".join(f"parrafo {i} " + "palabra " * 50 for i in range(20))
    assert len(larga) > 3500  # supera un mensaje

    await send_html_or_plain(send, larga)

    assert len(llamadas) > 1  # se troceó
    assert all(pm == ParseMode.HTML for _, pm in llamadas)


# ---------------------------------------------------------------------------
# format_caption — límite de 1024 y degradación a crudo
# ---------------------------------------------------------------------------


def test_format_caption_renderiza_markdown_con_parse_mode():
    assert format_caption("mirá el **gráfico**") == ("mirá el <b>gráfico</b>", ParseMode.HTML)


def test_format_caption_none_pasa_derecho_sin_parse_mode():
    """Un media sin caption no debe inventar un caption vacío ni un parse_mode."""
    assert format_caption(None) == (None, None)


def test_format_caption_vacio_no_activa_parse_mode():
    assert format_caption("   ") == ("   ", None)


def test_format_caption_degrada_a_crudo_si_el_html_no_entra_en_1024():
    """LA guarda anti-regresión: el render EXPANDE, y un caption no se puede trocear.

    300 ``<`` crudos entran holgados en el límite de Telegram; escapados a
    ``&lt;`` ocupan 1200 y lo pasan. Formatear a ciegas rompería un envío que
    hoy funciona — peor que el bug que vinimos a arreglar.
    """
    crudo = "<" * 300
    assert len(crudo) <= 1024
    assert len(format_response(crudo)) > 1024, "Fixture inválida: el HTML debe pasarse"

    texto, modo = format_caption(crudo)

    assert texto == crudo, "Debe devolver el caption CRUDO, no el HTML que no entra"
    assert modo is None, "Sin render no puede haber parse_mode"


def test_format_caption_acepta_el_borde_exacto_de_1024():
    """Un HTML de exactamente 1024 chars entra: el límite es inclusivo."""
    crudo = "a" * 1014 + " **bc**"  # -> "aaa... <b>bc</b>" = 1014 + 1 + 9 = 1024
    texto, modo = format_caption(crudo)

    assert len(texto) == 1024
    assert modo == ParseMode.HTML


# ---------------------------------------------------------------------------
# send_caption_or_plain — fallback + rebobinado del handle
# ---------------------------------------------------------------------------


class _FakeHandle:
    """Handle de fichero mínimo que registra sus rebobinados."""

    def __init__(self) -> None:
        self.seeks: list[int] = []

    def seek(self, pos: int) -> None:
        self.seeks.append(pos)


async def test_send_caption_or_plain_happy_path():
    llamadas: list[tuple[str | None, ParseMode | None]] = []

    async def send(texto: str | None, pm: ParseMode | None) -> None:
        llamadas.append((texto, pm))

    await send_caption_or_plain(send, "la **foto**")

    assert llamadas == [("la <b>foto</b>", ParseMode.HTML)]


async def test_send_caption_or_plain_fallback_rebobina_el_handle():
    """Sin ``seek(0)`` el reintento subiría un fichero VACÍO: ptb ya lo consumió."""
    handle = _FakeHandle()
    llamadas: list[tuple[str | None, ParseMode | None]] = []

    async def send(texto: str | None, pm: ParseMode | None) -> None:
        llamadas.append((texto, pm))
        if len(llamadas) == 1:
            raise BadRequest("Can't parse entities: unsupported start tag")

    await send_caption_or_plain(send, "roto **abierto", media=handle)

    assert llamadas[1] == ("roto **abierto", None), "El reintento manda el caption crudo"
    assert handle.seeks == [0], "El handle debe rebobinarse antes del reintento"


async def test_send_caption_or_plain_reraise_si_no_es_error_de_parseo():
    """Un BadRequest ajeno (chat inexistente) sube tal cual, sin reintento."""
    handle = _FakeHandle()
    llamadas: list[tuple[str | None, ParseMode | None]] = []

    async def send(texto: str | None, pm: ParseMode | None) -> None:
        llamadas.append((texto, pm))
        raise BadRequest("Chat not found")

    with pytest.raises(BadRequest, match="Chat not found"):
        await send_caption_or_plain(send, "la **foto**", media=handle)

    assert len(llamadas) == 1
    assert handle.seeks == [], "No hubo reintento: nada que rebobinar"


async def test_send_caption_or_plain_sin_parse_mode_no_reintenta():
    """Si el caption ya iba crudo, un error de parseo no es culpa nuestra: sube.

    Reintentar lo MISMO sería un bucle inútil y un segundo envío del media.
    """
    llamadas: list[tuple[str | None, ParseMode | None]] = []

    async def send(texto: str | None, pm: ParseMode | None) -> None:
        llamadas.append((texto, pm))
        raise BadRequest("Can't parse entities")

    with pytest.raises(BadRequest):
        await send_caption_or_plain(send, None)

    assert len(llamadas) == 1


async def test_send_caption_or_plain_tolera_media_no_seekable():
    """Un ``file_id`` string o una URL no tienen ``seek``: el reintento va igual."""
    llamadas: list[tuple[str | None, ParseMode | None]] = []

    async def send(texto: str | None, pm: ParseMode | None) -> None:
        llamadas.append((texto, pm))
        if len(llamadas) == 1:
            raise BadRequest("Can't parse entities")

    await send_caption_or_plain(send, "la **foto**", media="AgACAgEAAxkBAAI")

    assert llamadas[1] == ("la **foto**", None)
