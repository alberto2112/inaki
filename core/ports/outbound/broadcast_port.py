"""Puertos de broadcast entre instancias de Iñaki en LAN."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class BroadcastMessage:
    """Mensaje emitido por un agente al canal de broadcast.

    Todos los campos son inmutables. ``chat_id`` es siempre ``str`` para
    mantener consistencia con la tipificación de ``TelegramChannelConfig``
    y los filtros de historial (nunca ``int``).
    """

    timestamp: float
    """Epoch UTC (segundos con decimales). Usado para verificar frescura HMAC."""

    agent_id: str
    """Identificador del agente emisor. Usado para el filtro anti-loop."""

    chat_id: str
    """Identificador del chat de origen (ej: ``"-100123"`` para grupos Telegram)."""

    message: str
    """Texto plano de la respuesta del asistente. Tool calls excluidos."""


# Alias de tipo para callbacks de ingress. Recibe un BroadcastMessage y
# retorna un Awaitable — la implementación típica alimenta el BroadcastBuffer.
BroadcastCallback = Callable[[BroadcastMessage], Awaitable[None]]


class BroadcastEmitter(Protocol):
    """Interfaz de egress para emitir mensajes al canal de broadcast.

    La implementación concreta (``TcpBroadcastAdapter``) firma cada mensaje
    con HMAC-SHA256 y lo serializa como JSON line-delimited.
    """

    async def emit(self, msg: BroadcastMessage) -> None:
        """Emite un mensaje al canal de broadcast.

        La emisión es fire-and-forget: si no hay conexión activa, el mensaje
        se descarta silenciosamente. Las excepciones deben ser capturadas por
        el llamador.

        Args:
            msg: Mensaje a emitir.
        """
        ...


class BroadcastReceiver(Protocol):
    """Interfaz de ingress para recibir mensajes del canal de broadcast.

    El receptor mantiene un buffer efímero segmentado por ``chat_id`` y
    provee acceso sincrónico para inyectar contexto antes de cada turno LLM.
    """

    async def subscribe(self, callback: BroadcastCallback) -> None:
        """Registra un callback invocado por cada mensaje broadcast válido recibido.

        El callback es llamado SOLO para mensajes de otros agentes (anti-loop
        por ``agent_id`` implementado en el adapter). El callback alimenta
        normalmente un ``BroadcastBuffer``.

        Args:
            callback: Función asíncrona que recibe el ``BroadcastMessage`` validado.
        """
        ...

    def recent(self, chat_id: str) -> list[BroadcastMessage]:
        """Retorna los mensajes recientes del buffer para el chat dado.

        Los mensajes expirados (TTL) se filtran antes de retornar. Si no hay
        mensajes válidos para el ``chat_id``, retorna lista vacía.

        Args:
            chat_id: Identificador del chat a consultar.

        Returns:
            Lista de mensajes en orden cronológico (más antiguo primero).
        """
        ...

    def render(self, chat_id: str) -> str | None:
        """Renderiza el contexto de broadcast para el chat dado como texto markdown.

        Args:
            chat_id: Identificador del chat a renderizar.

        Returns:
            Texto markdown con el contexto, o ``None`` si no hay mensajes.
        """
        ...
