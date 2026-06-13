"""
Puerto de salida para fuentes de conocimiento.

Las implementaciones concretas viven en adapters/outbound/knowledge/.
``IKnowledgeSource`` es de solo lectura — NO hereda de IMemoryRepository (que es
lectura+escritura) para respetar el principio de sustitución de Liskov.

Las fuentes que ADEMÁS de buscarse se pueden indexar y gestionar implementan
``IIndexableKnowledgeSource``. Mantener la indexación en un sub-port separado
preserva Liskov: las fuentes read-only (memoria, sqlite externa) siguen
implementando solo ``IKnowledgeSource`` sin verse forzadas a un ``index()`` que
no tiene sentido para ellas.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from core.domain.value_objects.knowledge_chunk import KnowledgeChunk


class IKnowledgeSource(ABC):
    """Interfaz de una fuente de conocimiento consultable por vector de embedding."""

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Identificador único de esta fuente (ej. 'memory', 'docs-proyecto')."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Descripción breve de qué contiene esta fuente. Usada en logs y debug."""
        ...

    @abstractmethod
    async def search(
        self,
        query_vec: list[float],
        top_k: int,
        min_score: float,
    ) -> list[KnowledgeChunk]:
        """
        Busca los fragmentos más relevantes para el vector de consulta.

        Args:
            query_vec: Vector de embedding de la consulta (dimensión 384).
            top_k: Número máximo de fragmentos a retornar.
            min_score: Score mínimo de similitud coseno (inclusive). Los fragmentos
                con score < min_score se descartan. Rango típico: [0.0, 1.0].

        Returns:
            Lista de KnowledgeChunk ordenada por score descendente.
            Puede retornar menos de top_k si no hay fragmentos suficientes.
        """
        ...


class IIndexableKnowledgeSource(IKnowledgeSource):
    """Fuente de conocimiento gestionable: además de buscarse, se puede indexar.

    Contrato extendido para fuentes basadas en documentos (filesystem). Las
    operaciones de gestión (ingest/reindex/list/stats/delete) las consume
    ``ManageKnowledgeUseCase``; el LLM y los canales llegan a ellas a través de
    una tool, NO implementando nada propio.
    """

    @abstractmethod
    async def index(self) -> dict[str, int]:
        """(Re)indexa la fuente completa de forma incremental.

        Solo re-embebe los archivos cuya mtime cambió desde la última pasada.

        Returns:
            {"archivos_procesados", "archivos_saltados", "chunks_nuevos"}.
        """
        ...

    @abstractmethod
    async def ingest_file(self, source_path: Path) -> dict[str, int | str]:
        """Incorpora un archivo externo a la fuente y lo indexa (modelo inbox).

        El archivo se copia dentro del almacenamiento de la fuente y se indexa
        de inmediato, independientemente del ``glob`` configurado (un ``.txt``
        entra aunque el glob sea ``**/*.md``).

        Args:
            source_path: Ruta a un archivo existente a incorporar.

        Returns:
            Estadísticas de indexación más ``"stored_path"`` (ruta final del
            archivo dentro de la fuente).

        Raises:
            FileNotFoundError: Si ``source_path`` no existe o no es un archivo.
        """
        ...

    @abstractmethod
    async def get_stats(self) -> dict[str, int | str | float | None]:
        """Estadísticas del índice: archivos, chunks, última indexación, dimensión."""
        ...

    @abstractmethod
    async def list_files(self) -> list[dict[str, int | str | float]]:
        """Lista los archivos indexados con su ``file_path``, ``mtime`` y ``chunk_count``."""
        ...

    @abstractmethod
    async def delete_file(self, file_path: str, *, remove_physical: bool = False) -> int:
        """Elimina del índice todos los chunks de un archivo.

        Args:
            file_path: Ruta del archivo tal como figura en el índice.
            remove_physical: Si True, además borra el archivo físico SI vive
                dentro del almacenamiento de la fuente (defensa: nunca borra
                archivos fuera de su carpeta).

        Returns:
            Número de chunks eliminados.
        """
        ...
