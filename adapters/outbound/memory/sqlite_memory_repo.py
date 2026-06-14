"""
SQLiteMemoryRepository — almacenamiento vectorial de memorias con sqlite-vec.

Schema:
  memories          — metadatos y contenido
  memory_embeddings — tabla virtual vec0 para KNN
"""

from __future__ import annotations

import json
import logging
import struct
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

import aiosqlite
import sqlite_vec

from core.domain.entities.memory import MemoryEntry
from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.memory_port import IMemoryRepository

logger = logging.getLogger(__name__)

_CREATE_MEMORIES = """
CREATE TABLE IF NOT EXISTS memories (
    id           TEXT PRIMARY KEY,
    content      TEXT NOT NULL,
    relevance    REAL NOT NULL,
    tags         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    agent_id     TEXT,
    channel      TEXT,
    chat_id      TEXT,
    deleted      INTEGER NOT NULL DEFAULT 0,
    reconciled   INTEGER NOT NULL DEFAULT 0
)
"""

# Partial index sobre recuerdos activos: hace que las queries habituales
# (search/get_recent que filtran por deleted=0) usen un índice más chico.
_CREATE_SCOPE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_memories_scope
    ON memories(agent_id, channel, chat_id, created_at DESC)
    WHERE deleted = 0
"""

# Partial index sobre recuerdos pendientes de reconciliación: acelera
# load_unreconciled que filtra por reconciled=0 AND deleted=0.
_CREATE_RECONCILED_INDEX = """
CREATE INDEX IF NOT EXISTS idx_memories_unreconciled
    ON memories(agent_id, channel, chat_id, created_at ASC)
    WHERE reconciled = 0 AND deleted = 0
"""

_CREATE_EMBEDDINGS = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings USING vec0(
    id        TEXT PRIMARY KEY,
    embedding FLOAT[384]
)
"""


