"""Pipeline de media entrante del TelegramBot: fotos, álbumes, voz, video y documentos.

Mixin de ``TelegramBot``. Incluye la persistencia de file_id en
``telegram_files.db`` y la pre-descarga al workspace."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telegram import Update
from telegram.ext import ContextTypes

from adapters.inbound.telegram.message_mapper import (
    _TIPOS_GRUPO,
    extract_audio_payload,
    extract_photo_payload,
    extract_sender_name,
)
from adapters.inbound.turn_dispatch import INFLIGHT_ACK
from core.domain.errors import TranscriptionError
from core.domain.value_objects.telegram_file import FileContentType, TelegramFileRecord


if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from adapters.inbound.telegram.ports import TelegramBotPorts, TelegramBotSettings

logger = logging.getLogger(__name__)


# Ventana de espera para reunir todas las fotos de un álbum antes de disparar
# el pipeline. Telegram entrega los miembros de un álbum como mensajes
# separados en sucesión rápida (100-500ms entre cada uno). El handler que
# recibe el caption duerme esta cantidad para que los demás miembros lleguen
# y se persistan, y luego junta TODOS los paths para el LLM.
ALBUM_GATHER_DELAY_SEC = 2.0

# Máximo de media_group_id recordados para el dedup de álbumes (evita re-disparar
# el turno por cada una de las N fotos del mismo álbum). Acotado para no crecer
# indefinidamente — uso doméstico, los álbumes son efímeros.
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
    _albums_seen: dict[str, None]

    async def _handle_photo_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handler para mensajes con foto (``filters.PHOTO``).

        Flujo:
        1. Album guard: si ``media_group_id`` está seteado, descarta silenciosamente
           (sin respuesta, sin tokens, sin escritura en DB).
        2. Feature check: si ``process_photo`` no está disponible, responde con
           aviso de que la visión no está habilitada.
        3. Descarga la foto de mayor resolución.
        4. Persiste el mensaje "[foto recibida]" en el historial → obtiene history_id.
        5. Llama a ``ProcessPhotoUseCase.execute()`` → texto contextual + imagen anotada.
        6. Si hay imagen anotada, la envía con ``reply_photo``.
        7. Llama a ``_run_pipeline`` con el texto contextual como user_input.
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

        # Álbumes: persisten file_id (sin face/scene) y disparan el pipeline UNA
        # vez con __ALBUM__, traiga o no caption. Telegram entrega el álbum como
        # N mensajes; usamos dedup por media_group_id para que solo el PRIMERO
        # dispare el turno coalescido y los demás solo persistan. Antes, un álbum
        # sin caption quedaba mudo y el bot "no se enteraba" — ahora siempre avisa.
        if message.media_group_id is not None:
            media_group_id = str(message.media_group_id)
            await self._persist_incoming_file(update)
            # Caption de ESTE mensaje, como fallback si el gather no lo recupera
            # de los records (p.ej. el primer miembro que dispara antes de que el
            # resto del álbum persista su caption).
            caption_msg = (getattr(message, "caption", None) or "").strip()

            # Dedup atómico (sin await entre el check y el add → seguro con
            # concurrent_updates(True)): solo el primer miembro continúa.
            if media_group_id in self._albums_seen:
                return
            self._albums_seen[media_group_id] = None
            if len(self._albums_seen) > _ALBUM_DEDUP_MAX:
                del self._albums_seen[next(iter(self._albums_seen))]

            # Race condition de Telegram: las demás fotos del álbum aún no han
            # llegado en este momento. Esperamos un poco para que se persistan
            # y después juntamos TODAS las del media_group_id (y el caption, venga
            # en la foto que venga) desde la DB.
            await asyncio.sleep(ALBUM_GATHER_DELAY_SEC)

            chat_id_str = str(chat.id)
            paths_album, caption_db = await self._gather_album_paths(
                media_group_id=media_group_id, chat_id=chat_id_str
            )
            caption = caption_db or caption_msg
            if paths_album:
                paths_str = "\n".join(f"- {p}" for p in paths_album)
                ubicacion_album = f" ({len(paths_album)} photos):\n{paths_str}"
            else:
                ubicacion_album = ""

            user_input = f"__ALBUM__{ubicacion_album}\n\n{caption}".rstrip()
            chat_type_album = chat.type
            if chat_type_album in _TIPOS_GRUPO:
                await self._handle_group_message(update, user_input, chat_type_album)
            else:
                await self._set_reaction(update, "👀")
                await self._run_pipeline(update, user_input, chat_type=chat_type_album)
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
            # Persistir en el historial y obtener el history_id.
            # Si el usuario adjuntó una descripción, la incluimos en el registro.
            if scene_prompt:
                history_content = f"__PHOTO__ !{scene_prompt}"
            elif caption:
                history_content = f"__PHOTO__ {caption}"
            else:
                history_content = "__PHOTO__"
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
            if result.should_skip_run_agent:
                if chat_type not in _TIPOS_GRUPO:
                    await message.reply_text(
                        "La función de reconocimiento visual no está habilitada."
                    )
                return

            # Modo transcripción/extracción ("!"): el resultado del descriptor va directo
            # al chat y se guarda como mensaje del asistente para que el usuario pueda iterar.
            if scene_prompt and result.text_context:
                direct_text = result.text_context
                await message.reply_text(direct_text)
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

            # Construir el contenido enriquecido (faces + scene + caption + prefijo grupal).
            # En grupos antepone el prefijo "{sender} (foto): ..." para mantener simetría con
            # los audios (`{sender} (audio): ...`) y con `_format_history_prefix` aplicado a
            # los broadcasts entrantes — así originante y receptores ven la misma estructura.
            enriched_content = result.text_context
            if caption:
                enriched_content = f"{result.text_context}\n\nDescripción del usuario: {caption}"
            if chat_type in _TIPOS_GRUPO:
                sender = extract_sender_name(message)
                enriched_content = f"{sender} (foto): {enriched_content}"

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

            # CAMINO NORMAL: enriquecer el placeholder __PHOTO__ persistido al inicio
            # con el text_context final. Esto evita un segundo mensaje role=user
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
            return "file", message.document, getattr(message.document, "mime_type", None)
        return None

    async def _gather_album_paths(
        self, *, media_group_id: str, chat_id: str
    ) -> tuple[list["Path"], str]:
        """Junta y pre-descarga TODAS las fotos de un media_group_id, con su caption.

        Llamar DESPUÉS de esperar la ventana ``ALBUM_GATHER_DELAY_SEC`` para
        que las demás fotos del álbum hayan sido persistidas por sus handlers.

        Devuelve ``(paths, caption)``: los paths absolutos en orden de recepción
        (received_at ASC) y el primer caption no-vacío entre los miembros
        (Telegram suele ponerlo en una sola foto del álbum, no siempre la
        primera). Best-effort: si el repo o el downloader no están, devuelve
        ``([], "")``.
        """
        repo = self._ports.telegram_file_repo
        if repo is None:
            return [], ""

        try:
            # 100 cubre cualquier álbum razonable (Telegram limita a 10
            # miembros por álbum; dejamos margen para múltiples álbumes
            # recientes y filtramos por media_group_id).
            records = await repo.query_recent(
                agent_id=self._settings.id,
                channel="telegram",
                chat_id=chat_id,
                content_type="album",
                count=100,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "No pude leer álbum del repo (agent=%s, mgid=%s): %s",
                self._settings.id,
                media_group_id,
                exc,
            )
            return [], ""

        paths: list[Path] = []
        caption = ""
        for record in records:
            if record.media_group_id != media_group_id:
                continue
            if not caption and record.caption:
                caption = record.caption.strip()
            local_path = await self._pre_download_media(
                file_id=record.file_id,
                file_unique_id=record.file_unique_id,
                content_type="photo",
                mime_type=record.mime_type,
            )
            if local_path is not None:
                paths.append(local_path)
        return paths, caption

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

        - Persiste el ``file_id`` SIEMPRE en ``telegram_files.db``.
        - Si el media trae caption, lo trata como un mensaje del usuario:
          inyecta ``__FILE__/__VIDEO__ <name>\\n\\n<caption>`` y dispara el
          pipeline (privado o grupo según corresponda).
        - Sin caption: queda persistido para que el LLM lo recupere después
          con ``download_from_telegram``, pero NO genera respuesta.
        """
        user = update.effective_user
        if user is None:
            return
        if not self._is_authorized(update):
            return

        await self._persist_incoming_file(update)

        message = update.message
        if message is None:
            return
        caption = (getattr(message, "caption", None) or "").strip()
        if not caption:
            return  # silencioso: archivo "depositado" sin instrucción

        meta = self._extract_file_metadata(message)
        if meta is None:
            return
        content_type, payload, mime_type = meta
        prefix = "__VIDEO__" if content_type == "video" else "__FILE__"
        filename = getattr(payload, "file_name", None) or "<unnamed>"

        # Pre-descargar al workspace para entregar un path concreto al LLM —
        # evita depender del RAG de tools y de que el LLM elija
        # ``download_from_telegram``.
        local_path = await self._pre_download_media(
            file_id=payload.file_id,
            file_unique_id=payload.file_unique_id,
            content_type=content_type,
            mime_type=mime_type,
        )
        ubicacion = f" at {local_path}" if local_path is not None else ""
        user_input = f"{prefix} {filename}{ubicacion}\n\n{caption}"

        chat_type = message.chat.type if message.chat else "private"
        if chat_type in _TIPOS_GRUPO:
            await self._handle_group_message(update, user_input, chat_type)
        else:
            await self._set_reaction(update, "👀")
            await self._run_pipeline(update, user_input, chat_type=chat_type)

    async def _handle_voice_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handler único para `voice`, `audio` y `video_note`.

        Flujo: allow → voice_enabled → 🔊 → descarga+mime → size-check →
        transcribir → reinyectar el texto en el mismo pipeline que `_handle_message`.
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

        if not self._voice_enabled:
            # Transcripción deshabilitada: ya persistimos, salimos sin reply.
            return

        payload = await extract_audio_payload(message)
        if payload is None:
            return  # Defensa: ningún audio presente (no debería ocurrir por los filters).
        audio_bytes, mime, file_size = payload

        # Size-check: preferimos file_size de Telegram (antes de procesar),
        # con fallback al tamaño real descargado.
        transcription_cfg = self._settings.transcription
        transcription_provider = self._ports.transcription
        if transcription_cfg is None or transcription_provider is None:
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
            await message.reply_text(f"No pude transcribir el audio: {exc}")
            await self._set_reaction(update, "👎")
            return

        if not transcribed or not transcribed.strip():
            await message.reply_text("La transcripción vino vacía.")
            await self._set_reaction(update, "👎")
            return

        chat = update.effective_chat
        chat_type = message.chat.type if message.chat else "private"
        sender = extract_sender_name(message)

        # Emitir el evento user_input_voice si el flag está activo. Solo aplica
        # a chats grupales — en privado no hay otros agentes que reciban el evento.
        if chat_type in _TIPOS_GRUPO and chat is not None:
            asyncio.ensure_future(
                self._emit_event(
                    event_type="user_input_voice",
                    chat_id=str(chat.id),
                    content=transcribed,
                    sender=sender,
                )
            )

        # En grupos, el bot originante persiste el audio con el mismo formato que
        # `_format_history_prefix` aplica a los broadcasts entrantes — así los
        # demás bots y este ven la misma estructura `{sender} (audio): {texto}`.
        # En privado se pasa la transcripción cruda (no hay otros remitentes).
        if chat_type in _TIPOS_GRUPO:
            user_input = f"{sender} (audio): {transcribed}"
        else:
            user_input = transcribed
        await self._run_pipeline(update, user_input, chat_type=chat_type)
