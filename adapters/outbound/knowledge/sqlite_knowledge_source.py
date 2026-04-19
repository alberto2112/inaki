"""
SqliteKnowledgeSource — fuente de conocimiento basada en una DB SQLite pre-construida
por el usuario.

La DB debe seguir el schema documentado en docs/configuracion.md:
  - chunks(id INTEGER PRIMARY KEY, source_path TEXT, content TEXT, metadata_json TEXT)
  - chunk_embeddings — tabla virtual vec0 con embedding FLOAT[384]
    (rowid de chunk_embeddings = id de chunks)

En la primera conexión valida:
  1. Que las tablas `chunks` y `chunk_embeddings` existen.
  2. Que la dimensión de los embeddings coincide con EXPECTED_EMBEDDING_DIM (384).

Si alguna validación falla, lanza KnowledgeConfigError (mensaje en español, para devs).
"""

from __future__ import annotations

import logging
import re
import struct
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite
import sqlite_vec

from core.domain.errors import KnowledgeConfigError
from core.domain.value_objects.knowledge_chunk import KnowledgeChunk
from core.ports.outbound.knowledge_port import IKnowledgeSource

logger = logging.getLogger(__name__)

EXPECTED_EMBEDDING_DIM = 384

# Regex para extraer la dimensión del DDL de vec0.
# Ejemplo: "embedding FLOAT[384]" → grupo 1 = "384"
_VEC0_DIM_RE = re.compile(r"FLOAT\s*\[(\d+)\]", re.IGNORECASE)


class SqliteKnowledgeSource(IKnowledgeSource):
    """
    Fuente de conocimiento que lee una DB SQLite pre-construida por el usuario.

    En la primera conexión valida el schema y la dimensión de los embeddings.
    Lanza KnowledgeConfigError si algo no cuadra — el container captura esto
    al arrancar y omite la fuente con un log de error claro.
    """

    def __init__(
        self,
        source_id: str,
        description: str,
        db_path: str,
    ) -> None:
        self._source_id = source_id
        self._description = description
        self._db_path = db_path
        self._validated: bool = False

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

    async def _validate_schema(self, conn: aiosqlite.Connection) -> None:
        """
        Valida que la DB del usuario tiene el schema esperado.

        Verifica:
        - Tabla `chunks` existe.
        - Tabla virtual `chunk_embeddings` (vec0) existe.
        - La dimensión del vec0 es EXPECTED_EMBEDDING_DIM.

        Raises:
            KnowledgeConfigError: si alguna verificación falla.
        """
        # 1. Verificar que `chunks` existe
        rows = await conn.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks'"
        )
        if not rows:
            raise KnowledgeConfigError(
                f"Fuente '{self._source_id}': la tabla 'chunks' no existe en '{self._db_path}'. "
                "Consultá docs/configuracion.md para el schema requerido."
            )

        # 2. Verificar que `chunk_embeddings` existe como tabla virtual
        rows_ve = list(
            await conn.execute_fetchall(
                "SELECT name, sql FROM sqlite_master "
                "WHERE (type='table' OR type='shadow') AND name='chunk_embeddings'"
            )
        )
        if not rows_ve:
            raise KnowledgeConfigError(
                f"Fuente '{self._source_id}': la tabla virtual 'chunk_embeddings' no existe "
                f"en '{self._db_path}'. Consultá docs/configuracion.md para el schema requerido."
            )

        # 3. Extraer la dimensión del DDL del vec0
        rows_ve_list = rows_ve
        ddl = rows_ve_list[0]["sql"] or ""
        match = _VEC0_DIM_RE.search(ddl)
        if not match:
            # DDL no tiene la firma esperada — intentar con sqlite_master tipo 'shadow'
            # o la tabla virtual directamente. Buscar el DDL de la tabla principal vec0.
            rows_virtual = list(
                await conn.execute_fetchall(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunk_embeddings'"
                )
            )
            if rows_virtual:
                ddl = rows_virtual[0]["sql"] or ""
                match = _VEC0_DIM_RE.search(ddl)

        if not match:
            raise KnowledgeConfigError(
                f"Fuente '{self._source_id}': no se pudo determinar la dimensión del vec0 "
                f"'chunk_embeddings' en '{self._db_path}'. "
                f"Verificá que el DDL sea: CREATE VIRTUAL TABLE chunk_embeddings USING vec0(embedding FLOAT[{EXPECTED_EMBEDDING_DIM}])"
            )

        dim = int(match.group(1))
        if dim != EXPECTED_EMBEDDING_DIM:
            raise KnowledgeConfigError(
                f"Fuente '{self._source_id}': dimensión de embeddings incorrecta. "
                f"Esperado {EXPECTED_EMBEDDING_DIM}, encontrado {dim} en '{self._db_path}'. "
                "El modelo de embeddings de Iñaki usa 384 dimensiones (e5-small). "
                "Reconstruí la DB con la dimensión correcta."
            )

    async def _ensure_validated(self, conn: aiosqlite.Connection) -> None:
        """Ejecuta la validación del schema solo la primera vez."""
        if not self._validated:
            await self._validate_schema(conn)
            self._validated = True

    async def search(
        self,
        query_vec: list[float],
        top_k: int = 3,
        min_score: float = 0.5,
    ) -> list[KnowledgeChunk]:
        """
        Busca los chunks más similares al vector de consulta en la DB del usuario.

        Usa sqlite-vec vec0 con distancia L2 → convierte a coseno: score = 1 - d²/2.
        La primera llamada valida el schema. Las siguientes usan el flag `_validated`.

        Args:
            query_vec: Vector de embedding de la consulta (384 dimensiones).
            top_k: Número máximo de resultados.
            min_score: Score mínimo de coseno para incluir un resultado.

        Returns:
            Lista de KnowledgeChunk ordenada por score descendente.

        Raises:
            KnowledgeConfigError: si el schema no es válido (solo en la primera llamada).
        """
        if not query_vec:
            return []

        vec_bytes = struct.pack(f"{len(query_vec)}f", *query_vec)

        async with self._conn() as conn:
            await self._ensure_validated(conn)

            try:
                rows = await conn.execute_fetchall(
                    """
                    SELECT c.id, c.content, c.source_path, c.metadata_json, e.distance
                    FROM chunk_embeddings e
                    JOIN chunks c ON e.rowid = c.id
                    WHERE e.embedding MATCH ?
                      AND k = ?
                    ORDER BY e.distance
                    """,
                    (vec_bytes, top_k),
                )
            except Exception as exc:
                logger.warning(
                    "SqliteKnowledgeSource '%s': error en búsqueda vectorial: %s",
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

            # Parsear metadata_json de manera segura
            import json

            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except (ValueError, TypeError):
                metadata = {}

            metadata["source_path"] = row["source_path"]

            fragmentos.append(
                KnowledgeChunk(
                    source_id=self._source_id,
                    content=row["content"],
                    score=score,
                    metadata=metadata,
                )
            )

        return fragmentos
