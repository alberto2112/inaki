"""
Tests unitarios para ManageKnowledgeUseCase.

Verifica:
- Filtrado: solo las fuentes IIndexableKnowledgeSource son gestionables.
- Resolución de source_id: única implícita, ambigua exige id, id inexistente falla.
- Delegación correcta a ingest/reindex/list/stats/delete.
- delete que no borra nada → KnowledgeError.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.domain.errors import KnowledgeError
from core.domain.value_objects.knowledge_chunk import KnowledgeChunk
from core.ports.outbound.knowledge_port import (
    IIndexableKnowledgeSource,
    IKnowledgeSource,
)
from core.use_cases.manage_knowledge import ManageKnowledgeUseCase


class _FakeReadOnlySource(IKnowledgeSource):
    """Fuente read-only (como memoria) — NO debe ser gestionable."""

    def __init__(self, source_id: str = "memory") -> None:
        self._id = source_id

    @property
    def source_id(self) -> str:
        return self._id

    @property
    def description(self) -> str:
        return "read-only"

    async def search(self, query_vec, top_k, min_score) -> list[KnowledgeChunk]:
        return []


class _FakeIndexableSource(IIndexableKnowledgeSource):
    """Fuente indexable espía: registra llamadas y devuelve valores fijos."""

    def __init__(self, source_id: str = "docs") -> None:
        self._id = source_id
        self.ingested: list[Path] = []
        self.reindexed = 0
        self.deleted: list[tuple[str, bool]] = []
        self.delete_return = 3

    @property
    def source_id(self) -> str:
        return self._id

    @property
    def description(self) -> str:
        return "indexable"

    async def search(self, query_vec, top_k, min_score) -> list[KnowledgeChunk]:
        return []

    async def index(self) -> dict[str, int]:
        self.reindexed += 1
        return {"archivos_procesados": 1, "archivos_saltados": 0, "chunks_nuevos": 5}

    async def ingest_file(self, source_path: Path) -> dict[str, int | str]:
        self.ingested.append(source_path)
        return {"stored_path": f"/docs/{Path(source_path).name}", "chunks_nuevos": 7}

    async def get_stats(self) -> dict[str, int | str | float | None]:
        return {
            "source_id": self._id,
            "archivos_indexados": 2,
            "chunks_totales": 10,
            "db_path": "/x.db",
            "last_indexed_mtime": None,
            "embedding_dimension": 384,
        }

    async def list_files(self) -> list[dict[str, int | str | float]]:
        return [{"file_path": "/docs/a.md", "mtime": 1.0, "chunk_count": 4}]

    async def delete_file(self, file_path: str, *, remove_physical: bool = False) -> int:
        self.deleted.append((file_path, remove_physical))
        return self.delete_return


class TestResolucion:
    async def test_sin_fuentes_indexables_falla(self) -> None:
        uc = ManageKnowledgeUseCase(sources=[_FakeReadOnlySource()])
        with pytest.raises(KnowledgeError, match="indexables configuradas"):
            await uc.reindex()

    async def test_unica_fuente_resuelve_implicita(self) -> None:
        idx = _FakeIndexableSource("docs")
        uc = ManageKnowledgeUseCase(sources=[_FakeReadOnlySource(), idx])
        stats = await uc.reindex()  # sin source_id
        assert stats["source_id"] == "docs"
        assert idx.reindexed == 1

    async def test_varias_fuentes_exige_id(self) -> None:
        uc = ManageKnowledgeUseCase(
            sources=[_FakeIndexableSource("a"), _FakeIndexableSource("b")]
        )
        with pytest.raises(KnowledgeError, match="especificá cuál"):
            await uc.reindex()

    async def test_id_inexistente_falla(self) -> None:
        uc = ManageKnowledgeUseCase(sources=[_FakeIndexableSource("docs")])
        with pytest.raises(KnowledgeError, match="no existe"):
            await uc.reindex(source_id="nope")

    def test_list_sources_solo_indexables(self) -> None:
        uc = ManageKnowledgeUseCase(
            sources=[_FakeReadOnlySource("memory"), _FakeIndexableSource("docs")]
        )
        fuentes = uc.list_sources()
        assert [f["source_id"] for f in fuentes] == ["docs"]


class TestOperaciones:
    async def test_ingest_delega_y_anota_source(self, tmp_path: Path) -> None:
        idx = _FakeIndexableSource("docs")
        uc = ManageKnowledgeUseCase(sources=[idx])
        archivo = tmp_path / "x.txt"
        result = await uc.ingest(archivo)
        assert result["source_id"] == "docs"
        assert result["chunks_nuevos"] == 7
        assert idx.ingested == [archivo]

    async def test_list_documents(self) -> None:
        uc = ManageKnowledgeUseCase(sources=[_FakeIndexableSource("docs")])
        files = await uc.list_documents()
        assert files[0]["file_path"] == "/docs/a.md"

    async def test_stats(self) -> None:
        uc = ManageKnowledgeUseCase(sources=[_FakeIndexableSource("docs")])
        stats = await uc.stats()
        assert stats["chunks_totales"] == 10

    async def test_delete_ok(self) -> None:
        idx = _FakeIndexableSource("docs")
        uc = ManageKnowledgeUseCase(sources=[idx])
        result = await uc.delete_document("a.md", remove_physical=True)
        assert result["chunks_borrados"] == 3
        assert idx.deleted == [("a.md", True)]

    async def test_delete_sin_match_falla(self) -> None:
        idx = _FakeIndexableSource("docs")
        idx.delete_return = 0
        uc = ManageKnowledgeUseCase(sources=[idx])
        with pytest.raises(KnowledgeError, match="No se encontró"):
            await uc.delete_document("fantasma.md")
