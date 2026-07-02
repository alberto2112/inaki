"""Pipeline de media entrante del TelegramBot: fotos, álbumes, voz, video y documentos.

Mixin de ``TelegramBot``. Incluye la persistencia de file_id en
``telegram_files.db`` y la pre-descarga al workspace.

Principio rector — **persistencia simétrica**: TODO media que llega deja un
bloque de attachments en ``history.db`` (gramática de
``core/domain/value_objects/attachment.py``), traiga o no caption, dispare o
no un turno. Sin esto el agente convive con un contexto lleno de agujeros: el
usuario "le mandó algo" que el LLM no puede ver ni referenciar.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telegram import Update
from telegram.ext import ContextTypes

from adapters.inbound.telegram.message_mapper import (
    _TIPOS_GRUPO,
    extract_audio_payload,
    extract_photo_payload,
    extract_sender_name,
    send_html_or_plain,
)
from adapters.inbound.turn_dispatch import INFLIGHT_ACK
from core.domain.errors import TranscriptionError
from core.domain.value_objects.attachment import (
    IncomingAttachment,
    format_album,
    format_attachment,
)
from core.domain.value_objects.telegram_file import FileContentType, TelegramFileRecord


if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from adapters.inbound.telegram.ports import TelegramBotPorts, TelegramBotSettings

logger = logging.getLogger(__name__)


# Debounce de álbumes: cada miembro del media_group que llega REINICIA este
# timer; el álbum se cierra cuando pasa esta ventana sin miembros nuevos.
# Telegram entrega los miembros como mensajes separados en sucesión rápida
# (100-500ms entre cada uno), pero con álbumes grandes o red lenta un miembro
# puede tardar más — la ventana FIJA anterior (2s desde el PRIMER miembro)
# perdía los tardíos (bug real: álbum de 8 persistía 7).
ALBUM_DEBOUNCE_SEC = 1.5

# Máximo de media_group_id recordados como ya-flusheados (evita re-disparar el
# turno si un miembro llega DESPUÉS del cierre del álbum). Acotado para no
# crecer indefinidamente — uso doméstico, los álbumes son efímeros.
_ALBUM_DEDUP_MAX = 256


_DEFAULT_EXT_BY_TYPE: dict[str, str] = {
    "photo": ".jpg",
    "audio": ".ogg",
    "video": ".mp4",
    "file": ".bin",
}


def _extension_for(content_type: str, mime_type: str | None) -> str:
    """Adivina la extensión apropiada para un media. Replica la lógica de la tool download_from_telegram."""
    import mimetypes

    if mime_type:
        ext = mimetypes.guess_extension(mime_type)
        if ext:
            return ext
    return _DEFAULT_EXT_BY_TYPE.get(content_type, ".bin")


@dataclass
class _AlbumBuffer:
    """Estado in-memory de un álbum en curso (media_group_id sin cerrar)."""

    update: Update
    chat_type: str
    caption: str = ""
    task: asyncio.Task | None = None
    # True si este álbum (privado) tomó el slot del scope al crearse. El flush
    # lo libera al terminar. Sin esto, un texto que llega mientras el álbum
    # está en debounce+descargas arrancaba un turno ciego en paralelo (race
    # real: el usuario mandó las fotos y "ahí están", y el bot respondió que no
    # le llegaba nada porque su turno arrancó ANTES de que existiera el @album).
    slot_held: bool = False


class TelegramMediaMixin:
    """Handlers de media + helpers de persistencia/descarga de files."""

    # Contrato con TelegramBot — estado y colaboradores que este mixin consume.
    _settings: TelegramBotSettings
    _ports: TelegramBotPorts
    _voice_enabled: bool
    _is_authorized: Callable[[Update], bool]
    _set_reaction: Callable[..., Coroutine[Any, Any, None]]
    _run_pipeline: Callable[..., Coroutine[Any, Any, None]]
    _handle_group_message: Callable[..., Coroutine[Any, Any, None]]
    _schedule_group_flush: Callable[[str, str], None]
    _emit_event: Callable[..., Coroutine[Any, Any, None]]
    _album_buffers: dict[str, _AlbumBuffer]
    _albums_flushed: dict[str, None]

    async def _handle_photo_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handler para mensajes con foto (``filters.PHOTO``).

        Flujo:
        1. Album: si ``media_group_id`` está seteado, persiste el file_id y
           entra al debounce (``_debounce_album``) — el turno se dispara UNA
           vez cuando el álbum se cierra.
        2. Feature check: si ``process_photo`` no está disponible, responde con
           aviso de que la visión no está habilitada.
        3. Guarda los bytes al workspace (mismo cache-key que la tool
           ``download_from_telegram``) para tener el path local del bloque.
        4. Persiste el bloque ``@photo`` en el historial → obtiene history_id.
        5. Llama a ``ProcessPhotoUseCase.execute()`` → análisis + imagen anotada.
        6. Enriquece el bloque con ``@analysis`` vía ``update_message_content``.
        7. Llama a ``_run_pipeline`` en modo history-derived.
        """
        user = update.effective_user
        chat = update.effective_chat
        message = update.message
        if user is None or chat is None or message is None:
            return
        if not self._is_authorized(update):
            logger.warning(
                "Foto rechazada de user_id=%s (no autorizado)",
                user.id,
            )
            return

        # Álbumes: persisten file_id (sin face/scene) y entran al debounce.
        # Telegram entrega el álbum como N mensajes; el flush corre cuando
        # pasa ALBUM_DEBOUNCE_SEC sin miembros nuevos.
        if message.media_group_id is not None:
            media_group_id = str(message.media_group_id)
            await self._persist_incoming_file(update)
            caption_msg = (getattr(message, "caption", None) or "").strip()
            if media_group_id in self._albums_flushed:
                # Miembro tardío: el álbum ya cerró y su turno ya corrió.
                # Persistencia simétrica igual — rastro @photo sin re-turno.
                await self._record_straggler(update, str(chat.id))
                return
            await self._debounce_album(media_group_id, update, chat.type, caption_msg)
            return

        chat_type = chat.type
        chat_id = str(chat.id)

        # Feature check: si photos no está wired, avisar y salir.
        # En grupos hacemos return silencioso para no inundar el chat con el aviso
        # cada vez que llega una foto: el usuario sabe qué bot tiene fotos activadas
        # y los demás simplemente no participan.
        process_photo_uc = self._ports.process_photo
        if process_photo_uc is None:
            if chat_type not in _TIPOS_GRUPO:
                await message.reply_text("La función de reconocimiento visual no está habilitada.")
            return

        # Extraer bytes de la foto.
        payload = await extract_photo_payload(message)
        if payload is None:
            return
        image_bytes, _mime, _size = payload

        caption_raw = (getattr(message, "caption", None) or "").strip()
        # "!transcribí este texto" → prompt override para el scene describer.
        if caption_raw.startswith("!"):
            scene_prompt = caption_raw[1:].strip()
            caption = ""
        else:
            scene_prompt = None
            caption = caption_raw

        await self._set_reaction(update, "👀")

        # Los bytes ya están en memoria → al workspace sin segunda descarga.
        # Mismo dest (key = file_unique_id) que usaría download_from_telegram.
        photo_size = message.photo[-1]
        local_path = self._save_bytes_to_workspace(
            file_unique_id=photo_size.file_unique_id, ext=".jpg", data=image_bytes
        )
        att = IncomingAttachment(
            type="photo",
            path=str(local_path) if local_path is not None else None,
            file_ref=photo_size.file_unique_id,
        )

        # In-flight protection: en privados, si ya hay un turno corriendo sobre
        # este scope, tomamos un camino alternativo (record_user_message + ACK)
        # para no disparar un execute() paralelo. En grupos, el buffer-flush ya
        # se encarga (vía _schedule_group_flush idempotente al final).
        # Ver `in-flight-message-injection` y CLAUDE.md.
        # NO usa dispatch_inbound_turn a propósito: acá el slot se adquiere ANTES
        # del procesamiento pesado de la foto y el camino se decide al final,
        # porque el _run_pipeline anidado (user_input=None) depende de que el
        # slot ya esté tomado. Ver docstring de adapters/inbound/turn_dispatch.py.
        agent_id = self._ports.run_agent.get_agent_info().id
        scope = (agent_id, "telegram", chat_id)
        slot_acquired = False
        if chat_type not in _TIPOS_GRUPO:
            slot_acquired = await self._ports.scope_registry.try_mark_busy(scope)

        try:
            # Persistir el bloque @photo en el historial y obtener el history_id.
            # El caption del usuario (o el prompt "!" verbatim) viaja como @caption.
            placeholder_caption = f"!{scene_prompt}" if scene_prompt else caption
            history_content = format_attachment(att, caption=placeholder_caption)
            history_id = await self._ports.run_agent.record_photo_message(
                history_content,
                channel="telegram",
                chat_id=chat_id,
            )

            # Persistir file_id en telegram_files.db (best-effort).
            await self._persist_incoming_file(update, history_id=history_id)

            try:
                result = await process_photo_uc.execute(
                    image_bytes=image_bytes,
                    history_id=history_id,
                    agent_id=self._settings.id,
                    channel="telegram",
                    chat_id=chat_id,
                    chat_type=chat_type,
                    analysis_only=bool(caption),
                    scene_prompt=scene_prompt,
                )
            except Exception as exc:
                logger.exception("Error procesando foto Telegram para '%s'", self._settings.id)
                await message.reply_text(f"Error al procesar la foto: {exc}")
                await self._set_reaction(update, "👎")
                return

            # Enviar imagen anotada si existe (cara desconocida en chat privado).
            if result.annotated_image:
                await message.reply_photo(result.annotated_image)

            # Si el use case pide saltar el agente (photos.enabled=False en runtime),
            # responder con aviso solo en privado. En grupos return silencioso para no
            # ensuciar el chat — los bots sin la feature simplemente no participan.
            # El bloque @photo ya quedó persistido arriba: el depósito deja rastro
            # aunque la visión esté apagada.
            if result.should_skip_run_agent:
                if chat_type not in _TIPOS_GRUPO:
                    await message.reply_text(
                        "La función de reconocimiento visual no está habilitada."
                    )
                return

            # Modo transcripción/extracción ("!"): el resultado del descriptor va directo
            # al chat y se guarda como mensaje del asistente para que el usuario pueda iterar.
            # Usa send_html_or_plain (parte en fragmentos + fallback a texto plano):
            # una descripción larga ("describilo como un fotógrafo profesional") supera
            # los 4096 chars de Telegram y reply_text crudo reventaba con BadRequest.
            if scene_prompt and result.text_context:
                direct_text = result.text_context
                await send_html_or_plain(
                    lambda text, pm: message.reply_text(text, parse_mode=pm), direct_text
                )
                await self._ports.run_agent.record_assistant_message(
                    f"photo_transcription: {direct_text}",
                    channel="telegram",
                    chat_id=chat_id,
                )
                # Emitir user_input_photo solo si es chat grupal — sin assistant_response
                # porque este modo no pasa por LLM. Gated por flag.
                if chat_type in _TIPOS_GRUPO:
                    asyncio.ensure_future(
                        self._emit_event(
                            event_type="user_input_photo",
                            chat_id=chat_id,
                            content=direct_text,
                            sender=extract_sender_name(message),
                        )
                    )
                return

            # Bloque enriquecido: mismo @photo + @analysis (faces + scene) + @caption.
            # En grupos antepone el prefijo "{sender} (foto):" para mantener simetría
            # con los audios y con `_format_history_prefix` de los broadcasts entrantes.
            enriched_content = format_attachment(att, analysis=result.text_context, caption=caption)
            if chat_type in _TIPOS_GRUPO:
                sender = extract_sender_name(message)
                enriched_content = f"{sender} (foto):\n{enriched_content}"

            # CAMINO IN-FLIGHT (privado, slot ocupado): persistir el enriched como un
            # mensaje user NUEVO (no update_message_content) para que el drain del turno
            # en curso lo capte entre iteraciones — `_drain_new_user_messages` cuenta
            # filas nuevas, no detecta ediciones in-place. ACK rápido y volvemos sin
            # disparar otro execute().
            if not slot_acquired and chat_type not in _TIPOS_GRUPO:
                await self._ports.run_agent.record_user_message(
                    enriched_content, channel="telegram", chat_id=chat_id
                )
                await message.reply_text(INFLIGHT_ACK)
                return

            # CAMINO NORMAL: enriquecer el bloque @photo persistido al inicio con
            # el @analysis final. Esto evita un segundo mensaje role=user
            # consecutivo en el historial. El history_id no cambia → face_ref sigue
            # válido y el orden cronológico se preserva.
            await self._ports.run_agent.update_message_content(history_id, enriched_content)

            # Emitir user_input_photo (solo grupos) ANTES de correr el pipeline, para que
            # otros agentes vean la descripción antes que la respuesta del LLM.
            if chat_type in _TIPOS_GRUPO and result.text_context:
                asyncio.ensure_future(
                    self._emit_event(
                        event_type="user_input_photo",
                        chat_id=chat_id,
                        content=result.text_context,
                        sender=extract_sender_name(message),
                    )
                )

            # Si photos.debug está activo, registrar la ruta del archivo de debug
            # en run_agent para que Phase 2 (historial + system prompt + mensajes al
            # LLM) se agregue al archivo.
            if result.debug_path:
                self._ports.run_agent.set_photo_debug_path(result.debug_path)

            # Dispatch:
            # - Grupo: delegamos al buffer-flush idempotente. Si ya hay un flush
            #   pendiente disparado por mensajes previos, esta llamada es no-op y
            #   ese flush eventual va a leer la foto enriquecida junto con todo lo
            #   demás. Evita el race "foto + texto rápido" → dos execute() paralelos.
            # - Privado con slot adquirido: history-derived run_pipeline (la query
            #   del turno se deriva del trailing role=user que acabamos de actualizar).
            if chat_type in _TIPOS_GRUPO:
                self._schedule_group_flush(chat_id, chat_type)
            else:
                await self._run_pipeline(update, None, chat_type=chat_type)
        finally:
            # Liberar el slot SIEMPRE que lo hayamos adquirido, incluso si algún
            # camino lanzó excepción. Sin esto un fallo dejaría el scope busy
            # para siempre y todos los mensajes siguientes irían al ACK path.
            if slot_acquired:
                await self._ports.scope_registry.mark_idle(scope)

    def _extract_file_metadata(self, message) -> tuple[FileContentType, Any, str | None] | None:
        """Detecta el media payload de un Message y devuelve (content_type, payload, mime).

        ``payload`` es el objeto de telegram (PhotoSize, Voice, Audio, Video,
        VideoNote o Document) — desde ahí se leen ``file_id``, ``file_unique_id``
        y ``file_size``. Devuelve ``None`` si no hay media reconocible.
        """
        if message.photo:
            return "photo", message.photo[-1], "image/jpeg"
        if getattr(message, "voice", None):
            return "audio", message.voice, getattr(message.voice, "mime_type", None) or "audio/ogg"
        if getattr(message, "audio", None):
            return "audio", message.audio, getattr(message.audio, "mime_type", None)
        if getattr(message, "video", None):
            return "video", message.video, getattr(message.video, "mime_type", None) or "video/mp4"
        if getattr(message, "video_note", None):
            return "video", message.video_note, "video/mp4"
        if getattr(message, "document", None):
            document = message.document
            mime = getattr(document, "mime_type", None)
            # Un audio adjuntado "como archivo" ES un audio: Telegram clasifica
            # según cómo lo mandó el cliente, no según el contenido.
            if mime and mime.startswith("audio/"):
                return "audio", document, mime
            return "file", document, mime
        return None

    async def _debounce_album(
        self, media_group_id: str, update: Update, chat_type: str, caption: str
    ) -> None:
        """Registra un miembro del álbum y reinicia el timer de cierre.

        El flush (``_flush_album_later``) corre cuando pasa
        ``ALBUM_DEBOUNCE_SEC`` sin miembros nuevos.

        Al crear el buffer (primer miembro), un álbum PRIVADO toma el slot del
        scope: mientras el álbum se junta y descarga, cualquier mensaje del
        usuario cae al camino in-flight (``record_user_message`` + ACK) en vez
        de arrancar un turno paralelo ciego. El flush lo drena y libera el slot.
        En grupos NO se toma el slot — la coalescencia la maneja el buffer de
        grupo (``_schedule_group_flush``).
        """
        buf = self._album_buffers.get(media_group_id)
        if buf is None:
            buf = _AlbumBuffer(update=update, chat_type=chat_type)
            chat = update.effective_chat
            if chat_type not in _TIPOS_GRUPO and chat is not None:
                scope = (self._ports.run_agent.get_agent_info().id, "telegram", str(chat.id))
                buf.slot_held = await self._ports.scope_registry.try_mark_busy(scope)
            self._album_buffers[media_group_id] = buf
        # Telegram suele poner el caption en UNA sola foto del álbum (no
        # siempre la primera) — el primer no-vacío gana como fallback del
        # que se recupere de los records al flushear.
        if caption and not buf.caption:
            buf.caption = caption
        if buf.task is not None and not buf.task.done():
            buf.task.cancel()
        buf.task = asyncio.create_task(self._flush_album_later(media_group_id))

    def _scope_for(self, update: Update) -> tuple[str, str, str] | None:
        """Scope ``(agent_id, 'telegram', chat_id)`` del update, o ``None`` sin chat."""
        chat = update.effective_chat
        if chat is None:
            return None
        return (self._ports.run_agent.get_agent_info().id, "telegram", str(chat.id))

    async def _flush_album_later(self, media_group_id: str) -> None:
        """Cierra el álbum tras la ventana de silencio y dispara el turno.

        Corre como task suelto (fuera del ciclo de handlers de PTB) → los
        errores se capturan acá y se loguean; no hay error handler upstream.

        Persistencia + turno según el tier:
        - Grupo: delega a ``_handle_group_message`` (buffer de grupo).
        - Privado con slot tomado: persiste el bloque ``@album`` y corre un
          turno history-derived (``_run_pipeline(update, None)``) que ve el
          ``@album`` + cualquier mensaje recordado in-flight mientras juntábamos
          el álbum. Libera el slot en ``finally``.
        - Privado sin slot (había un turno corriendo al crear el álbum): solo
          persiste el ``@album`` — ese turno en curso lo drena. No dispara otro.
        """
        await asyncio.sleep(ALBUM_DEBOUNCE_SEC)
        buf = self._album_buffers.pop(media_group_id, None)
        if buf is None:
            return
        self._albums_flushed[media_group_id] = None
        if len(self._albums_flushed) > _ALBUM_DEDUP_MAX:
            del self._albums_flushed[next(iter(self._albums_flushed))]

        update = buf.update
        scope = self._scope_for(update) if buf.slot_held else None
        try:
            chat = update.effective_chat
            if chat is None:
                return
            chat_id_str = str(chat.id)
            members, caption_db = await self._gather_album_members(
                media_group_id=media_group_id, chat_id=chat_id_str
            )
            caption = caption_db or buf.caption
            user_input = format_album(members, caption=caption)

            if buf.chat_type in _TIPOS_GRUPO:
                await self._handle_group_message(
                    update, user_input, buf.chat_type, preformatted=True
                )
            elif buf.slot_held:
                # Tenemos el slot: persistir el @album y correr un turno
                # history-derived. La query se deriva del trailing batch
                # (@album + lo recordado in-flight). No re-adquiere el slot
                # (user_input=None ⇒ _run_pipeline salta dispatch_inbound_turn).
                await self._ports.run_agent.record_user_message(
                    user_input, channel="telegram", chat_id=chat_id_str
                )
                await self._set_reaction(update, "👀")
                await self._run_pipeline(update, None, chat_type=buf.chat_type)
            else:
                # Había un turno corriendo cuando llegó el álbum: solo dejamos el
                # bloque persistido para que ese turno lo drene entre iteraciones.
                await self._ports.run_agent.record_user_message(
                    user_input, channel="telegram", chat_id=chat_id_str
                )
        except Exception:
            logger.exception(
                "Error flusheando álbum (agent=%s, media_group_id=%s)",
                self._settings.id,
                media_group_id,
            )
        finally:
            if scope is not None:
                await self._ports.scope_registry.mark_idle(scope)

    async def _record_straggler(self, update: Update, chat_id: str) -> None:
        """Persiste el bloque @ de un miembro de álbum llegado DESPUÉS del flush.

        El turno del álbum ya corrió — re-dispararlo duplicaría trabajo. Pero el
        archivo NO puede desaparecer del contexto: rastro sin turno (el mismo
        principio que el depósito de archivos sin caption).
        """
        message = update.message
        meta = self._extract_file_metadata(message) if message is not None else None
        if meta is None:
            return
        content_type, payload, mime_type = meta
        local_path = await self._pre_download_media(
            file_id=payload.file_id,
            file_unique_id=payload.file_unique_id,
            content_type=content_type,
            mime_type=mime_type,
        )
        att = IncomingAttachment(
            type=content_type,
            path=str(local_path) if local_path is not None else None,
            mime=mime_type if content_type != "photo" else None,
            file_ref=payload.file_unique_id,
        )
        await self._ports.run_agent.record_user_message(
            format_attachment(att), channel="telegram", chat_id=chat_id
        )

    async def _gather_album_members(
        self, *, media_group_id: str, chat_id: str
    ) -> tuple[list[IncomingAttachment], str]:
        """Junta y pre-descarga TODOS los miembros de un media_group_id, con su caption.

        Llamar DESPUÉS de que el debounce venza — para entonces todos los
        miembros persistieron su record. Devuelve ``(members, caption)``: los
        attachments en orden de recepción (received_at ASC) y el primer caption
        no-vacío entre los miembros. Best-effort: si el repo o el downloader no
        están, devuelve ``([], "")`` y el formatter degrada a ``@album pending``.
        """
        repo = self._ports.telegram_file_repo
        if repo is None:
            return [], ""

        try:
            records = await repo.query_by_media_group(
                agent_id=self._settings.id,
                channel="telegram",
                chat_id=chat_id,
                media_group_id=media_group_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "No pude leer álbum del repo (agent=%s, mgid=%s): %s",
                self._settings.id,
                media_group_id,
                exc,
            )
            return [], ""

        members: list[IncomingAttachment] = []
        caption = ""
        for record in records:
            if not caption and record.caption:
                caption = record.caption.strip()
            local_path = await self._pre_download_media(
                file_id=record.file_id,
                file_unique_id=record.file_unique_id,
                content_type=record.content_type,
                mime_type=record.mime_type,
            )
            members.append(
                IncomingAttachment(
                    type=record.content_type,
                    path=str(local_path) if local_path is not None else None,
                    mime=record.mime_type if record.content_type != "photo" else None,
                    file_ref=record.file_unique_id,
                )
            )
        return members, caption

    def _save_bytes_to_workspace(
        self, *, file_unique_id: str, ext: str, data: bytes
    ) -> "Path | None":
        """Escribe bytes YA descargados al cache del workspace (idempotente).

        Mismo dest que ``_pre_download_media``/``download_from_telegram``
        (cache key = ``file_unique_id``) pero sin segunda descarga: los bytes
        ya están en memoria (fotos y audios se bajan completos para procesar).
        Best-effort: ante error loguea y devuelve ``None`` (bloque degradado).
        """
        workspace = Path(self._settings.workspace_path).expanduser().resolve()
        dest = workspace / "telegram" / f"{file_unique_id}{ext}"
        if dest.exists():
            return dest
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return dest
        except OSError as exc:
            logger.warning(
                "No pude guardar media al workspace (agent=%s, file_unique_id=%s): %s",
                self._settings.id,
                file_unique_id,
                exc,
            )
            return None

    async def _pre_download_media(
        self,
        *,
        file_id: str,
        file_unique_id: str,
        content_type: FileContentType,
        mime_type: str | None,
    ) -> "Path | None":
        """Descarga el media a ``<workspace>/telegram/<file_unique_id>.<ext>`` (idempotente).

        Devuelve el path local o ``None`` si no hay downloader o falla la
        descarga. El path queda absoluto y es idéntico al que devolvería
        ``download_from_telegram``: cache key = ``file_unique_id``.

        Best-effort: cualquier excepción se loggea y devuelve None.
        """
        downloader = self._ports.telegram_file_downloader
        if downloader is None:
            return None

        workspace = Path(self._settings.workspace_path).expanduser().resolve()
        download_dir = workspace / "telegram"

        ext = _extension_for(content_type, mime_type)
        dest = download_dir / f"{file_unique_id}{ext}"

        if dest.exists():
            return dest

        try:
            await downloader.download(file_id=file_id, dest=dest)
            return dest
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "No pude pre-descargar media (agent=%s, file_unique_id=%s): %s",
                self._settings.id,
                file_unique_id,
                exc,
            )
            return None

    async def _persist_incoming_file(
        self,
        update: Update,
        *,
        history_id: int | None = None,
    ) -> None:
        """Guarda metadata del media en ``telegram_files.db`` (best-effort).

        No lanza: si el repo no está disponible o la persistencia falla, se
        loggea y se continúa — la feature no debe romper el flujo principal.
        """
        repo = self._ports.telegram_file_repo
        if repo is None:
            return
        chat = update.effective_chat
        message = update.message
        if chat is None or message is None:
            return
        meta = self._extract_file_metadata(message)
        if meta is None:
            return
        content_type, payload, mime_type = meta

        try:
            from datetime import datetime, timezone

            received = (
                message.date.astimezone(timezone.utc)
                if getattr(message, "date", None) is not None
                else datetime.now(timezone.utc)
            )
            caption = getattr(message, "caption", None) or None
            if caption is not None:
                caption = caption.strip() or None

            record = TelegramFileRecord(
                agent_id=self._settings.id,
                channel="telegram",
                chat_id=str(chat.id),
                content_type=content_type,
                file_id=payload.file_id,
                file_unique_id=payload.file_unique_id,
                media_group_id=getattr(message, "media_group_id", None),
                caption=caption,
                history_id=history_id,
                mime_type=mime_type,
                received_at=received,
            )
            await repo.save(record)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "No se pudo persistir telegram_file (agent=%s, content_type=%s): %s",
                self._settings.id,
                content_type,
                exc,
            )

    async def _handle_silent_media(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handler para video/document.

        - Documento con mime ``audio/*`` → delega al pipeline de voz (un mp3
          adjuntado "como archivo" es un audio y debe transcribirse).
        - Con ``media_group_id`` → mismo debounce de álbum que las fotos
          (Telegram agrupa también documentos/videos enviados juntos).
        - Con caption → dispara el turno con el bloque ``@file``/``@video``.
        - Sin caption → persiste el bloque en el historial SIN turno: depósito
          CON rastro (antes era invisible — el agente no se enteraba de que
          llegó un archivo, bug del "audio viejo").
        """
        user = update.effective_user
        if user is None:
            return
        if not self._is_authorized(update):
            return

        message = update.message
        if message is None:
            return

        meta = self._extract_file_metadata(message)
        if meta is None:
            return
        content_type, payload, mime_type = meta

        # Audio disfrazado de documento → pipeline de voz completo (persiste
        # su propio record y su bloque @audio, con o sin transcripción).
        if content_type == "audio":
            await self._handle_voice_message(update, context)
            return

        await self._persist_incoming_file(update)

        chat = update.effective_chat
        chat_id_str = str(chat.id) if chat is not None else ""
        chat_type = message.chat.type if message.chat else "private"
        caption = (getattr(message, "caption", None) or "").strip()

        # Archivos enviados juntos comparten media_group_id — mismo mecanismo
        # de coalescencia que los álbumes de fotos.
        if getattr(message, "media_group_id", None) is not None:
            media_group_id = str(message.media_group_id)
            if media_group_id in self._albums_flushed:
                await self._record_straggler(update, chat_id_str)
                return
            await self._debounce_album(media_group_id, update, chat_type, caption)
            return

        # Pre-descargar al workspace para entregar un path concreto al LLM —
        # evita depender del RAG de tools y de que el LLM elija
        # ``download_from_telegram``.
        local_path = await self._pre_download_media(
            file_id=payload.file_id,
            file_unique_id=payload.file_unique_id,
            content_type=content_type,
            mime_type=mime_type,
        )
        att = IncomingAttachment(
            type=content_type,
            name=getattr(payload, "file_name", None),
            mime=mime_type,
            path=str(local_path) if local_path is not None else None,
            file_ref=payload.file_unique_id,
        )

        if not caption:
            # Depósito sin instrucción: rastro en el historial, sin turno ni
            # tokens. El próximo turno del usuario ve el bloque en su contexto.
            block = format_attachment(att)
            if chat_type in _TIPOS_GRUPO:
                block = f"{extract_sender_name(message)} sent:\n{block}"
            await self._ports.run_agent.record_user_message(
                block, channel="telegram", chat_id=chat_id_str
            )
            return

        user_input = format_attachment(att, caption=caption)
        if chat_type in _TIPOS_GRUPO:
            await self._handle_group_message(update, user_input, chat_type, preformatted=True)
        else:
            await self._set_reaction(update, "👀")
            await self._run_pipeline(update, user_input, chat_type=chat_type)

    async def _handle_voice_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handler único para ``voice``, ``audio``, ``video_note`` y document-audio.

        Flujo: allow → persist file_id → voice_enabled → descarga+mime →
        size-check → transcribir → turno con el bloque ``@audio`` +
        ``@transcription``.

        Persistencia simétrica: TODAS las salidas tempranas (voz deshabilitada,
        audio muy grande, transcripción fallida o vacía) dejan igualmente el
        bloque ``@audio`` en el historial — sin esto el agente no tiene forma
        de saber qué archivo le mandaron (bug del "audio viejo": el LLM
        adivinaba con download_from_telegram y traía otro).
        """
        user = update.effective_user
        message = update.message
        if user is None or message is None:
            return
        if not self._is_authorized(update):
            logger.warning(
                "Audio rechazado de user_id=%s (no autorizado)",
                user.id,
            )
            return

        # Persistir file_id SIEMPRE, antes de cualquier check de feature o tamaño:
        # el LLM debe poder recuperar el media aunque la transcripción esté
        # apagada o el audio sea demasiado grande para procesar.
        await self._persist_incoming_file(update)

        meta = self._extract_file_metadata(message)
        if meta is None:
            return
        content_type, payload, mime_type = meta

        chat = update.effective_chat
        chat_id_str = str(chat.id) if chat is not None else ""
        chat_type = message.chat.type if message.chat else "private"

        async def _persistir_marcador(local_path: Path | None) -> None:
            """Deja el bloque @ en el historial sin disparar turno (salidas tempranas)."""
            att = IncomingAttachment(
                type=content_type,
                name=getattr(payload, "file_name", None),
                mime=mime_type,
                path=str(local_path) if local_path is not None else None,
                file_ref=payload.file_unique_id,
            )
            block = format_attachment(att)
            if chat_type in _TIPOS_GRUPO:
                block = f"{extract_sender_name(message)} sent:\n{block}"
            await self._ports.run_agent.record_user_message(
                block, channel="telegram", chat_id=chat_id_str
            )

        if not self._voice_enabled:
            # Transcripción deshabilitada: depósito con rastro, sin reply.
            await _persistir_marcador(
                await self._pre_download_media(
                    file_id=payload.file_id,
                    file_unique_id=payload.file_unique_id,
                    content_type=content_type,
                    mime_type=mime_type,
                )
            )
            return

        audio_payload = await extract_audio_payload(message)
        if audio_payload is None:
            return  # Defensa: ningún audio presente (no debería ocurrir por los filters).
        audio_bytes, mime, file_size = audio_payload

        # Los bytes ya están en memoria → al workspace sin segunda descarga.
        local_path = self._save_bytes_to_workspace(
            file_unique_id=payload.file_unique_id,
            ext=_extension_for(content_type, mime),
            data=audio_bytes,
        )

        # Size-check: preferimos file_size de Telegram (antes de procesar),
        # con fallback al tamaño real descargado.
        transcription_cfg = self._settings.transcription
        transcription_provider = self._ports.transcription
        if transcription_cfg is None or transcription_provider is None:
            await _persistir_marcador(local_path)
            return
        max_mb = transcription_cfg.max_audio_mb
        max_bytes = max_mb * 1024 * 1024
        effective_size = file_size or len(audio_bytes)
        if effective_size > max_bytes:
            logger.warning(
                "Audio demasiado grande para agente '%s': %d bytes > límite %d bytes",
                self._settings.id,
                effective_size,
                max_bytes,
            )
            await _persistir_marcador(local_path)
            await self._set_reaction(update, "👎")
            await message.reply_text(
                f"El audio es demasiado grande ({effective_size // (1024 * 1024)} MB). "
                f"Máximo permitido: {max_mb} MB."
            )
            return

        await self._set_reaction(update, "👀")

        # Transcribir — errores del provider se reportan al usuario pero NO
        # corren el pipeline (sin texto no hay nada que ejecutar).
        try:
            transcribed = await transcription_provider.transcribe(
                audio_bytes,
                mime,
                language=transcription_cfg.language,
            )
        except TranscriptionError as exc:
            logger.warning("Transcripción fallida para agente '%s': %s", self._settings.id, exc)
            await _persistir_marcador(local_path)
            await message.reply_text(f"No pude transcribir el audio: {exc}")
            await self._set_reaction(update, "👎")
            return

        if not transcribed or not transcribed.strip():
            await _persistir_marcador(local_path)
            await message.reply_text("La transcripción vino vacía.")
            await self._set_reaction(update, "👎")
            return

        sender = extract_sender_name(message)

        # Emitir el evento user_input_voice si el flag está activo. Solo aplica
        # a chats grupales — en privado no hay otros agentes que reciban el evento.
        # El content del broadcast sigue siendo la transcripción cruda (wire
        # format sin cambios — los otros bots aplican su propio prefijo).
        if chat_type in _TIPOS_GRUPO and chat is not None:
            asyncio.ensure_future(
                self._emit_event(
                    event_type="user_input_voice",
                    chat_id=str(chat.id),
                    content=transcribed,
                    sender=sender,
                )
            )

        att = IncomingAttachment(
            type=content_type,
            name=getattr(payload, "file_name", None),
            mime=mime,
            path=str(local_path) if local_path is not None else None,
            file_ref=payload.file_unique_id,
        )
        block = format_attachment(att, transcription=transcribed)
        # En grupos, el bot originante antepone el sender — misma estructura
        # que `_format_history_prefix` aplica a los broadcasts entrantes.
        if chat_type in _TIPOS_GRUPO:
            user_input = f"{sender} (audio):\n{block}"
        else:
            user_input = block
        await self._run_pipeline(update, user_input, chat_type=chat_type)
