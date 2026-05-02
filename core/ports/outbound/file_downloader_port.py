"""Puerto de descarga de ficheros desde Telegram por ``file_id``."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


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
