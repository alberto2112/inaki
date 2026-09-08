"""Puerto de persistencia de :class:`TelegramFileRecord`.

Vive en una DB dedicada (``telegram_files.db``) — separada de ``history.db``
para no contaminar el historial de conversación con metadata de transporte.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from inaki.channels.telegram.files.model import (
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

    @abstractmethod
    async def query_by_media_group(
        self,
        *,
        agent_id: str,
        channel: str,
        chat_id: str,
        media_group_id: str,
    ) -> list[TelegramFileRecord]:
        """Devuelve TODOS los miembros de un ``media_group_id``, cualquier tipo.

        A diferencia de ``query_recent(content_type='album')`` (solo fotos),
        este método no filtra por tipo: Telegram agrupa también documentos y
        videos enviados juntos bajo un mismo ``media_group_id``. Orden:
        ``received_at ASC`` (orden de recepción, el que espera el usuario).
        """


class IFileDownloader(ABC):
    """Descarga de ficheros usando el ``file_id`` que conserva Telegram."""

    @abstractmethod
    async def download(self, *, file_id: str, dest: Path) -> None:
        """Descarga el fichero correspondiente a ``file_id`` a ``dest``.

        ``dest`` debe ser un Path absoluto a un fichero (NO directorio). El
        adapter crea los directorios padre si no existen. Si ``dest`` ya
        existe, el caller decide qué hacer (la tool cachea por
        ``file_unique_id`` y evita re-descargar).
        """
