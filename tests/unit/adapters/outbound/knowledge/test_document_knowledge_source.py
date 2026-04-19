"""
Tests unitarios para DocumentKnowledgeSource.

Verifica:
- index() → search() round-trip: chunks indexados son recuperables por similitud
- Indexación incremental: segunda llamada sin cambios de mtime = cero re-embeddings
- Carpeta inexistente: index() retorna stats vacías sin explotar
- search() en DB vacía: retorna lista vacía
- min_score filtra correctamente
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from adapters.outbound.knowledge.document_knowledge_source import DocumentKnowledgeSource


def _make_vec(val: float, dim: int = 384) -> list[float]:
    """Genera un vector L2-normalizado sintético de dimensión `dim`."""
    vec = [0.0] * dim
    vec[0] = val
    # normalizar
    norma = (sum(x**2 for x in vec)) ** 0.5 or 1.0
    return [x / norma for x in vec]


def _make_embedder(vector: list[float]) -> MagicMock:
    """Mock de IEmbeddingProvider que siempre retorna `vector`."""
    embedder = MagicMock()
    embedder.embed_query = AsyncMock(return_value=vector)
    return embedder


class TestDocumentKnowledgeSourceIndex:
    async def test_index_archivos_nuevos(self, tmp_path: Path) -> None:
        """Los archivos nuevos deben ser indexados y generar chunks."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "doc1.md").write_text("# Título\n" + " ".join([f"p{i}" for i in range(30)]))
        (docs_dir / "doc2.md").write_text(" ".join([f"q{i}" for i in range(30)]))

        db_dir = tmp_path / "knowledge"
        vec = _make_vec(1.0)
        embedder = _make_embedder(vec)

        source = DocumentKnowledgeSource(
            source_id="test-src",
            description="Test source",
            path=str(docs_dir),
            embedder=embedder,
            glob="**/*.md",
            chunk_size=20,
            chunk_overlap=5,
        )
        # Override db_path to use tmp_path
        source._db_path = str(db_dir / "test-src.db")
        db_dir.mkdir(parents=True, exist_ok=True)

        stats = await source.index()

        assert stats["archivos_procesados"] == 2
        assert stats["archivos_saltados"] == 0
        assert stats["chunks_nuevos"] > 0
        # El embedder debe haber sido llamado al menos una vez
        assert embedder.embed_query.call_count >= 1

    async def test_index_incremental_sin_cambios(self, tmp_path: Path) -> None:
        """Segunda llamada a index() con mtime sin cambio → cero re-embeddings."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "doc.md").write_text("contenido de prueba " * 20)

        db_dir = tmp_path / "knowledge"
        db_dir.mkdir(parents=True, exist_ok=True)

        vec = _make_vec(0.9)
        embedder = _make_embedder(vec)

        source = DocumentKnowledgeSource(
            source_id="incr-src",
            description="Incremental test",
            path=str(docs_dir),
            embedder=embedder,
            glob="**/*.md",
            chunk_size=10,
            chunk_overlap=2,
        )
        source._db_path = str(db_dir / "incr-src.db")

        # Primera indexación
        stats1 = await source.index()
        call_count_1 = embedder.embed_query.call_count

        assert stats1["archivos_procesados"] == 1
        assert stats1["chunks_nuevos"] > 0

        # Segunda indexación sin cambios
        stats2 = await source.index()

        assert stats2["archivos_procesados"] == 0
        assert stats2["archivos_saltados"] == 1
        # No se llamó al embedder de nuevo
        assert embedder.embed_query.call_count == call_count_1

    async def test_index_reindexado_al_cambiar_mtime(self, tmp_path: Path) -> None:
        """Si el mtime cambia, el archivo debe re-indexarse."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        doc = docs_dir / "doc.md"
        doc.write_text("versión original " * 20)

        db_dir = tmp_path / "knowledge"
        db_dir.mkdir(parents=True, exist_ok=True)

        vec = _make_vec(0.8)
        embedder = _make_embedder(vec)

        source = DocumentKnowledgeSource(
            source_id="mtime-src",
            description="Mtime test",
            path=str(docs_dir),
            embedder=embedder,
            glob="**/*.md",
            chunk_size=10,
            chunk_overlap=2,
        )
        source._db_path = str(db_dir / "mtime-src.db")

        # Primera indexación
        await source.index()
        calls_1 = embedder.embed_query.call_count

        # Modificar el archivo — tocar mtime
        import time

        time.sleep(0.01)  # pequeño delay para que mtime sea diferente
        doc.write_text("versión nueva " * 20)

        # Segunda indexación → debe re-indexar
        stats2 = await source.index()
        assert stats2["archivos_procesados"] == 1
        # Nuevas llamadas al embedder
        assert embedder.embed_query.call_count > calls_1

    async def test_index_carpeta_inexistente(self, tmp_path: Path) -> None:
        """Si la carpeta no existe, index() retorna stats vacías sin explotar."""
        db_dir = tmp_path / "knowledge"
        db_dir.mkdir(parents=True, exist_ok=True)

        embedder = _make_embedder(_make_vec(0.5))

        source = DocumentKnowledgeSource(
            source_id="no-dir",
            description="No dir",
            path=str(tmp_path / "inexistente"),
            embedder=embedder,
            glob="**/*.md",
        )
        source._db_path = str(db_dir / "no-dir.db")

        stats = await source.index()
        assert stats["archivos_procesados"] == 0
        assert stats["chunks_nuevos"] == 0


