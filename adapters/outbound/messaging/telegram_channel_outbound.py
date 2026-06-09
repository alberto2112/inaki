"""TelegramChannelOutbound — implementación de ``IChannelOutbound`` para Telegram.

Unifica la lógica de ``TelegramFileSender`` (photo/audio/video/file/album) y
``TelegramMessageSender`` (texto) bajo la interfaz genérica ``IChannelOutbound``.

El adapter persiste el envío exitoso en ``IHistoryStore`` como ``Role.ASSISTANT``
bajo el scope ``(agent_id, "telegram", chat_id)``. Esto asegura que el historial
refleje lo enviado aunque el LLM no haya generado texto en ese turno.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from core.domain.entities.message import Message, Role
from core.domain.value_objects.outbound_kind import OutboundKind
from core.ports.outbound.channel_outbound_port import IChannelOutbound
from core.ports.outbound.history_port import IHistoryStore

logger = logging.getLogger(__name__)


class TelegramChannelOutbound(IChannelOutbound):
    """Adapter de envío saliente para Telegram.

    Soporta todos los kinds: TEXT, PHOTO, AUDIO, VIDEO, FILE y ALBUM.
    """

    channel_name = "telegram"

    def __init__(
        self,
        get_telegram_bot: Callable[[], object | None],
        history: IHistoryStore,
        agent_id: str,
    ) -> None:
        """Inicializa el adapter.

        Args:
            get_telegram_bot: Callable que devuelve el bot activo, o None si
                Telegram no está disponible en este momento.
            history: Store de historial donde se persiste cada envío exitoso.
            agent_id: Identificador del agente que realiza el envío.
        """
        self._get_telegram_bot = get_telegram_bot
        self._history = history
        self._agent_id = agent_id

    def capabilities(self) -> set[OutboundKind]:
        """Retorna los kinds soportados por Telegram."""
        return {
            OutboundKind.TEXT,
            OutboundKind.PHOTO,
            OutboundKind.AUDIO,
            OutboundKind.VIDEO,
            OutboundKind.FILE,
            OutboundKind.ALBUM,
        }

    async def send(
        self,
        *,
        chat_id: str,
        kind: OutboundKind,
        text: str | None = None,
        sources: list[Path] | None = None,
        caption: str | None = None,
    ) -> None:
        """Envía un payload a Telegram y lo persiste en el historial.

        Valida precondiciones antes de llamar a la API de Telegram:
        - kind no soportado → ``ValueError``
        - TEXT sin texto → ``ValueError``
        - media sin sources → ``ValueError``
        - archivo inexistente → ``FileNotFoundError``
        - bot no disponible → ``RuntimeError``
        """
        if kind not in self.capabilities():
            raise ValueError(
                f"El canal 'telegram' no soporta kind={kind.value!r}. "
                f"Kinds soportados: {[k.value for k in self.capabilities()]}"
            )

        if kind == OutboundKind.TEXT:
            await self._enviar_texto(chat_id=chat_id, text=text)
            contenido_historial = text or ""
        elif kind == OutboundKind.ALBUM:
            await self._enviar_album(chat_id=chat_id, sources=sources or [], caption=caption)
            contenido_historial = caption or ""
        else:
            # PHOTO, AUDIO, VIDEO, FILE — media individual
            fuentes = sources or []
            if len(fuentes) != 1:
                raise ValueError(
                    f"kind={kind.value!r} requiere exactamente 1 source; "
                    f"se recibieron {len(fuentes)}"
                )
            await self._enviar_media(chat_id=chat_id, kind=kind, source=fuentes[0], caption=caption)
            contenido_historial = caption or ""

        # Persistir en historial bajo scope (agent_id, "telegram", chat_id)
        await self._history.append(
            self._agent_id,
            Message(role=Role.ASSISTANT, content=contenido_historial),
            channel="telegram",
            chat_id=chat_id,
        )

    # ---------------------------------------------------------------------------
    # Métodos privados de envío
    # ---------------------------------------------------------------------------

    async def _enviar_texto(self, *, chat_id: str, text: str | None) -> None:
        """Envía un mensaje de texto a Telegram."""
        if not text or not text.strip():
            raise ValueError("el texto del mensaje no puede ser vacío para kind=TEXT")
        bot = self._require_bot()
        chat_id_int = self._parse_chat_id(chat_id)
        await bot.send_message(chat_id=chat_id_int, text=text)  # type: ignore[attr-defined]

    async def _enviar_media(
        self,
        *,
        chat_id: str,
        kind: OutboundKind,
        source: Path,
        caption: str | None,
    ) -> None:
        """Envía un archivo individual (photo/audio/video/file)."""
        bot = self._require_bot()
        chat_id_int = self._parse_chat_id(chat_id)

        if not source.exists():
            raise FileNotFoundError(f"El fichero no existe: {source}")

        handle = source.open("rb")
        try:
            if kind == OutboundKind.PHOTO:
                await bot.send_photo(  # type: ignore[attr-defined]
                    chat_id=chat_id_int, photo=handle, caption=caption
                )
            elif kind == OutboundKind.AUDIO:
                await bot.send_audio(  # type: ignore[attr-defined]
                    chat_id=chat_id_int, audio=handle, caption=caption
                )
            elif kind == OutboundKind.VIDEO:
                await bot.send_video(  # type: ignore[attr-defined]
                    chat_id=chat_id_int, video=handle, caption=caption
                )
            elif kind == OutboundKind.FILE:
                await bot.send_document(  # type: ignore[attr-defined]
                    chat_id=chat_id_int, document=handle, caption=caption
                )
            else:  # pragma: no cover — never reached; kind validado antes
                raise ValueError(f"kind no manejado en _enviar_media: {kind!r}")
        finally:
            handle.close()

    async def _enviar_album(
        self,
        *,
        chat_id: str,
        sources: list[Path],
        caption: str | None,
    ) -> None:
        """Envía un álbum de fotos.

        Si ``sources`` tiene una sola foto, delega a ``_enviar_media`` (PHOTO).
        Con múltiples fotos, usa ``send_media_group``.
        """
        if not sources:
            raise ValueError("ALBUM requiere al menos 1 source")

        if len(sources) == 1:
            await self._enviar_media(
                chat_id=chat_id,
                kind=OutboundKind.PHOTO,
                source=sources[0],
                caption=caption,
            )
            return

        bot = self._require_bot()
        chat_id_int = self._parse_chat_id(chat_id)

        for path in sources:
            if not path.exists():
                raise FileNotFoundError(f"El fichero no existe: {path}")

        # Lazy import para no atar el adapter al módulo telegram en tiempo de carga
        from telegram import InputMediaPhoto  # noqa: PLC0415

        handles = [path.open("rb") for path in sources]
        try:
            media = [
                InputMediaPhoto(media=handles[0], caption=caption),
                *(InputMediaPhoto(media=h) for h in handles[1:]),
            ]
            await bot.send_media_group(  # type: ignore[attr-defined]
                chat_id=chat_id_int, media=media
            )
        finally:
            for h in handles:
                h.close()

    # ---------------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------------

    def _require_bot(self) -> object:
        """Retorna el bot activo o lanza ``RuntimeError`` si no está disponible."""
        bot = self._get_telegram_bot()
        if bot is None:
            raise RuntimeError(
                "Telegram no está disponible: no hay un bot registrado en el sistema."
            )
        return bot

    @staticmethod
    def _parse_chat_id(chat_id: str) -> int:
        """Parsea el chat_id de string a entero."""
        try:
            return int(chat_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"chat_id debe ser un entero serializado: {chat_id!r}") from exc
