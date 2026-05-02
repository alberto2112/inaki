"""Puerto de persistencia de :class:`TelegramFileRecord`.

Vive en una DB dedicada (``telegram_files.db``) — separada de ``history.db``
para no contaminar el historial de conversación con metadata de transporte.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from core.domain.value_objects.telegram_file import (
    DownloadableContentType,
    TelegramFileRecord,
)


class IFileRecordRepo(ABC):
    """Repo de registros de ficheros recibidos por Telegram."""

    @abstractmethod
    async def ensure_schema(self) -> None:
        """Crea tabla e índices si no existen. Llamar una vez al arranque."""

    @abstractmethod
    async def save(self, record: TelegramFileRecord) -> None:
        """Persiste un record.

        No deduplica por ``file_unique_id`` — si la misma foto llega dos veces
        con metadata distinta (ej: distinto caption), ambos quedan registrados.
        """

    @abstractmethod
    async def query_recent(
        self,
        *,
        agent_id: str,
        channel: str,
        chat_id: str,
        content_type: DownloadableContentType,
        count: int,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[TelegramFileRecord]:
        """Devuelve los ``count`` records más recientes que cumplen los filtros.

        Reglas:
        - ``content_type='album'`` filtra ``content_type='photo' AND
          media_group_id IS NOT NULL``; los registros se devuelven agrupados
          por ``media_group_id`` (todos los miembros del álbum más reciente
          primero, luego los del siguiente, hasta llenar ``count``).
        - ``content_type='photo'`` filtra ``media_group_id IS NULL`` para no
          contaminar con miembros de álbum.
        - Otros tipos: filtro directo por ``content_type``.
        - ``since`` y ``until`` deben ser timezone-aware (UTC). El repo
          asume UTC y compara contra ``received_at`` directamente.
        - Orden: ``received_at DESC``.
        """