class TestDocumentKnowledgeSourceSearch:
    async def test_search_en_db_vacia(self, tmp_path: Path) -> None:
        """search() en una DB sin chunks retorna lista vacía."""
        db_dir = tmp_path / "knowledge"
        db_dir.mkdir(parents=True, exist_ok=True)

        vec = _make_vec(1.0)
        embedder = _make_embedder(vec)

        source = DocumentKnowledgeSource(
            source_id="empty-src",
            description="Empty",
            path=str(tmp_path / "docs"),
            embedder=embedder,
        )
        source._db_path = str(db_dir / "empty-src.db")

        resultados = await source.search(vec, top_k=5, min_score=0.0)
        assert resultados == []

    async def test_search_round_trip(self, tmp_path: Path) -> None:
        """Tras indexar, search() debe retornar chunks con score."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "doc.md").write_text("contenido relevante " * 30)

        db_dir = tmp_path / "knowledge"
        db_dir.mkdir(parents=True, exist_ok=True)

        # El embedder retorna siempre el mismo vector → similitud coseno = 1
        vec = _make_vec(1.0)
        embedder = _make_embedder(vec)

        source = DocumentKnowledgeSource(
            source_id="rt-src",
            description="Round trip",
            path=str(docs_dir),
            embedder=embedder,
            glob="**/*.md",
            chunk_size=10,
            chunk_overlap=2,
        )
        source._db_path = str(db_dir / "rt-src.db")

        await source.index()
        resultados = await source.search(vec, top_k=5, min_score=0.0)

        assert len(resultados) > 0
        assert all(r.source_id == "rt-src" for r in resultados)
        assert all(isinstance(r.content, str) for r in resultados)
        # Score entre -1 y 1 (coseno de vectores normalizados ≈ 1 para vectores idénticos)
        assert all(-1.0 <= r.score <= 1.0 for r in resultados)

    async def test_search_min_score_filtra(self, tmp_path: Path) -> None:
        """min_score alto debe filtrar todos los resultados cuando el score es bajo."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "doc.md").write_text("texto " * 30)

        db_dir = tmp_path / "knowledge"
        db_dir.mkdir(parents=True, exist_ok=True)

        # Embedder retorna vectores opuestos → score coseno muy bajo
        vec_index = _make_vec(1.0)
        vec_query = _make_vec(-1.0)

        embedder_index = MagicMock()
        embedder_index.embed_query = AsyncMock(return_value=vec_index)

        source = DocumentKnowledgeSource(
            source_id="filter-src",
            description="Filter test",
            path=str(docs_dir),
            embedder=embedder_index,
            glob="**/*.md",
            chunk_size=10,
            chunk_overlap=2,
        )
        source._db_path = str(db_dir / "filter-src.db")

        await source.index()
        resultados = await source.search(vec_query, top_k=5, min_score=0.9)
        # Con vectores opuestos, el score ≈ -1 → todos filtrados con min_score=0.9
        assert resultados == []

    async def test_get_stats_devuelve_estadisticas(self, tmp_path: Path) -> None:
        """get_stats() retorna conteos correctos después de indexar."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "a.md").write_text("texto a " * 30)
        (docs_dir / "b.md").write_text("texto b " * 30)

        db_dir = tmp_path / "knowledge"
        db_dir.mkdir(parents=True, exist_ok=True)

        vec = _make_vec(1.0)
        embedder = _make_embedder(vec)

        source = DocumentKnowledgeSource(
            source_id="stats-src",
            description="Stats test",
            path=str(docs_dir),
            embedder=embedder,
            glob="**/*.md",
            chunk_size=10,
            chunk_overlap=2,
        )
        source._db_path = str(db_dir / "stats-src.db")

        await source.index()
        stats = await source.get_stats()

        assert stats["source_id"] == "stats-src"
        assert stats["archivos_indexados"] == 2
        assert stats["chunks_totales"] > 0
