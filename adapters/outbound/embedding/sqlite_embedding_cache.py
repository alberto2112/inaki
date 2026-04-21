"""
SqliteEmbeddingCache — caché persistente de embeddings en SQLite.

Almacena embeddings calculados para evitar recalcularlos en cada reinicio.
Clave compuesta: (content_hash, provider, dimension) para manejar cambios
de proveedor o modelo sin conflictos.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from core.ports.outbound.embedding_cache_port import IEmbeddingCache

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash  TEXT    NOT NULL,
    provider      TEXT    NOT NULL,
    dimension     INTEGER NOT NULL,
    embedding     TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    PRIMARY KEY (content_hash, provider, dimension)
);
"""


class SqliteEmbeddingCache(IEmbeddingCache):
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            yield conn

    async def _ensure_schema(self, conn: aiosqlite.Connection) -> None:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute(_CREATE_TABLE)
        await conn.commit()

    async def get(self, content_hash: str, provider: str, dimension: int) -> list[float] | None:
        try:
            async with self._conn() as conn:
                await self._ensure_schema(conn)
                cursor = await conn.execute(
                    "SELECT embedding FROM embedding_cache "
                    "WHERE content_hash = ? AND provider = ? AND dimension = ?",
                    (content_hash, provider, dimension),
                )
                row = await cursor.fetchone()
                if row is None:
                    return None
                return json.loads(row["embedding"])
        except Exception as exc:
            logger.warning("Error leyendo embedding cache: %s", exc)
            return None

    async def put(
        self, content_hash: str, provider: str, dimension: int, embedding: list[float]
    ) -> None:
        try:
            async with self._conn() as conn:
                await self._ensure_schema(conn)
                await conn.execute(
                    "INSERT OR REPLACE INTO embedding_cache "
                    "(content_hash, provider, dimension, embedding, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        content_hash,
                        provider,
                        dimension,
                        json.dumps(embedding),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                await conn.commit()
        except Exception as exc:
            logger.warning("Error escribiendo embedding cache: %s", exc)
