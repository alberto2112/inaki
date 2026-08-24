"""Mapper entre mensajes de Telegram y entidades del dominio."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from html import escape as _html_escape
from typing import Any

import httpx
from markdown_it import MarkdownIt
from markdown_it.token import Token
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, TimedOut

logger = logging.getLogger(__name__)

_md = MarkdownIt("commonmark", {"html": False}).enable("strikethrough")


def telegram_update_to_input(update: Update) -> str | None:
    """Extrae el texto del mensaje de un Update de Telegram.

    Soporta:
    - Mensajes de texto: devuelve el texto strippeado.
    - Ubicaciones únicas (``message.location`` sin ``live_period``): devuelve
      ``{GPS:lat,lon}`` como representación textual procesable por el LLM.

    Las ubicaciones en tiempo real (``live_period`` seteado) se ignoran porque
    el flujo actual no consume ``edited_message`` updates — sólo confundirían
    al LLM al recibir una posición sin saber que se actualizará.
    """
    if update.message and update.message.text:
        return update.message.text.strip()
    if update.message and update.message.location:
        location = update.message.location
        if getattr(location, "live_period", None):
            return None
        return f"{{GPS:{location.latitude},{location.longitude}}}"
    return None


async def extract_audio_payload(message) -> tuple[bytes, str, int] | None:
    """Detecta voice/audio/video_note (o document con mime audio) y devuelve (bytes, mime, size).

    Retorna ``None`` si el mensaje no contiene ninguno de los tipos.
    Prioridad: voice > audio > video_note > document-audio (los cuatro nunca
    coinciden en Telegram, pero la prioridad es explícita por defensa).

    Defaults de mime cuando el payload no lo informa:
    - voice → ``audio/ogg`` (Telegram usa OGG/Opus).
    - audio → ``audio/mpeg``.
    - video_note → ``video/mp4`` (Telegram garantiza MP4).

    El branch de document cubre el caso "mp3 adjuntado como archivo": Telegram
    clasifica el media según CÓMO lo mandó el cliente, no por su contenido —
    un audio enviado con el selector de archivos llega como ``document`` con
    ``mime_type=audio/*`` y debe transcribirse igual que un ``audio`` nativo.
    """
    document = getattr(message, "document", None)
    if message.voice:
        payload = message.voice
        mime = getattr(payload, "mime_type", None) or "audio/ogg"
    elif message.audio:
        payload = message.audio
        mime = getattr(payload, "mime_type", None) or "audio/mpeg"
    elif message.video_note:
        payload = message.video_note
        mime = "video/mp4"
    elif document is not None and str(getattr(document, "mime_type", "") or "").startswith(
        "audio/"
    ):
        payload = document
        mime = document.mime_type
    else:
        return None

    file = await payload.get_file()
    data = await file.download_as_bytearray()
    size = int(getattr(payload, "file_size", None) or 0)
    return bytes(data), mime, size


async def extract_photo_payload(message) -> tuple[bytes, str, int] | None:
    """Detecta una foto individual en un Message y devuelve (bytes, mime, size).

    Telegram envía las fotos como una lista de PhotoSize con distintas resoluciones.
    Se elige la de mayor resolución (última de la lista).

    Retorna ``None`` si no hay foto. Telegram siempre envía JPEG.
    """
    if not message.photo:
        return None
    photo = message.photo[-1]
    file = await photo.get_file()
    data = await file.download_as_bytearray()
    size = int(getattr(photo, "file_size", None) or 0)
    return bytes(data), "image/jpeg", size


def extract_sender_name(message) -> str:
    """Extrae el nombre del remitente humano de un mensaje de Telegram.

    Patrón de fallback: ``username > first_name > "anonimo"``. Usa duck-typing
    via ``getattr`` para tolerar stubs en tests (no requiere importar
    ``telegram.User``).

    Args:
        message: Objeto Message de Telegram (real o stub) con ``from_user``.

    Returns:
        El nombre del remitente, o ``"anonimo"`` si no se puede determinar.
    """
    from_user = getattr(message, "from_user", None)
    if from_user is None:
        return "anonimo"

    username = getattr(from_user, "username", None)
    if username:
        return username

    first_name = getattr(from_user, "first_name", None)
    if first_name:
        return first_name

    return "anonimo"


def compose_sender_identity(message) -> str | None:
    """Compone una identidad legible del remitente para inyectar en el system prompt.

    Formato según los campos disponibles en ``from_user``:

    - ``first_name`` + ``last_name`` + ``@username``  → ``"Juan Pérez (@juan_dev)"``
    - ``first_name`` + ``last_name`` sin username     → ``"Juan Pérez"``
    - ``first_name`` + ``@username`` sin last_name    → ``"Juan (@juan_dev)"``
    - solo ``first_name``                             → ``"Juan"``
    - sin ``first_name``, con ``@username``           → ``"@juan_dev"`` (edge case
      defensivo: la API de Telegram garantiza ``first_name`` para mensajes humanos,
      pero stubs en tests pueden no setearlo)
    - sin nada utilizable o ``from_user`` ausente     → ``None``

    Diferencia con ``extract_sender_name``: aquella prioriza ``username > first_name``
    para preservar unicidad en el prefijo de grupos. Esta función prioriza el nombre
    real (``first_name``) y trata ``@username`` como anotación entre paréntesis para
    que el LLM tenga ambos: forma humana de dirigirse al usuario + handle único
    desambiguador. Por eso ambas conviven sin reemplazarse.

    Args:
        message: Objeto Message de Telegram (real o stub) con ``from_user``.

    Returns:
        La identidad compuesta o ``None`` si no hay información utilizable.
    """
    from_user = getattr(message, "from_user", None)
    if from_user is None:
        return None

    first_name = (getattr(from_user, "first_name", None) or "").strip()
    last_name = (getattr(from_user, "last_name", None) or "").strip()
    username = (getattr(from_user, "username", None) or "").strip()

    if first_name:
        nombre = f"{first_name} {last_name}".strip() if last_name else first_name
        if username:
            return f"{nombre} (@{username})"
        return nombre

    # Defensa: first_name es requerido por la API real de Telegram, pero los stubs
    # de test pueden omitirlo. Si igual hay username, lo devolvemos como @handle.
    if username:
        return f"@{username}"

    return None


def format_group_message(message) -> str:
    """Formatea un mensaje de grupo con prefijo del remitente.

    El sender DEBE embeberse en el ``content`` porque el role ``user`` del
    protocolo OpenAI no carga identidad — sin él, el LLM no sabe quién habló.

    Formatos posibles:
    - Sin reply: ``"<remitente> said: <texto>"``
    - Con reply: ``"<remitente> reply to <original>(<original_texto[:64]>): <texto>"``

    La marca de tiempo se inyecta aparte en ``RunAgentUseCase`` cuando el flag
    ``channels.telegram.add_llm_timestamp`` está activo.

    Args:
        message: Objeto ``telegram.Message`` con ``from_user`` y opcionalmente
            ``reply_to_message`` poblados.
    """
    location = getattr(message, "location", None)
    if location and not getattr(location, "live_period", None):
        texto = f"{{GPS:{location.latitude},{location.longitude}}}"
    else:
        texto = (message.text or "").strip()

    remitente = extract_sender_name(message)

    reply = getattr(message, "reply_to_message", None)
    if reply is not None:
        nombre_original = extract_sender_name(reply)
        texto_original = (getattr(reply, "text", None) or "").strip()[:64]
        return f"{remitente} reply to {nombre_original}({texto_original}): {texto}"

    return f"{remitente} said: {texto}"


def hay_menciones(message) -> bool:
    """Devuelve ``True`` si el mensaje contiene al menos una entidad de tipo mención.

    Detecta ``mention`` (``@usuario``) y ``text_mention`` (usuario sin username público).
    No distingue a quién apunta la mención — solo informa si hay alguna.

    Args:
        message: Objeto ``telegram.Message`` con el campo ``entities`` poblado.
    """
    entities = getattr(message, "entities", None) or []
    return any(getattr(e, "type", None) in ("mention", "text_mention") for e in entities)


def detect_mention(message, bot_username: str) -> bool:
    """Detecta si un mensaje menciona al bot por su username.

    Itera ``message.entities`` buscando:
    - ``type == "mention"``: extrae el substring del texto, compara sin ``@`` con ``bot_username``.
    - ``type == "text_mention"``: compara ``entity.user.username`` con ``bot_username``.

    Args:
        message: Objeto ``telegram.Message`` con ``entities`` y ``text`` poblados.
        bot_username: Username del bot SIN arroba (ej: ``"inakilabs_bot"``).

    Returns:
        ``True`` si alguna entidad menciona al bot; ``False`` en caso contrario.
    """
    entities = getattr(message, "entities", None) or []
    texto = message.text or ""

    for entity in entities:
        tipo = getattr(entity, "type", None)

        if tipo == "mention":
            # La entidad incluye el '@' — extraemos el substring y comparamos sin '@'.
            fragmento = texto[entity.offset : entity.offset + entity.length]
            if fragmento.lstrip("@") == bot_username:
                return True

        elif tipo == "text_mention":
            # Usuario sin username público — comparamos por username del objeto User.
            usuario = getattr(entity, "user", None)
            if usuario is not None:
                username_entidad = getattr(usuario, "username", None)
                if username_entidad == bot_username:
                    return True

    return False


def es_reply_a(message, bot_username: str) -> bool:
    """Devuelve ``True`` si el mensaje es un reply a un mensaje del bot indicado."""
    reply = getattr(message, "reply_to_message", None)
    if reply is None:
        return False
    reply_from = getattr(reply, "from_user", None)
    if reply_from is None:
        return False
    return getattr(reply_from, "username", None) == bot_username


def es_reply_a_bot(message) -> bool:
    """Devuelve ``True`` si el mensaje es un reply a CUALQUIER usuario marcado como bot."""
    reply = getattr(message, "reply_to_message", None)
    if reply is None:
        return False
    reply_from = getattr(reply, "from_user", None)
    if reply_from is None:
        return False
    return bool(getattr(reply_from, "is_bot", False))


def dirigido_a(message, bot_username: str) -> bool:
    """Devuelve ``True`` si el mensaje está dirigido al bot indicado.

    Un mensaje está dirigido a un bot si lo menciona explícitamente (``@username``)
    o si es un reply a un mensaje suyo. Reply ≡ mención implícita.
    """
    return detect_mention(message, bot_username) or es_reply_a(message, bot_username)


def hay_destinatario_explicito(message) -> bool:
    """Devuelve ``True`` si el mensaje apunta a un destinatario concreto.

    Cuenta como destinatario explícito:
    - Una mención (``@usuario`` o text_mention).
    - Un reply a un usuario marcado como bot.
    """
    return hay_menciones(message) or es_reply_a_bot(message)


def format_response(response: str) -> str:
    """
    Convierte la respuesta markdown del LLM al subset HTML de Telegram.

    Telegram HTML soporta: b, i, u, s, code, pre, a, blockquote, tg-spoiler.
    El resto (headers, listas, hr) se degrada a texto plano con marcadores.
    Usar con parse_mode="HTML" en reply_text.
    """
    if not response:
        return ""
    tokens = _md.parse(response)
    return _render(tokens).strip()


# Fragmentos del mensaje con que Telegram (HTTP 400) rechaza un HTML mal formado.
# Si el envío falla por esto reintentamos en texto plano: vale más que el usuario
# lea el contenido crudo a que reciba un "Error:" opaco y pierda la respuesta.
_PARSE_ERROR_HINTS: tuple[str, ...] = (
    "can't parse",
    "cant parse",
    "can't find end",
    "unsupported start tag",
    "unclosed",
    "entities",
)


def es_error_de_parseo(exc: BadRequest) -> bool:
    """``True`` si el ``BadRequest`` viene de un HTML que Telegram no pudo parsear."""
    mensaje = str(exc).lower()
    return any(hint in mensaje for hint in _PARSE_ERROR_HINTS)


# Límite duro de Telegram para el texto de un mensaje (4096 chars). El formateo a
# HTML EXPANDE el texto (escapes ``&lt;`` y tags ``<b></b>``), así que partimos el
# markdown CRUDO con un límite conservador para que el HTML resultante no se pase
# de 4096 y Telegram no rechace con BadRequest "message is too long".
_TELEGRAM_MAX_CHARS = 4096
_CHUNK_CHARS = 3500


def split_message(text: str, limit: int = _CHUNK_CHARS) -> list[str]:
    """Parte ``text`` en fragmentos de longitud ≤ ``limit`` para Telegram.

    Prefiere cortar en límites de línea (``\\n``) para no romper palabras; si una
    línea sola excede el límite, la corta duro. Devuelve ``[text]`` intacto cuando
    ya entra en un solo mensaje (caso común → cero cambios de comportamiento).
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    actual = ""
    for linea in text.split("\n"):
        # Una línea sola más larga que el límite → cortarla en pedazos duros.
        while len(linea) > limit:
            if actual:
                chunks.append(actual)
                actual = ""
            chunks.append(linea[:limit])
            linea = linea[limit:]
        candidato = f"{actual}\n{linea}" if actual else linea
        if len(candidato) <= limit:
            actual = candidato
        else:
            if actual:
                chunks.append(actual)
            actual = linea
    if actual:
        chunks.append(actual)
    return chunks


async def send_html_or_plain(
    send: Callable[[str, ParseMode | None], Awaitable[Any]],
    response: str,
) -> None:
    """Envía ``response`` como HTML de Telegram, troceado y con fallback a texto plano.

    Telegram rechaza con HTTP 400 ("can't parse entities") cualquier mensaje cuyo
    HTML quede mal formado — un ``<`` que se coló sin escapar, un tag sin cerrar
    por un edge case del renderer, etc. Sin contención, el handler de arriba cae a
    su ``except`` y el usuario recibe "Error: ..." en lugar de la respuesta.

    También trocea respuestas largas (``split_message``): Telegram corta en 4096
    chars, así que una respuesta de 100+ líneas se enviaba como un solo request
    grande (más lento → más expuesto a ``TimedOut``) o directamente rebotaba con
    "message is too long". Cada fragmento viaja como un mensaje independiente.

    Estrategia por fragmento: intentar con ``format_response`` + ``ParseMode.HTML``;
    si Telegram rechaza el parseo, reintentar con el markdown CRUDO sin
    ``parse_mode`` (legible aunque pierda el formato). Cualquier otro ``BadRequest``
    (chat inexistente, etc.) se re-lanza tal cual para que el handler lo trate.

    Args:
        send: callable async ``(texto, parse_mode) -> Awaitable``. Abstrae el
            transporte concreto — sirve igual para ``message.reply_text`` que para
            ``bot.send_message`` (cada call-site pasa su propio closure).
        response: la respuesta markdown del LLM, sin formatear.
    """
    for fragmento in split_message(response):
        await _send_fragmento(send, fragmento)


# Reintentos de red al ENTREGAR un mensaje. Solo se reintenta cuando tenemos
# CERTEZA de que el request no llegó a Telegram (ver ``_envio_no_llego``): un
# ReadTimeout/WriteTimeout pudo haberse entregado y reintentar duplicaría el
# mensaje en el chat — Telegram no tiene idempotency keys para ``sendMessage``.
_SEND_RETRY_ATTEMPTS = 3
_SEND_RETRY_BASE_DELAY = 0.5


def _envio_no_llego(exc: NetworkError) -> bool:
    """``True`` si el fallo garantiza que el mensaje NUNCA llegó a Telegram.

    Casos seguros de reintentar: la conexión no se estableció (``ConnectTimeout`` /
    ``ConnectError``) o el request ni salió del pool (``PoolTimeout`` —
    python-telegram-bot lo marca explícitamente como "not sent to Telegram"). Un
    ``ReadTimeout`` / ``WriteTimeout`` significa que el request PUDO haberse
    entregado y su respuesta HTTP tardó → NO se reintenta, para no duplicar el
    mensaje. La discriminación se hace por ``__cause__`` (la excepción ``httpx``
    original que ptb preserva con ``raise TimedOut from err``).
    """
    causa = exc.__cause__
    if isinstance(causa, (httpx.PoolTimeout, httpx.ConnectTimeout, httpx.ConnectError)):
        return True
    # Defensa por si ``__cause__`` se perdiera: ptb marca el PoolTimeout con el
    # texto "Request was *not* sent to Telegram" (los asteriscos rompen "not sent",
    # por eso matcheamos el fragmento contiguo "sent to Telegram", exclusivo de ese caso).
    return "sent to Telegram" in str(exc)


async def _send_con_reintento(make_send: Callable[[], Awaitable[Any]]) -> Any:
    """Ejecuta ``make_send()`` reintentando SOLO fallos de red donde el mensaje no llegó.

    ``BadRequest`` (HTML mal formado, chat inexistente) se re-lanza sin reintentar:
    reintentar un request malformado es inútil y su fallback a texto plano lo
    maneja ``_send_fragmento``. Backoff lineal (0.5s, 1s) entre intentos.
    """
    for intento in range(1, _SEND_RETRY_ATTEMPTS + 1):
        try:
            return await make_send()
        except BadRequest:
            raise
        except (TimedOut, NetworkError) as exc:
            if intento == _SEND_RETRY_ATTEMPTS or not _envio_no_llego(exc):
                raise
            delay = _SEND_RETRY_BASE_DELAY * intento
            logger.warning(
                "Telegram: envío falló (intento %d/%d, el mensaje no llegó), "
                "reintento en %.1fs: %s",
                intento,
                _SEND_RETRY_ATTEMPTS,
                delay,
                exc,
            )
            await asyncio.sleep(delay)


async def _send_fragmento(
    send: Callable[[str, ParseMode | None], Awaitable[Any]],
    fragmento: str,
) -> None:
    """Envía UN fragmento: HTML primero, fallback a texto plano ante error de parseo."""
    try:
        await _send_con_reintento(lambda: send(format_response(fragmento), ParseMode.HTML))
    except BadRequest as exc:
        if not es_error_de_parseo(exc):
            raise
        logger.warning("Telegram rechazó el HTML; reintento en texto plano: %s", exc)
        await _send_con_reintento(lambda: send(fragmento, None))


# Límite duro de Telegram para el caption de un media (1024 chars) — MUY por
# debajo de los 4096 de un mensaje de texto.
_TELEGRAM_MAX_CAPTION_CHARS = 1024


def format_caption(caption: str | None) -> tuple[str | None, ParseMode | None]:
    """Renderiza un caption a HTML de Telegram, degradando a crudo si no entra.

    Devuelve ``(texto, parse_mode)`` listo para pasar a ``send_photo`` y familia.

    Un caption NO es un mensaje de texto y NO se puede tratar igual:

    - **No se puede trocear**: viaja pegado al media, en el mismo request. No hay
      "segundo caption" al que mandar el sobrante.
    - **El límite es 1024**, no 4096.

    Como el render a HTML EXPANDE el texto (escapes ``&amp;``/``&lt;`` y tags
    ``<b></b>``), un caption que hoy entra crudo puede NO entrar formateado. Sin
    esta guarda, formatear rompería envíos que hoy funcionan: la regresión sería
    peor que el bug. Ante la duda mandamos el crudo — perder el formato es mejor
    que perder el envío.

    Args:
        caption: el caption markdown, o ``None`` si el media va sin texto.

    Returns:
        ``(caption_html, ParseMode.HTML)`` si el render entra en el límite;
        ``(caption_original, None)`` en cualquier otro caso.
    """
    if caption is None or not caption.strip():
        return caption, None
    renderizado = format_response(caption)
    if not renderizado or len(renderizado) > _TELEGRAM_MAX_CAPTION_CHARS:
        return caption, None
    return renderizado, ParseMode.HTML


def rebobinar(media: Any) -> None:
    """Devuelve un handle de fichero al byte 0, si es que lo es y se puede.

    Reintentar un envío de media SIN rebobinar sube un fichero VACÍO:
    python-telegram-bot ya consumió el stream en el intento fallido. Tolera
    cualquier cosa que no sea seekable (``bytes``, ``file_id`` string, URL) —
    en esos casos no hay nada que rebobinar y el reintento es seguro tal cual.
    """
    seek = getattr(media, "seek", None)
    if seek is None:
        return
    try:
        seek(0)
    except Exception:  # pragma: no cover — stream no seekable (pipe, socket)
        logger.warning("No se pudo rebobinar el media para el reintento sin formato")


async def send_caption_or_plain(
    send: Callable[[str | None, ParseMode | None], Awaitable[Any]],
    caption: str | None,
    media: Any = None,
) -> None:
    """Envía un media con su caption renderizado, con fallback al caption crudo.

    Gemelo de ``send_html_or_plain`` para la familia ``send_photo`` /
    ``send_audio`` / ``send_video`` / ``send_document``: mismo contrato de
    "el usuario recibe el contenido aunque el formato falle", sin el troceo
    (un caption no se puede partir) y con el rebobinado del handle, que es lo
    que hace SEGURO el reintento.

    Args:
        send: callable async ``(caption, parse_mode) -> Awaitable``. El media ya
            viaja capturado en el closure de cada call-site.
        caption: el caption markdown crudo, o ``None``.
        media: el payload del media, para rebobinarlo antes de reintentar. Se
            ignora si no es un handle seekable.
    """
    texto, modo = format_caption(caption)
    try:
        await send(texto, modo)
    except BadRequest as exc:
        # Sin parse_mode no hay HTML que culpar: el fallo es otro y sube tal cual.
        if modo is None or not es_error_de_parseo(exc):
            raise
        logger.warning("Telegram rechazó el caption HTML; reintento en texto plano: %s", exc)
        rebobinar(media)
        await send(caption, None)


def _escape(text: str) -> str:
    return _html_escape(text, quote=False)


def _render_inline(token: Token) -> str:
    out: list[str] = []
    for child in token.children or []:
        t = child.type
        if t == "text":
            out.append(_escape(child.content))
        elif t in ("softbreak", "hardbreak"):
            out.append("\n")
        elif t == "strong_open":
            out.append("<b>")
        elif t == "strong_close":
            out.append("</b>")
        elif t == "em_open":
            out.append("<i>")
        elif t == "em_close":
            out.append("</i>")
        elif t == "s_open":
            out.append("<s>")
        elif t == "s_close":
            out.append("</s>")
        elif t == "code_inline":
            out.append(f"<code>{_escape(child.content)}</code>")
        elif t == "link_open":
            # attrGet retorna str | int | float | None — normalizamos a str
            # porque _escape requiere str y los hrefs son siempre cadenas.
            href = str(child.attrGet("href") or "")
            out.append(f'<a href="{_escape(href)}">')
        elif t == "link_close":
            out.append("</a>")
        elif t == "image":
            alt = child.content or ""
            src = str(child.attrGet("src") or "")
            if src:
                out.append(f'<a href="{_escape(src)}">{_escape(alt or src)}</a>')
            elif alt:
                out.append(_escape(alt))
        elif child.content:
            out.append(_escape(child.content))
    return "".join(out)


# Una cita pasa a colapsable (<blockquote expandable>) cuando es lo bastante larga
# como para comerse la pantalla: Telegram la pliega con un "mostrar más". Umbrales
# por criterio — sin knob de config (uso doméstico, ver CLAUDE.md).
_BLOCKQUOTE_EXPAND_MIN_LINEAS = 4
_BLOCKQUOTE_EXPAND_MIN_CHARS = 280


def _cita_es_larga(inner_html: str) -> bool:
    """``True`` si la cita amerita render colapsable por su largo."""
    lineas = inner_html.count("\n") + 1
    return (
        lineas >= _BLOCKQUOTE_EXPAND_MIN_LINEAS or len(inner_html) >= _BLOCKQUOTE_EXPAND_MIN_CHARS
    )


def _render(tokens: list[Token]) -> str:
    out: list[str] = []
    list_stack: list[dict] = []

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        t = tok.type

        if t == "heading_open":
            inline = tokens[i + 1]
            content = _render_inline(inline)
            out.append(f"<b>{content}</b>\n\n")
            i += 3
            continue

        if t == "paragraph_open":
            inline = tokens[i + 1]
            content = _render_inline(inline)
            if list_stack:
                out.append(content)
            else:
                out.append(content + "\n\n")
            i += 3
            continue

        if t == "bullet_list_open":
            list_stack.append({"type": "ul", "index": 0})
            i += 1
            continue
        if t == "ordered_list_open":
            start = tok.attrGet("start")
            list_stack.append({"type": "ol", "index": int(start) if start else 1})
            i += 1
            continue
        if t in ("bullet_list_close", "ordered_list_close"):
            list_stack.pop()
            if not list_stack:
                out.append("\n")
            i += 1
            continue

        if t == "list_item_open":
            depth = max(len(list_stack) - 1, 0)
            indent = "  " * depth
            current = list_stack[-1]
            if current["type"] == "ul":
                marker = "• "
            else:
                marker = f"{current['index']}. "
                current["index"] += 1
            out.append(f"{indent}{marker}")
            i += 1
            continue
        if t == "list_item_close":
            out.append("\n")
            i += 1
            continue

        if t in ("fence", "code_block"):
            info = (tok.info or "").strip()
            lang = info.split()[0] if info else ""
            content = _escape(tok.content.rstrip("\n"))
            if lang:
                out.append(
                    f'<pre><code class="language-{_escape(lang)}">{content}</code></pre>\n\n'
                )
            else:
                out.append(f"<pre>{content}</pre>\n\n")
            i += 1
            continue

        if t == "blockquote_open":
            depth = 1
            j = i + 1
            inner: list[Token] = []
            while j < len(tokens):
                if tokens[j].type == "blockquote_open":
                    depth += 1
                elif tokens[j].type == "blockquote_close":
                    depth -= 1
                    if depth == 0:
                        break
                inner.append(tokens[j])
                j += 1
            inner_html = _render(inner).strip()
            tag = "blockquote expandable" if _cita_es_larga(inner_html) else "blockquote"
            out.append(f"<{tag}>{inner_html}</blockquote>\n\n")
            i = j + 1
            continue

        if t == "hr":
            out.append("──────────\n\n")
            i += 1
            continue

        if t == "inline":
            out.append(_render_inline(tok))
            i += 1
            continue

        i += 1

    return "".join(out)


# Tipos de chat que Telegram considera "grupos" (no privados).
_TIPOS_GRUPO = {"group", "supergroup", "channel"}


def _safe_optional_str(val: object) -> str | None:
    """Devuelve ``val`` solo si es ``str`` no vacío; en cualquier otro caso ``None``.

    Filtra ``MagicMock`` (tests que stub ``update = MagicMock()`` no setean estos
    campos), ``None`` y strings vacíos/blank. ``ChannelContext`` rechaza strings
    vacíos vía validator — convertir a ``None`` acá evita el ValueError aguas abajo.
    """
    if isinstance(val, str) and val.strip():
        return val
    return None
