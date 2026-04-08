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
    id         TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    relevance  REAL NOT NULL,
    tags       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    agent_id   TEXT
)
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
        await conn.execute(_CREATE_EMBEDDINGS)
        await conn.commit()

    async def store(self, entry: MemoryEntry) -> None:
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            await conn.execute(
                """
                INSERT OR REPLACE INTO memories (id, content, relevance, tags, created_at, agent_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.content,
                    entry.relevance,
                    json.dumps(entry.tags),
                    entry.created_at.isoformat(),
                    entry.agent_id,
                ),
            )
            vec_bytes = struct.pack(f"{len(entry.embedding)}f", *entry.embedding)
            await conn.execute(
                "INSERT OR REPLACE INTO memory_embeddings (id, embedding) VALUES (?, ?)",
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
            rows = await conn.execute_fetchall(
                """
                SELECT m.id, m.content, m.relevance, m.tags, m.created_at, m.agent_id
                FROM memory_embeddings e
                JOIN memories m ON e.id = m.id
                WHERE e.embedding MATCH ?
                  AND k = ?
                ORDER BY distance
                """,
                (vec_bytes, top_k),
            )

        return [self._row_to_entry(row) for row in rows]

    async def get_recent(self, limit: int = 10) -> list[MemoryEntry]:
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            rows = await conn.execute_fetchall(
                "SELECT id, content, relevance, tags, created_at, agent_id "
                "FROM memories ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [self._row_to_entry(row) for row in rows]

    def _row_to_entry(self, row) -> MemoryEntry:
        return MemoryEntry(
            id=row["id"],
            content=row["content"],
            embedding=[],
            relevance=row["relevance"],
            tags=json.loads(row["tags"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            agent_id=row["agent_id"],
        )
