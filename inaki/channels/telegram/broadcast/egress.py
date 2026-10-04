"""Emisión de eventos al broadcast — UNA política, aplicada en los dos puntos de egress.

Todo lo que sale de Telegram hacia un grupo puede tener que replicarse al LAN
(otros bots del mismo grupo no lo ven de otra forma). La decisión de emitir vive
acá y solo acá: la consultan el egress del canal (``TelegramChannelOutbound``:
scheduler, tools, ``/admin/send``, resultados ``bg-N``) y la respuesta
conversacional del bot (``TurnRunner``: turno con update y flush de grupos), que no pasa por el
outbound porque responde citando el mensaje del usuario.

Antes la misma decisión estaba copiada en el bot y en ``/admin/send``, y ausente
en el scheduler: un ``channel_send`` a un grupo era invisible para los otros bots.
"""

from __future__ import annotations

import logging
import time

from inaki.channels.telegram.broadcast.port import BroadcastEmitter, BroadcastMessage, EventType
from inaki.channels.telegram.ports import TelegramEmitFlags

logger = logging.getLogger(__name__)


def es_chat_de_grupo(chat_id: str) -> bool:
    """En Telegram los grupos y supergrupos tienen ``chat_id`` negativo."""
    return chat_id.startswith("-")


class BroadcastEgress:
    """Emite un evento al LAN si hay transporte y el flag ``emit.<event_type>`` lo permite.

    Best-effort por contrato: jamás propaga — un LAN caído no rompe un envío a Telegram.
    """

    def __init__(
        self,
        emitter: BroadcastEmitter | None,
        agent_id: str,
        flags: TelegramEmitFlags,
    ) -> None:
        self._emitter = emitter
        self._agent_id = agent_id
        self._flags = flags
        # Rótulo del propio bot en Telegram (``@username (Nombre)``). Lo fija el
        # canal al arrancar, tras ``get_me()``; vacío si no se pudo resolver, y
        # entonces los receptores caen al ``agent_id``.
        self._identidad = ""

    def fijar_identidad(self, rotulo: str) -> None:
        """Fija el rótulo con el que este bot firma sus ``assistant_response``."""
        self._identidad = rotulo

    @property
    def activo(self) -> bool:
        return self._emitter is not None

    def permite(self, event_type: EventType) -> bool:
        return bool(getattr(self._flags, event_type, False))

    async def emit(
        self,
        *,
        event_type: EventType,
        chat_id: str,
        content: str,
        sender: str = "",
    ) -> None:
        """Construye y emite el ``BroadcastMessage``; silencioso si no aplica o falla.

        En ``assistant_response`` el emisor es el propio bot: si el caller no pasa
        ``sender``, viaja su identidad de Telegram para que los demás agentes lo
        rotulen igual que a un humano (``@username (Nombre) said: ...``).
        """
        if self._emitter is None or not self.permite(event_type) or not content.strip():
            return
        if event_type == "assistant_response" and not sender:
            sender = self._identidad
        msg = BroadcastMessage(
            timestamp=time.time(),
            agent_id=self._agent_id,
            chat_id=chat_id,
            event_type=event_type,
            content=content,
            sender=sender,
        )
        try:
            await self._emitter.emit(msg)
        except Exception as exc:  # noqa: BLE001 — el LAN nunca rompe el envío principal
            logger.warning(
                "Fallo al emitir broadcast event_type=%s (agent=%s, chat_id=%s): %s",
                event_type,
                self._agent_id,
                chat_id,
                exc,
            )
