"""Puerto de envío de ficheros por Telegram.

Reemplaza el viejo ``IPhotoSender``: ahora unificamos photo / audio / video /
file / album bajo una sola interfaz.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from core.domain.value_objects.telegram_file import FileContentType


class IFileSender(ABC):
    """Envío de ficheros a un chat de Telegram identificado por ``chat_id``."""

    @abstractmethod
    async def send(
        self,
        *,
        chat_id: str,
        content_type: FileContentType,
        source: Path,
        caption: str | None = None,
    ) -> None:
        """Envía UN fichero al chat.

        ``source`` debe ser un Path local existente (las URLs públicas no se
        soportan en este flujo: el LLM trae el fichero al workspace antes).
        """

    @abstractmethod
    async def send_album(
        self,
        *,
        chat_id: str,
        sources: list[Path],
        caption: str | None = None,
    ) -> None:
        """Envía un grupo de fotos como álbum (Telegram ``send_media_group``).

        El ``caption`` se aplica a la primera foto del álbum (convención
        Telegram); las demás se envían sin caption. Si ``sources`` tiene una
        sola foto, se delega a :meth:`send` con ``content_type='photo'``.
        """
