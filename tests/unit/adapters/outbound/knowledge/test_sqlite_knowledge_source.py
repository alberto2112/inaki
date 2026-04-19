"""
Tests unitarios para SqliteKnowledgeSource.

Verifica:
- DB válida (384 dim) conecta y realiza búsqueda round-trip.
- DB con 512 dimensiones lanza KnowledgeConfigError.
- DB sin tabla `chunks` lanza KnowledgeConfigError.
- DB sin tabla `chunk_embeddings` lanza KnowledgeConfigError.
- min_score clamp descarta resultados de baja similitud.
- source_id se propaga a los KnowledgeChunks retornados.
"""

from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path

import pytest
import sqlite_vec

from adapters.outbound.knowledge.sqlite_knowledge_source import (
    EXPECTED_EMBEDDING_DIM,
    SqliteKnowledgeSource,
)
from core.domain.errors import KnowledgeConfigError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vec(val: float, dim: int = EXPECTED_EMBEDDING_DIM) -> list[float]:
    """Genera un vector L2-normalizado de dimensión `dim`."""
    vec = [0.0] * dim
    vec[0] = val
    norma = (sum(x**2 for x in vec)) ** 0.5 or 1.0
    return [x / norma for x in vec]


def _create_valid_db(db_path: Path, dim: int = EXPECTED_EMBEDDING_DIM) -> None:
    """Crea una DB SQLite válida con el schema esperado por SqliteKnowledgeSource."""
    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    conn.load_extension(sqlite_vec.loadable_path())
    conn.enable_load_extension(False)

    conn.execute("""
        CREATE TABLE chunks (
            id            INTEGER PRIMARY KEY,
            source_path   TEXT NOT NULL,
            content       TEXT NOT NULL,
            metadata_json TEXT DEFAULT '{}'
        )
    """)
    conn.execute(f"CREATE VIRTUAL TABLE chunk_embeddings USING vec0(embedding FLOAT[{dim}])")
    conn.commit()
    conn.close()


def _insert_chunk(
    db_path: Path,
    content: str,
    vector: list[float],
    source_path: str = "/doc.md",
    metadata: dict | None = None,
) -> int:
    """Inserta un chunk con su embedding en la DB. Retorna el id insertado."""
    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    conn.load_extension(sqlite_vec.loadable_path())
    conn.enable_load_extension(False)

    meta_json = json.dumps(metadata or {})
    cursor = conn.execute(
        "INSERT INTO chunks (source_path, content, metadata_json) VALUES (?, ?, ?)",
        (source_path, content, meta_json),
    )
    row_id = cursor.lastrowid
    vec_bytes = struct.pack(f"{len(vector)}f", *vector)
    conn.execute(
        "INSERT INTO chunk_embeddings (rowid, embedding) VALUES (?, ?)",
        (row_id, vec_bytes),
    )
    conn.commit()
    conn.close()
    return row_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSqliteKnowledgeSourceValid:
    async def test_round_trip_384_dim(self, tmp_path: Path) -> None:
        """DB válida con 384 dimensiones: conecta y retorna chunks con score."""
        db_path = tmp_path / "valid.db"
        _create_valid_db(db_path, dim=384)

        vec = _make_vec(1.0)
        _insert_chunk(db_path, "contenido de prueba", vec, source_path="/ruta/doc.md")

        source = SqliteKnowledgeSource(
            source_id="test-sqlite",
            description="Test source",
            db_path=str(db_path),
        )

        resultados = await source.search(vec, top_k=5, min_score=0.0)

        assert len(resultados) == 1
        assert resultados[0].content == "contenido de prueba"
        assert resultados[0].source_id == "test-sqlite"
        assert -1.0 <= resultados[0].score <= 1.0
        assert resultados[0].metadata["source_path"] == "/ruta/doc.md"

    async def test_source_id_propagates(self, tmp_path: Path) -> None:
        """source_id debe aparecer en todos los KnowledgeChunks retornados."""
        db_path = tmp_path / "source_id.db"
        _create_valid_db(db_path)

        vec = _make_vec(0.8)
        _insert_chunk(db_path, "chunk uno", vec)
        _insert_chunk(db_path, "chunk dos", vec)

        source = SqliteKnowledgeSource(
            source_id="mi-fuente",
            description="Fuente test",
            db_path=str(db_path),
        )

        resultados = await source.search(vec, top_k=10, min_score=0.0)

        assert len(resultados) == 2
        assert all(r.source_id == "mi-fuente" for r in resultados)

    async def test_min_score_clamp(self, tmp_path: Path) -> None:
        """Resultados con score < min_score deben ser descartados."""
        db_path = tmp_path / "minscore.db"
        _create_valid_db(db_path)

        # Vectores opuestos → score ≈ -1 (muy baja similitud)
        vec_indexado = _make_vec(1.0)
        vec_query = _make_vec(-1.0)
        _insert_chunk(db_path, "contenido opuesto", vec_indexado)

        source = SqliteKnowledgeSource(
            source_id="minscore-src",
            description="Min score test",
            db_path=str(db_path),
        )

        # Con min_score=0.9, los resultados de baja similitud se descartan
        resultados = await source.search(vec_query, top_k=5, min_score=0.9)
        assert resultados == []

    async def test_empty_query_vec_returns_empty(self, tmp_path: Path) -> None:
        """Un query_vec vacío retorna lista vacía sin explotar."""
        db_path = tmp_path / "empty_q.db"
        _create_valid_db(db_path)

        source = SqliteKnowledgeSource(
            source_id="empty-q",
            description="Test",
            db_path=str(db_path),
        )

        resultados = await source.search([], top_k=5, min_score=0.0)
        assert resultados == []

    async def test_metadata_json_propagates(self, tmp_path: Path) -> None:
        """Los campos de metadata_json deben aparecer en KnowledgeChunk.metadata."""
        db_path = tmp_path / "meta.db"
        _create_valid_db(db_path)

        vec = _make_vec(0.7)
        _insert_chunk(
            db_path,
            "chunk con metadata",
            vec,
            metadata={"capitulo": "intro", "pagina": 1},
        )

        source = SqliteKnowledgeSource(
            source_id="meta-src",
            description="Metadata test",
            db_path=str(db_path),
        )

        resultados = await source.search(vec, top_k=5, min_score=0.0)

        assert len(resultados) == 1
        assert resultados[0].metadata["capitulo"] == "intro"
        assert resultados[0].metadata["pagina"] == 1