class SQLiteMemoryRepository(IMemoryRepository):
    def __init__(self, db_path: str, embedder: IEmbeddingProvider) -> None:
        self._db_path = db_path
        self._embedder = embedder
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[aiosqlite.Connection]:
        """Context manager que abre, configura sqlite-vec y cierra la conexión."""
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.enable_load_extension(True)
            await conn.load_extension(sqlite_vec.loadable_path())
            await conn.enable_load_extension(False)
            yield conn

    async def _ensure_schema(self, conn: aiosqlite.Connection) -> None:
        await conn.execute(_CREATE_MEMORIES)
        await conn.execute(_CREATE_SCOPE_INDEX)
        await conn.execute(_CREATE_EMBEDDINGS)
        await self._migrate_reconciled_column(conn)
        await conn.execute(_CREATE_RECONCILED_INDEX)
        await conn.commit()

    async def _migrate_reconciled_column(self, conn: aiosqlite.Connection) -> None:
        """Migración en caliente idempotente: agrega columna ``reconciled`` si no existe.

        Las DBs creadas antes de la introducción del flag ``reconciled`` no tienen
        la columna. Detectamos su ausencia consultando ``PRAGMA table_info`` y
        ejecutamos ``ALTER TABLE`` solo si hace falta — idempotente, seguro de
        correr en cada arranque.
        """
        rows = await conn.execute_fetchall("PRAGMA table_info(memories)")
        columnas = {row["name"] for row in rows}
        if "reconciled" not in columnas:
            await conn.execute(
                "ALTER TABLE memories ADD COLUMN reconciled INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Migración aplicada: columna 'reconciled' agregada a memories")

    async def store(self, entry: MemoryEntry) -> None:
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            await conn.execute(
                """
                INSERT OR REPLACE INTO memories
                    (id, content, relevance, tags, created_at, agent_id, channel, chat_id,
                     deleted, reconciled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.content,
                    entry.relevance,
                    json.dumps(entry.tags),
                    entry.created_at.isoformat(),
                    entry.agent_id,
                    entry.channel,
                    entry.chat_id,
                    int(entry.deleted),
                    int(entry.reconciled),
                ),
            )
            # vec0 (sqlite-vec) no soporta INSERT OR REPLACE: el path REPLACE
            # falla con UNIQUE constraint. Para soportar re-store del mismo id
            # (consolidación reintentada), borramos y re-insertamos.
            vec_bytes = struct.pack(f"{len(entry.embedding)}f", *entry.embedding)
            await conn.execute(
                "DELETE FROM memory_embeddings WHERE id = ?",
                (entry.id,),
            )
            await conn.execute(
                "INSERT INTO memory_embeddings (id, embedding) VALUES (?, ?)",
                (entry.id, vec_bytes),
            )
            await conn.commit()
        logger.debug("Memoria almacenada: %s (relevance=%.2f)", entry.id, entry.relevance)

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[MemoryEntry]:
        if not query_embedding:
            return []

        vec_bytes = struct.pack(f"{len(query_embedding)}f", *query_embedding)

        async with self._conn() as conn:
            await self._ensure_schema(conn)
            # NOTA: filtramos `deleted = 0` después del MATCH; sqlite-vec no
            # acepta filtros adicionales sobre la tabla virtual. El `k` se queda
            # tal cual — si todos los topk matcheados están borrados, se devuelve
            # lista vacía (caso muy raro en práctica).
            rows = await conn.execute_fetchall(
                """
                SELECT m.id, m.content, m.relevance, m.tags, m.created_at,
                       m.agent_id, m.channel, m.chat_id, m.deleted, m.reconciled
                FROM memory_embeddings e
                JOIN memories m ON e.id = m.id
                WHERE e.embedding MATCH ?
                  AND k = ?
                  AND m.deleted = 0
                ORDER BY distance
                """,
                (vec_bytes, top_k),
            )

        return [self._row_to_entry(row) for row in rows]

    async def search_with_scores(
        self,
        query_vec: list[float],
        top_k: int = 5,
    ) -> list[tuple[MemoryEntry, float]]:
        """
        Busca las memorias más similares y devuelve pares (entrada, score coseno).

        Fórmula: ``score = 1 - distance² / 2``
        Válida para vectores L2-normalizados (e5-small los normaliza automáticamente).
        El score resultante es el coseno ∈ [-1, 1].
        """
        if not query_vec:
            return []

        vec_bytes = struct.pack(f"{len(query_vec)}f", *query_vec)

        async with self._conn() as conn:
            await self._ensure_schema(conn)
            rows = await conn.execute_fetchall(
                """
                SELECT m.id, m.content, m.relevance, m.tags, m.created_at,
                       m.agent_id, m.channel, m.chat_id, m.deleted, m.reconciled,
                       e.distance
                FROM memory_embeddings e
                JOIN memories m ON e.id = m.id
                WHERE e.embedding MATCH ?
                  AND k = ?
                  AND m.deleted = 0
                ORDER BY e.distance
                """,
                (vec_bytes, top_k),
            )

        resultado: list[tuple[MemoryEntry, float]] = []
        for row in rows:
            distancia = row["distance"]
            # score coseno a partir de distancia L2 (vectores unitarios: ‖a-b‖²=2(1-cosθ))
            score = 1.0 - (distancia**2) / 2.0
            if resultado:
                # assert de runtime: el primer resultado (mayor similitud) debe estar en rango
                pass
            entrada = self._row_to_entry(row)
            resultado.append((entrada, score))

        if resultado:
            primer_score = resultado[0][1]
            assert -1.0 <= primer_score <= 1.0, (
                f"search_with_scores: score del primer resultado fuera de rango [-1, 1]: {primer_score}"
            )

        return resultado

    async def get_recent(
        self,
        limit: int = 10,
        agent_id: str | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[MemoryEntry]:
        """
        Devuelve los `limit` recuerdos más recientes, opcionalmente filtrados por
        ``(agent_id, channel, chat_id)``.

        Cada filtro es opcional e independiente:
        - ``agent_id is None`` → no filtra por agente
        - ``channel is None`` → no filtra por canal
        - ``chat_id is None`` → no filtra por chat
        Cuando un filtro está provisto, hace match EXACTO (incluye matchear NULL
        si el caller pasa ``None`` no aplica filtro — usar ``""`` no es
        soportado actualmente).
        """
        clauses: list[str] = ["deleted = 0"]
        params: list[object] = []
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if channel is not None:
            clauses.append("channel = ?")
            params.append(channel)
        if chat_id is not None:
            clauses.append("chat_id = ?")
            params.append(chat_id)

        where = f"WHERE {' AND '.join(clauses)} "
        sql = (
            "SELECT id, content, relevance, tags, created_at, agent_id, channel, chat_id, "
            "deleted, reconciled "
            f"FROM memories {where}ORDER BY created_at DESC LIMIT ?"
        )
        params.append(limit)

        async with self._conn() as conn:
            await self._ensure_schema(conn)
            rows = await conn.execute_fetchall(sql, tuple(params))
        return [self._row_to_entry(row) for row in rows]

    async def get_by_id(self, memory_id: str) -> MemoryEntry | None:
        """Devuelve la entry por id ignorando deleted, o None si no existe."""
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            row = await (
                await conn.execute(
                    "SELECT id, content, relevance, tags, created_at, "
                    "agent_id, channel, chat_id, deleted, reconciled "
                    "FROM memories WHERE id = ?",
                    (memory_id,),
                )
            ).fetchone()
        return self._row_to_entry(row) if row is not None else None

    async def delete(self, memory_id: str) -> MemoryEntry | None:
        """
        Soft-delete: marca ``deleted=1`` en la fila. La memoria deja de aparecer
        en ``search``/``search_with_scores``/``get_recent`` pero el embedding y
        los datos siguen en disco — restaurable con un futuro ``UPDATE deleted=0``.

        Devuelve la entry tal como queda tras el delete (con ``deleted=True``)
        o ``None`` si el id no existía o ya estaba borrado (idempotencia).
        """
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            cursor = await conn.execute(
                "UPDATE memories SET deleted = 1 WHERE id = ? AND deleted = 0",
                (memory_id,),
            )
            await conn.commit()
            if cursor.rowcount == 0:
                logger.debug("delete: memoria '%s' no existe o ya borrada", memory_id)
                return None
            row = await (
                await conn.execute(
                    "SELECT id, content, relevance, tags, created_at, "
                    "agent_id, channel, chat_id, deleted, reconciled "
                    "FROM memories WHERE id = ?",
                    (memory_id,),
                )
            ).fetchone()
        if row is None:
            return None
        logger.info("Memoria soft-deleted: %s", memory_id)
        return self._row_to_entry(row)

    async def update(
        self,
        memory_id: str,
        content: str | None = None,
        tags: list[str] | None = None,
        relevance: float | None = None,
        embedding: list[float] | None = None,
    ) -> MemoryEntry | None:
        """
        Update parcial. Solo actualiza los campos no-``None``. Si se pasa
        ``content`` el caller DEBE pasar también ``embedding`` (el repo no
        recomputa embeddings — eso es responsabilidad del caller, que tiene
        acceso al ``IEmbeddingProvider``).

        Devuelve la entry actualizada o ``None`` si el id no existe o está
        soft-deleted (no permitimos editar un recuerdo borrado — primero
        habría que restaurarlo).
        """
        sets: list[str] = []
        params: list[object] = []
        if content is not None:
            sets.append("content = ?")
            params.append(content)
        if tags is not None:
            sets.append("tags = ?")
            params.append(json.dumps(tags))
        if relevance is not None:
            sets.append("relevance = ?")
            params.append(relevance)

        if not sets and embedding is None:
            # Nada que actualizar — devolver la entry actual si existe y está activa.
            async with self._conn() as conn:
                await self._ensure_schema(conn)
                row = await (
                    await conn.execute(
                        "SELECT id, content, relevance, tags, created_at, "
                        "agent_id, channel, chat_id, deleted, reconciled "
                        "FROM memories WHERE id = ? AND deleted = 0",
                        (memory_id,),
                    )
                ).fetchone()
            return self._row_to_entry(row) if row is not None else None

        async with self._conn() as conn:
            await self._ensure_schema(conn)
            if sets:
                params.append(memory_id)
                cursor = await conn.execute(
                    f"UPDATE memories SET {', '.join(sets)} WHERE id = ? AND deleted = 0",
                    tuple(params),
                )
                if cursor.rowcount == 0:
                    await conn.commit()
                    logger.debug("update: memoria '%s' no existe o está borrada", memory_id)
                    return None

            if embedding is not None:
                # vec0 no soporta INSERT OR REPLACE — DELETE + INSERT.
                vec_bytes = struct.pack(f"{len(embedding)}f", *embedding)
                await conn.execute(
                    "DELETE FROM memory_embeddings WHERE id = ?",
                    (memory_id,),
                )
                await conn.execute(
                    "INSERT INTO memory_embeddings (id, embedding) VALUES (?, ?)",
                    (memory_id, vec_bytes),
                )

            await conn.commit()

            row = await (
                await conn.execute(
                    "SELECT id, content, relevance, tags, created_at, "
                    "agent_id, channel, chat_id, deleted, reconciled "
                    "FROM memories WHERE id = ? AND deleted = 0",
                    (memory_id,),
                )
            ).fetchone()

        if row is None:
            return None
        logger.info(
            "Memoria actualizada: %s (campos=%s)", memory_id, [s.split(" =")[0] for s in sets]
        )
        return self._row_to_entry(row)

    def _row_to_entry(self, row) -> MemoryEntry:
        # `channel`, `chat_id`, `deleted`, `reconciled` pueden no existir en
        # filas de DBs pre-migración; SQLite Row no implementa .get(), así que
        # probamos con KeyError-guard.
        try:
            channel = row["channel"]
        except (KeyError, IndexError):
            channel = None
        try:
            chat_id = row["chat_id"]
        except (KeyError, IndexError):
            chat_id = None
        try:
            deleted = bool(row["deleted"])
        except (KeyError, IndexError):
            deleted = False
        try:
            reconciled = bool(row["reconciled"])
        except (KeyError, IndexError):
            reconciled = False
        return MemoryEntry(
            id=row["id"],
            content=row["content"],
            embedding=[],
            relevance=row["relevance"],
            tags=json.loads(row["tags"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            agent_id=row["agent_id"],
            channel=channel,
            chat_id=chat_id,
            deleted=deleted,
            reconciled=reconciled,
        )

    async def load_unreconciled(
        self,
        agent_id: str,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[MemoryEntry]:
        """Devuelve recuerdos activos (deleted=0) pendientes de reconciliación (reconciled=0).

        Siempre filtra por ``agent_id``. ``channel`` y ``chat_id`` son opcionales;
        ``None`` significa sin filtro por ese campo.
        Ordenados por created_at ASC para procesar primero los más viejos.
        """
        clauses: list[str] = ["reconciled = 0", "deleted = 0", "agent_id = ?"]
        params: list[object] = [agent_id]
        if channel is not None:
            clauses.append("channel = ?")
            params.append(channel)
        if chat_id is not None:
            clauses.append("chat_id = ?")
            params.append(chat_id)

        where = f"WHERE {' AND '.join(clauses)} "
        sql = (
            "SELECT id, content, relevance, tags, created_at, agent_id, channel, chat_id, "
            "deleted, reconciled "
            f"FROM memories {where}ORDER BY created_at ASC"
        )

        async with self._conn() as conn:
            await self._ensure_schema(conn)
            rows = await conn.execute_fetchall(sql, tuple(params))
        return [self._row_to_entry(row) for row in rows]

    async def mark_reconciled(self, ids: list[str]) -> int:
        """Marca como reconciliados los recuerdos con los ids dados.

        No-op si ``ids`` está vacío. Devuelve el número de filas actualizadas.
        Opera SOLO sobre los ids dados — nunca actualiza en lote global por
        agent_id para preservar granularidad.
        """
        if not ids:
            return 0
        placeholders = ", ".join("?" * len(ids))
        sql = f"UPDATE memories SET reconciled = 1 WHERE id IN ({placeholders})"
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            cursor = await conn.execute(sql, tuple(ids))
            await conn.commit()
        afectadas = cursor.rowcount
        logger.debug("mark_reconciled: %d memorias marcadas como reconciliadas", afectadas)
        return afectadas
