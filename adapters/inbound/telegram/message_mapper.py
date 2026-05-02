"""Mapper entre mensajes de Telegram y entidades del dominio."""

from __future__ import annotations

from html import escape as _html_escape

from markdown_it import MarkdownIt
from markdown_it.token import Token
from telegram import Update

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
    """Detecta voice/audio/video_note en un Message y devuelve (bytes, mime, size).

    Retorna ``None`` si el mensaje no contiene ninguno de los tres tipos.
    Prioridad: voice > audio > video_note (los tres nunca coinciden en Telegram,
    pero la prioridad es explícita por defensa).

    Defaults de mime cuando el payload no lo informa:
    - voice → ``audio/ogg`` (Telegram usa OGG/Opus).
    - audio → ``audio/mpeg``.
    - video_note → ``video/mp4`` (Telegram garantiza MP4).
    """
    if message.voice:
        payload = message.voice
        mime = getattr(payload, "mime_type", None) or "audio/ogg"
    elif message.audio:
        payload = message.audio
        mime = getattr(payload, "mime_type", None) or "audio/mpeg"
    elif message.video_note:
        payload = message.video_note
        mime = "video/mp4"
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


def format_group_message(message) -> str:
    """Formatea un mensaje de grupo con prefijo del remitente.

    El formato resultante es ``"username said: texto"``. El sender DEBE
    embeberse en el ``content`` porque el role ``user`` del protocolo OpenAI no
    carga identidad — sin él, el LLM no sabe quién habló en un grupo.

    La marca de tiempo se inyecta aparte en ``RunAgentUseCase`` cuando el flag
    ``channels.telegram.add_llm_timestamp`` está activo, leyendo el
    ``Message.timestamp`` persistido en la DB. Mantener acá un timestamp
    embebido duplicaría el dato y obligaría a parsearlo en cualquier
    re-procesamiento del historial.

    Args:
        message: Objeto ``telegram.Message`` con el campo ``from_user`` poblado.

    Returns:
        String con formato ``"<remitente> said: <texto>"``.
    """
    location = getattr(message, "location", None)
    if location and not getattr(location, "live_period", None):
        texto = f"{{GPS:{location.latitude},{location.longitude}}}"
    else:
        texto = (message.text or "").strip()

    remitente = extract_sender_name(message)
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
            href = child.attrGet("href") or ""
            out.append(f'<a href="{_escape(href)}">')
        elif t == "link_close":
            out.append("</a>")
        elif t == "image":
            alt = child.content or ""
            src = child.attrGet("src") or ""
            if src:
                out.append(f'<a href="{_escape(src)}">{_escape(alt or src)}</a>')
            elif alt:
                out.append(_escape(alt))
        elif child.content:
            out.append(_escape(child.content))
    return "".join(out)


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
            out.append(f"<blockquote>{inner_html}</blockquote>\n\n")
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
