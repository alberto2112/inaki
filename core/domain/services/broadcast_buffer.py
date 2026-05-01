"""
BroadcastBuffer — buffer efímero de contexto de grupo por chat_id.

Mantiene los últimos N mensajes recibidos vía broadcast, segmentados por
``chat_id``, con TTL configurable. No persiste en DB ni en memoria semántica:
si el proceso se reinicia, el buffer comienza vacío.

Asunción de threading: el buffer corre en un único event loop de asyncio.
No se usan locks porque todas las operaciones son sincrónicas (sin await)
y Python tiene GIL para las estructuras de datos nativas.
"""

from __future__ import annotations

import time as _time_module
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.ports.outbound.broadcast_port import BroadcastMessage

# Re-import real para runtime (evita ciclos a la vez que mantiene type hints)
from core.ports.outbound.broadcast_port import BroadcastMessage


class BroadcastBuffer:
    """Buffer FIFO por ``chat_id`` con TTL y límite de capacidad.

    Parámetros ajustables en el constructor para facilitar pruebas
    deterministas con tiempo congelado.
    """

    def __init__(
        self,
        ttl: float = 300.0,
        max_size: int = 50,
        _now: Callable[[], float] = _time_module.time,
    ) -> None:
        """Inicializa el buffer.

        Args:
            ttl: Tiempo de vida en segundos de cada mensaje. Por defecto 300s
                (5 minutos, ventana conversacional típica).
            max_size: Cantidad máxima de mensajes por ``chat_id``. Cuando se
                supera, se descarta el mensaje más antiguo (pop-left).
            _now: Fuente de tiempo inyectable. Por defecto ``time.time``;
                usar un callable que retorne un valor fijo en tests.
        """
        self._ttl = ttl
        self._max_size = max_size
        self._now = _now
        self._buckets: dict[str, deque[BroadcastMessage]] = {}

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def append(self, msg: BroadcastMessage) -> None:
        """Encola un mensaje en el bucket de su ``chat_id``.

        Después de encolar, poda los mensajes expirados y, si el bucket
        supera la capacidad, descarta el mensaje más antiguo.

        Args:
            msg: Mensaje de broadcast a almacenar.
        """
        bucket = self._buckets.setdefault(msg.chat_id, deque())
        bucket.append(msg)
        self._prune(bucket)
        while len(bucket) > self._max_size:
            bucket.popleft()

    def recent(self, chat_id: str) -> list[BroadcastMessage]:
        """Retorna los mensajes vigentes del bucket indicado.

        Poda los expirados antes de retornar. Si no hay mensajes o el
        ``chat_id`` no existe, retorna lista vacía.

        Args:
            chat_id: Identificador del chat a consultar.

        Returns:
            Lista de mensajes en orden cronológico (más antiguo primero).
        """
        bucket = self._buckets.get(chat_id)
        if not bucket:
            return []
        self._prune(bucket)
        return list(bucket)

    def render(self, chat_id: str) -> str | None:
        """Genera una sección markdown con el contexto de grupo.

        El formato de cada línea depende del ``event_type`` del mensaje:

        - ``assistant_response``: ``"- [HH:MM:SS] {agent_id}: {content}"``
        - ``user_input_voice``:   ``"- [HH:MM:SS] {sender} (audio): {content}"``
        - ``user_input_photo``:   ``"- [HH:MM:SS] {sender} (foto): {content}"``

        Retorna ``None`` si el buffer está vacío o todos los mensajes
        expiraron — el llamador puede omitir la inyección en el prompt.

        Args:
            chat_id: Identificador del chat a renderizar.

        Returns:
            Sección markdown o ``None`` si no hay mensajes vigentes.
        """
        mensajes = self.recent(chat_id)
        if not mensajes:
            return None

        lineas = ["## Contexto del grupo (otros agentes)"]
        for m in mensajes:
            hora = datetime.fromtimestamp(m.timestamp, tz=timezone.utc).strftime("%H:%M:%S")
            lineas.append(_formatear_linea(m, hora))

        return "\n".join(lineas)

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _prune(self, bucket: deque[BroadcastMessage]) -> None:
        """Elimina del frente del bucket los mensajes cuyo timestamp expiró."""
        now = self._now()
        umbral = now - self._ttl
        while bucket and bucket[0].timestamp < umbral:
            bucket.popleft()


def _formatear_linea(m: BroadcastMessage, hora: str) -> str:
    """Formatea una línea del buffer según el ``event_type``.

    Pure function — facilita testing aislado y mantiene ``render()`` legible.
    """
    if m.event_type == "user_input_voice":
        return f"- [{hora}] {m.sender} (audio): {m.content}"
    if m.event_type == "user_input_photo":
        return f"- [{hora}] {m.sender} (foto): {m.content}"
    # assistant_response (default y backward-compat)
    return f"- [{hora}] {m.agent_id}: {m.content}"
