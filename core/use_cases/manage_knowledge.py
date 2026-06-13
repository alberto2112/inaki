"""
ManageKnowledgeUseCase — gestión del índice de knowledge (ingest/reindex/list/stats/delete).

Facade sobre las fuentes ``IIndexableKnowledgeSource`` registradas en el agente.
Es la ÚNICA pieza que orquesta operaciones de gestión: la tool ``knowledge_admin``
(LLM), el admin REST y el CLI son clientes finos de este use case — ninguno
re-implementa la lógica. Así un canal nuevo (Slack, etc.) no necesita código de
knowledge: el LLM ya llega a esta capacidad vía la tool.

Recibe la MISMA lista viva de fuentes que el ``KnowledgeOrchestrator`` (referencia
compartida): si una extensión añade una fuente indexable después del wiring, este
use case la ve sin reconstruirse. Las fuentes read-only (memoria, sqlite externa)
se filtran por tipo y quedan fuera de toda operación de gestión.
"""

from __future__ import annotations

from pathlib import Path

from core.domain.errors import KnowledgeError
from core.ports.outbound.knowledge_port import (
    IIndexableKnowledgeSource,
    IKnowledgeSource,
)


class ManageKnowledgeUseCase:
    """Operaciones de gestión sobre las fuentes de conocimiento indexables."""

    def __init__(self, sources: list[IKnowledgeSource]) -> None:
        # Referencia VIVA a la lista del container (la misma que usa el
        # orchestrator). NO se copia: las fuentes de extensiones añadidas
        # post-wiring quedan incluidas automáticamente.
        self._sources = sources

    def _indexables(self) -> dict[str, IIndexableKnowledgeSource]:
        """Fuentes gestionables, indexadas por ``source_id``."""
        return {
            fuente.source_id: fuente
            for fuente in self._sources
            if isinstance(fuente, IIndexableKnowledgeSource)
        }

    def _resolver(self, source_id: str | None) -> IIndexableKnowledgeSource:
        """Resuelve la fuente indexable objetivo.

        Sin ``source_id``: si hay exactamente una fuente indexable, la usa; si
        hay varias, exige desambiguar. Lanza ``KnowledgeError`` con un mensaje
        accionable en cualquier caso de error.
        """
        indexables = self._indexables()
        if not indexables:
            raise KnowledgeError(
                "No hay fuentes de conocimiento indexables configuradas. "
                "Agregá una source 'type: document' en ~/.inaki/config/global.yaml."
            )

        if source_id is None:
            if len(indexables) == 1:
                return next(iter(indexables.values()))
            raise KnowledgeError(
                "Hay varias fuentes indexables — especificá cuál. "
                f"Disponibles: {sorted(indexables)}."
            )

        fuente = indexables.get(source_id)
        if fuente is None:
            raise KnowledgeError(
                f"La fuente indexable '{source_id}' no existe. "
                f"Disponibles: {sorted(indexables)}."
            )
        return fuente

    def list_sources(self) -> list[dict[str, str]]:
        """Lista las fuentes indexables (id + descripción) para que el caller elija."""
        return [
            {"source_id": fuente.source_id, "description": fuente.description}
            for fuente in self._indexables().values()
        ]

    async def ingest(
        self,
        source_path: Path,
        source_id: str | None = None,
    ) -> dict[str, int | str]:
        """Incorpora un archivo a una fuente y lo indexa. Ver ``ingest_file`` del port."""
        fuente = self._resolver(source_id)
        result = await fuente.ingest_file(source_path)
        return {"source_id": fuente.source_id, **result}

    async def reindex(self, source_id: str | None = None) -> dict[str, int | str]:
        """Re-indexa una fuente completa (incremental por mtime)."""
        fuente = self._resolver(source_id)
        stats = await fuente.index()
        return {"source_id": fuente.source_id, **stats}

    async def list_documents(
        self,
        source_id: str | None = None,
    ) -> list[dict[str, int | str | float]]:
        """Lista los archivos indexados de una fuente."""
        fuente = self._resolver(source_id)
        return await fuente.list_files()

    async def stats(self, source_id: str | None = None) -> dict[str, int | str | float | None]:
        """Estadísticas del índice de una fuente."""
        fuente = self._resolver(source_id)
        return await fuente.get_stats()

    async def delete_document(
        self,
        file_path: str,
        source_id: str | None = None,
        *,
        remove_physical: bool = False,
    ) -> dict[str, int | str]:
        """Borra del índice los chunks de un archivo. Devuelve cuántos chunks se borraron."""
        fuente = self._resolver(source_id)
        borrados = await fuente.delete_file(file_path, remove_physical=remove_physical)
        if borrados == 0:
            raise KnowledgeError(
                f"No se encontró '{file_path}' en el índice de '{fuente.source_id}'."
            )
        return {"source_id": fuente.source_id, "file_path": file_path, "chunks_borrados": borrados}