class TestSqliteKnowledgeSourceValidationErrors:
    async def test_wrong_dim_512_raises_error(self, tmp_path: Path) -> None:
        """DB con embeddings de 512 dimensiones debe lanzar KnowledgeConfigError."""
        db_path = tmp_path / "wrong_dim.db"
        _create_valid_db(db_path, dim=512)

        source = SqliteKnowledgeSource(
            source_id="wrong-dim",
            description="Wrong dim test",
            db_path=str(db_path),
        )

        with pytest.raises(KnowledgeConfigError, match="512"):
            await source.search(_make_vec(1.0, dim=512), top_k=3, min_score=0.0)

    async def test_missing_chunks_table_raises_error(self, tmp_path: Path) -> None:
        """DB sin tabla `chunks` debe lanzar KnowledgeConfigError."""
        db_path = tmp_path / "no_chunks.db"

        # Crear DB solo con chunk_embeddings, sin chunks
        conn = sqlite3.connect(str(db_path))
        conn.enable_load_extension(True)
        conn.load_extension(sqlite_vec.loadable_path())
        conn.enable_load_extension(False)
        conn.execute("CREATE VIRTUAL TABLE chunk_embeddings USING vec0(embedding FLOAT[384])")
        conn.commit()
        conn.close()

        source = SqliteKnowledgeSource(
            source_id="no-chunks",
            description="No chunks table",
            db_path=str(db_path),
        )

        with pytest.raises(KnowledgeConfigError, match="chunks"):
            await source.search(_make_vec(1.0), top_k=3, min_score=0.0)

    async def test_missing_chunk_embeddings_table_raises_error(self, tmp_path: Path) -> None:
        """DB sin tabla `chunk_embeddings` debe lanzar KnowledgeConfigError."""
        db_path = tmp_path / "no_embeddings.db"

        # Crear DB solo con chunks, sin chunk_embeddings
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY,
                source_path TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT DEFAULT '{}'
            )
        """)
        conn.commit()
        conn.close()

        source = SqliteKnowledgeSource(
            source_id="no-embeddings",
            description="No embeddings table",
            db_path=str(db_path),
        )

        with pytest.raises(KnowledgeConfigError, match="chunk_embeddings"):
            await source.search(_make_vec(1.0), top_k=3, min_score=0.0)

    async def test_error_message_contains_source_id(self, tmp_path: Path) -> None:
        """El mensaje de KnowledgeConfigError debe mencionar el source_id."""
        db_path = tmp_path / "no_table.db"

        # DB vacía — sin ninguna tabla
        sqlite3.connect(str(db_path)).close()

        source = SqliteKnowledgeSource(
            source_id="fuente-identificable",
            description="Test",
            db_path=str(db_path),
        )

        with pytest.raises(KnowledgeConfigError, match="fuente-identificable"):
            await source.search(_make_vec(1.0), top_k=3, min_score=0.0)

    async def test_validation_runs_once(self, tmp_path: Path) -> None:
        """Una vez validado, el flag _validated evita re-validar en llamadas subsiguientes."""
        db_path = tmp_path / "valid_once.db"
        _create_valid_db(db_path)

        vec = _make_vec(1.0)
        _insert_chunk(db_path, "chunk único", vec)

        source = SqliteKnowledgeSource(
            source_id="once-src",
            description="Validation once",
            db_path=str(db_path),
        )

        assert source._validated is False

        await source.search(vec, top_k=3, min_score=0.0)
        assert source._validated is True

        # Segunda búsqueda — _validated sigue True (no re-valida)
        await source.search(vec, top_k=3, min_score=0.0)
        assert source._validated is True
