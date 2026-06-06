"""Puerto de envío de mensajes de texto a un chat de Telegram.

Complementa a :class:`IFileSender` (ficheros) con el envío de texto plano a un
``chat_id`` arbitrario. Pensado para que el LLM pueda mandar un mensaje a otro
chat distinto al del turno actual (lo provee explícitamente).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class IMessageSender(ABC):
    """Envío de texto a un chat de Telegram identificado por ``chat_id``."""

    @abstractmethod
    async def send_message(self, *, chat_id: str, text: str) -> None:
        """Envía un mensaje de texto al chat.

        ``chat_id`` se recibe como string (serialización del entero de Telegram)
        y el adapter lo parsea internamente. ``text`` no puede ser vacío.
        """
