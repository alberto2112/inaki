"""
DocumentKnowledgeSource — fuente de conocimiento basada en documentos del sistema de archivos.

Indexa archivos de una carpeta, persiste chunks + embeddings en ~/.inaki/knowledge/{id}.db
y expone búsqueda vectorial con sqlite-vec.

Schema de la DB:
  chunks          — contenido y metadatos por chunk
  chunk_embeddings — tabla virtual vec0 (L2 → coseno vía score = 1 - d²/2)
  files_indexed   — registro de archivos procesados con su mtime
"""

from __future__ import annotations

import logging
import shutil
import struct
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import aiosqlite
import sqlite_vec

from adapters.outbound.knowledge._chunker import chunkear_archivo
from core.domain.errors import KnowledgeError
from core.domain.value_objects.knowledge_chunk import KnowledgeChunk
from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.knowledge_port import IIndexableKnowledgeSource

logger = logging.getLogger(__name__)

_INAKI_HOME = Path.home() / ".inaki"

_CREATE_CHUNKS = """
CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    file_path   TEXT NOT NULL,
    file_mtime  REAL NOT NULL,
    chunk_idx   INTEGER NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
)
"""

_CREATE_CHUNKS_IDX = "CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_path)"

_CREATE_CHUNK_EMBEDDINGS = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_embeddings USING vec0(
    id        TEXT PRIMARY KEY,
    embedding FLOAT[384]
)
"""

_CREATE_FILES_INDEXED = """
CREATE TABLE IF NOT EXISTS files_indexed (
    file_path   TEXT PRIMARY KEY,
    mtime       REAL NOT NULL,
    chunk_count INTEGER NOT NULL
)
"""


class DocumentKnowledgeSource(IIndexableKnowledgeSource):
    """
    Fuente de conocimiento que indexa documentos de una carpeta y busca por similitud coseno.

    Indexación incremental: solo re-embebe archivos cuya mtime cambió desde la última indexación.
    Los embeddings se persisten en ~/.inaki/knowledge/{id}.db.

    Implementa ``IIndexableKnowledgeSource``: además de ``search()`` expone
    ``index()``/``ingest_file()``/``list_files()``/``delete_file()``/``get_stats()``
    para que ``ManageKnowledgeUseCase`` (y a través de él la tool ``knowledge_admin``
    y los canales) gestionen el índice.
    """

    def __init__(
        self,
        source_id: str,
        description: str,
        path: str,
        embedder: IEmbeddingProvider,
        glob: str = "**/*.md",
        chunk_size: int = 500,
        chunk_overlap: int = 80,
        dimension: int = 384,
    ) -> None:
        self._source_id = source_id
        self._description = description
        self._docs_path = Path(path).expanduser().resolve()
        self._embedder = embedder
        self._glob = glob
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._dimension = dimension

        # DB de índice: ~/.inaki/knowledge/{id}.db
        db_dir = _INAKI_HOME / "knowledge"
        db_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = str(db_dir / f"{source_id}.db")

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def description(self) -> str:
        return self._description

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[aiosqlite.Connection]:
        """Abre conexión con sqlite-vec cargado."""
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.enable_load_extension(True)
            await conn.load_extension(sqlite_vec.loadable_path())
            await conn.enable_load_extension(False)
            yield conn

    async def _ensure_schema(self, conn: aiosqlite.Connection) -> None:
        await conn.execute(_CREATE_CHUNKS)
        await conn.execute(_CREATE_CHUNKS_IDX)
        await conn.execute(_CREATE_CHUNK_EMBEDDINGS)
        await conn.execute(_CREATE_FILES_INDEXED)
        await conn.commit()

    async def index(self) -> dict[str, int]:
        """
        Indexa (o re-indexa) los documentos de la carpeta configurada.

        Compara mtime de cada archivo con la tabla files_indexed.
        Solo re-embebe archivos nuevos o modificados.

        Returns:
            Diccionario con estadísticas: {
                "archivos_procesados": int,
                "archivos_saltados": int,
                "chunks_nuevos": int,
            }
        """
        if not self._docs_path.exists():
            logger.warning(
                "DocumentKnowledgeSource '%s': carpeta no encontrada: %s",
                self._source_id,
                self._docs_path,
            )
            return {"archivos_procesados": 0, "archivos_saltados": 0, "chunks_nuevos": 0}

        archivos = list(self._docs_path.glob(self._glob))
        stats = {"archivos_procesados": 0, "archivos_saltados": 0, "chunks_nuevos": 0}

        async with self._conn() as conn:
            await self._ensure_schema(conn)

            # Cargar registro de archivos ya indexados
            rows = await conn.execute_fetchall("SELECT file_path, mtime FROM files_indexed")
            indexados: dict[str, float] = {row["file_path"]: row["mtime"] for row in rows}

            for archivo in sorted(archivos):
                ruta_str = str(archivo)
                try:
                    mtime_actual = archivo.stat().st_mtime
                except OSError as exc:
                    logger.warning(
                        "DocumentKnowledgeSource '%s': no se pudo leer stat de %s: %s",
                        self._source_id,
                        ruta_str,
                        exc,
                    )
                    continue

                mtime_indexado = indexados.get(ruta_str)
                if mtime_indexado is not None and abs(mtime_actual - mtime_indexado) < 0.001:
                    stats["archivos_saltados"] += 1
                    continue

                # Archivo nuevo o modificado → re-indexar
                chunk_count = await self._index_file(conn, archivo, mtime_actual)
                if chunk_count is None:
                    continue
                await conn.commit()

                stats["archivos_procesados"] += 1
                stats["chunks_nuevos"] += chunk_count

        return stats

    async def _index_file(
        self,
        conn: aiosqlite.Connection,
        archivo: Path,
        mtime_actual: float,
    ) -> int | None:
        """Indexa un único archivo: chunkea, borra chunks viejos, embebe e inserta.

        NO hace commit (el caller decide el alcance transaccional) ni filtra por
        mtime (esa decisión es del caller — ``index()`` saltea sin cambios,
        ``ingest_file()`` siempre re-indexa).

        Returns:
            Número de chunks insertados, o ``None`` si el archivo no se pudo
            chunkear (error logueado, el caller debe saltearlo).
        """
        ruta_str = str(archivo)
        logger.debug("DocumentKnowledgeSource '%s': indexando %s", self._source_id, ruta_str)

        try:
            chunks = chunkear_archivo(archivo, self._chunk_size, self._chunk_overlap)
        except Exception as exc:
            logger.warning(
                "DocumentKnowledgeSource '%s': error chunkando %s: %s",
                self._source_id,
                ruta_str,
                exc,
            )
            return None

        # Borrar chunks viejos: primero recuperar IDs para borrar embeddings,
        # luego borrar chunks (el orden importa por la FK implícita en vec0).
        ids_viejos = await conn.execute_fetchall(
            "SELECT id FROM chunks WHERE file_path = ?", (ruta_str,)
        )
        for row in ids_viejos:
            await conn.execute("DELETE FROM chunk_embeddings WHERE id = ?", (row["id"],))
        await conn.execute("DELETE FROM chunks WHERE file_path = ?", (ruta_str,))

        ahora = datetime.now(timezone.utc).isoformat()
        chunk_count = 0

        for idx, contenido in enumerate(chunks):
            chunk_id = str(uuid.uuid4())
            await conn.execute(
                """
                INSERT INTO chunks (id, file_path, file_mtime, chunk_idx, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (chunk_id, ruta_str, mtime_actual, idx, contenido, ahora),
            )

            try:
                embedding = await self._embedder.embed_query(contenido)
                vec_bytes = struct.pack(f"{len(embedding)}f", *embedding)
                await conn.execute(
                    "INSERT INTO chunk_embeddings (id, embedding) VALUES (?, ?)",
                    (chunk_id, vec_bytes),
                )
            except Exception as exc:
                logger.warning(
                    "DocumentKnowledgeSource '%s': error embebiendo chunk %d de %s: %s",
                    self._source_id,
                    idx,
                    ruta_str,
                    exc,
                )
                # Borrar el chunk sin embedding para mantener consistencia
                await conn.execute("DELETE FROM chunks WHERE id = ?", (chunk_id,))
                continue

            chunk_count += 1

        # Actualizar registro de archivos indexados
        await conn.execute(
            """
            INSERT OR REPLACE INTO files_indexed (file_path, mtime, chunk_count)
            VALUES (?, ?, ?)
            """,
            (ruta_str, mtime_actual, chunk_count),
        )

        logger.info(
            "DocumentKnowledgeSource '%s': indexado %s (%d chunks)",
            self._source_id,
            archivo.name,
            chunk_count,
        )
        return chunk_count

    async def ingest_file(self, source_path: Path) -> dict[str, int | str]:
        """Incorpora un archivo externo a la carpeta de la fuente y lo indexa (modelo inbox).

        Copia preservando la mtime (``copy2``) para que el indexado incremental
        de ``index()`` lo reconozca igual. Indexa el archivo de inmediato sin
        filtrar por ``glob`` — un ``.txt`` entra aunque el glob sea ``**/*.md``.

        Raises:
            FileNotFoundError: Si ``source_path`` no existe o no es un archivo.
        """
        src = Path(source_path).expanduser().resolve()
        if not src.exists() or not src.is_file():
            raise FileNotFoundError(f"No existe el archivo a ingerir: {src}")

        self._docs_path.mkdir(parents=True, exist_ok=True)
        dest = self._docs_path / src.name
        shutil.copy2(src, dest)

        async with self._conn() as conn:
            await self._ensure_schema(conn)
            chunk_count = await self._index_file(conn, dest, dest.stat().st_mtime)
            await conn.commit()

        if chunk_count is None:
            # El archivo se copió pero no se pudo chunkear (formato ilegible).
            raise KnowledgeError(
                f"El archivo '{src.name}' se copió a la fuente '{self._source_id}' "
                "pero no se pudo procesar (formato ilegible o vacío)."
            )

        logger.info(
            "DocumentKnowledgeSource '%s': ingerido %s (%d chunks)",
            self._source_id,
            src.name,
            chunk_count,
        )
        return {"stored_path": str(dest), "chunks_nuevos": chunk_count}

    async def search(
        self,
        query_vec: list[float],
        top_k: int = 3,
        min_score: float = 0.5,
    ) -> list[KnowledgeChunk]:
        """
        Busca los chunks más similares al vector de consulta.

        Usa sqlite-vec vec0 con distancia L2 → convierte a coseno: score = 1 - d²/2.

        Args:
            query_vec: Vector de embedding de la consulta.
            top_k: Número máximo de resultados.
            min_score: Score mínimo de coseno para incluir un resultado.

        Returns:
            Lista de KnowledgeChunk ordenada por score descendente.
        """
        if not query_vec:
            return []

        vec_bytes = struct.pack(f"{len(query_vec)}f", *query_vec)

        async with self._conn() as conn:
            await self._ensure_schema(conn)

            try:
                rows = await conn.execute_fetchall(
                    """
                    SELECT c.id, c.content, c.file_path, c.chunk_idx, e.distance
                    FROM chunk_embeddings e
                    JOIN chunks c ON e.id = c.id
                    WHERE e.embedding MATCH ?
                      AND k = ?
                    ORDER BY e.distance
                    """,
                    (vec_bytes, top_k),
                )
            except Exception as exc:
                logger.warning(
                    "DocumentKnowledgeSource '%s': error en búsqueda vectorial: %s",
                    self._source_id,
                    exc,
                )
                return []

        fragmentos: list[KnowledgeChunk] = []
        for row in rows:
            distancia = row["distance"]
            score = 1.0 - (distancia**2) / 2.0
            score_efectivo = max(0.0, score)

            if score_efectivo < min_score:
                continue

            fragmentos.append(
                KnowledgeChunk(
                    source_id=self._source_id,
                    content=row["content"],
                    score=score,
                    metadata={
                        "file_path": row["file_path"],
                        "chunk_idx": row["chunk_idx"],
                    },
                )
            )

        return fragmentos

    async def get_stats(self) -> dict[str, int | str | float | None]:
        """Devuelve estadísticas del índice: archivos, chunks, última indexación y dimensión."""
        async with self._conn() as conn:
            await self._ensure_schema(conn)

            row_files = list(await conn.execute_fetchall("SELECT COUNT(*) as n FROM files_indexed"))
            row_chunks = list(await conn.execute_fetchall("SELECT COUNT(*) as n FROM chunks"))
            row_mtime = list(
                await conn.execute_fetchall("SELECT MAX(mtime) as m FROM files_indexed")
            )

        last_mtime = row_mtime[0]["m"] if row_mtime else None

        return {
            "source_id": self._source_id,
            "archivos_indexados": row_files[0]["n"] if row_files else 0,
            "chunks_totales": row_chunks[0]["n"] if row_chunks else 0,
            "db_path": self._db_path,
            "last_indexed_mtime": last_mtime,
            "embedding_dimension": self._dimension,
        }

    async def list_files(self) -> list[dict[str, int | str | float]]:
        """Lista los archivos indexados con su ruta, mtime y cantidad de chunks."""
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            rows = await conn.execute_fetchall(
                "SELECT file_path, mtime, chunk_count FROM files_indexed ORDER BY file_path"
            )

        return [
            {
                "file_path": row["file_path"],
                "mtime": row["mtime"],
                "chunk_count": row["chunk_count"],
            }
            for row in rows
        ]

    async def delete_file(self, file_path: str, *, remove_physical: bool = False) -> int:
        """Elimina del índice todos los chunks de un archivo. Ver contrato del port.

        Resuelve ``file_path`` tanto contra la ruta exacta indexada como contra
        su forma absoluta dentro de la carpeta de la fuente — así el caller puede
        borrar pasando solo el nombre del archivo (``doc.txt``) o la ruta completa.
        """
        ruta_indexada = await self._resolver_file_path(file_path)
        if ruta_indexada is None:
            return 0

        async with self._conn() as conn:
            await self._ensure_schema(conn)
            ids = list(
                await conn.execute_fetchall(
                    "SELECT id FROM chunks WHERE file_path = ?", (ruta_indexada,)
                )
            )
            for row in ids:
                await conn.execute("DELETE FROM chunk_embeddings WHERE id = ?", (row["id"],))
            await conn.execute("DELETE FROM chunks WHERE file_path = ?", (ruta_indexada,))
            await conn.execute("DELETE FROM files_indexed WHERE file_path = ?", (ruta_indexada,))
            await conn.commit()

        borrados = len(ids)

        # Borrado físico opcional: SOLO si el archivo vive dentro de la carpeta de
        # la fuente. Defensa contra borrar documentos del usuario fuera del inbox.
        if remove_physical and borrados:
            archivo = Path(ruta_indexada)
            try:
                dentro = archivo.resolve().is_relative_to(self._docs_path)
            except (OSError, ValueError):
                dentro = False
            if dentro and archivo.exists():
                try:
                    archivo.unlink()
                except OSError as exc:
                    logger.warning(
                        "DocumentKnowledgeSource '%s': no pude borrar archivo físico %s: %s",
                        self._source_id,
                        ruta_indexada,
                        exc,
                    )

        logger.info(
            "DocumentKnowledgeSource '%s': borrados %d chunks de %s",
            self._source_id,
            borrados,
            ruta_indexada,
        )
        return borrados

    async def _resolver_file_path(self, file_path: str) -> str | None:
        """Mapea ``file_path`` (nombre o ruta) a la ruta exacta presente en el índice.

        Devuelve la ruta indexada que matchea, o ``None`` si no hay coincidencia.
        """
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            rows = await conn.execute_fetchall("SELECT file_path FROM files_indexed")

        indexadas = [row["file_path"] for row in rows]
        if file_path in indexadas:
            return file_path

        # Match por forma absoluta dentro de la carpeta de la fuente.
        candidato = str((self._docs_path / Path(file_path).name).resolve())
        if candidato in indexadas:
            return candidato

        # Match por nombre de archivo (último recurso, si es inequívoco).
        nombre = Path(file_path).name
        por_nombre = [p for p in indexadas if Path(p).name == nombre]
        if len(por_nombre) == 1:
            return por_nombre[0]

        return None
