"""
SQLiteHistoryStore — historial de conversación persistido en SQLite.

Un registro por mensaje: tabla `history` en data/history.db.
Solo se persisten mensajes user y assistant — nunca tool calls.

Schema:
  history — una fila por mensaje con flag `infused` (0=pendiente de extracción,
  1=ya procesado por el extractor de recuerdos).

La columna `archived` del schema original es legacy (venía de un soft-delete
que se eliminó cuando la consolidación pasó a usar `trim` en vez de
archive+clear). Se mantiene en CREATE TABLE IF NOT EXISTS para no romper
DBs existentes, pero ninguna query de esta clase la usa.

La columna `infused` fue añadida en una migración posterior para evitar que
el extractor re-procesara los mensajes que siguen vivos en el buffer tras el
trim. DBs preexistentes reciben la columna vía ALTER TABLE ADD COLUMN en
`_ensure_schema`, y todos los mensajes ya presentes quedan marcados con
`infused=1` durante la migración (asumimos que forman parte de un estado
estable previo al cambio).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from core.domain.entities.message import Message, Role
from core.ports.outbound.history_port import IHistoryStore
from infrastructure.config import HistoryConfig

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    archived   INTEGER NOT NULL DEFAULT 0,
    infused    INTEGER NOT NULL DEFAULT 0
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_history_agent ON history(agent_id, id);
"""

_CREATE_INFUSED_INDEX = """
CREATE INDEX IF NOT EXISTS idx_history_uninfused ON history(agent_id, infused);
"""


class SQLiteHistoryStore(IHistoryStore):

    def __init__(self, cfg: HistoryConfig) -> None:
        self._db_path = cfg.db_path
        self._max_n = cfg.max_messages_in_prompt
        Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            yield conn

    async def _ensure_schema(self, conn: aiosqlite.Connection) -> None:
        await conn.execute(_CREATE_TABLE)
        await conn.execute(_CREATE_INDEX)

        # Migración: añadir columna `infused` si la DB viene de una versión
        # previa al cambio. Se marca todo lo existente como infused=1 para
        # no reprocesar mensajes ya vividos (asumimos estado estable).
        cursor = await conn.execute("PRAGMA table_info(history)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "infused" not in cols:
            logger.info(
                "Migrando tabla history: añadiendo columna `infused` y marcando "
                "todas las filas existentes como infused=1"
            )
            await conn.execute(
                "ALTER TABLE history ADD COLUMN infused INTEGER NOT NULL DEFAULT 0"
            )
            await conn.execute("UPDATE history SET infused = 1")

        await conn.execute(_CREATE_INFUSED_INDEX)
        await conn.commit()

    async def append(self, agent_id: str, message: Message) -> None:
        if message.role not in (Role.USER, Role.ASSISTANT):
            return

        if message.timestamp is None:
            message.timestamp = datetime.now(timezone.utc)

        ts = message.timestamp.isoformat()

        async with self._conn() as conn:
            await self._ensure_schema(conn)
            await conn.execute(
                "INSERT INTO history (agent_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (agent_id, message.role.value, message.content, ts),
            )
            await conn.commit()

    async def load(self, agent_id: str) -> list[Message]:
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            if self._max_n > 0:
                rows = await conn.execute_fetchall(
                    "SELECT role, content, created_at FROM history "
                    "WHERE agent_id = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (agent_id, self._max_n),
                )
                return [self._row_to_message(r) for r in reversed(rows)]
            else:
                rows = await conn.execute_fetchall(
                    "SELECT role, content, created_at FROM history "
                    "WHERE agent_id = ? "
                    "ORDER BY id ASC",
                    (agent_id,),
                )
                return [self._row_to_message(r) for r in rows]

    async def load_full(self, agent_id: str) -> list[Message]:
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            rows = await conn.execute_fetchall(
                "SELECT role, content, created_at FROM history "
                "WHERE agent_id = ? "
                "ORDER BY id ASC",
                (agent_id,),
            )
        return [self._row_to_message(r) for r in rows]

    async def load_uninfused(self, agent_id: str) -> list[Message]:
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            rows = await conn.execute_fetchall(
                "SELECT role, content, created_at FROM history "
                "WHERE agent_id = ? AND infused = 0 "
                "ORDER BY id ASC",
                (agent_id,),
            )
        return [self._row_to_message(r) for r in rows]

    async def mark_infused(self, agent_id: str) -> int:
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            cursor = await conn.execute(
                "UPDATE history SET infused = 1 WHERE agent_id = ? AND infused = 0",
                (agent_id,),
            )
            await conn.commit()
            if cursor.rowcount > 0:
                logger.info(
                    "Historial de '%s': %d mensaje(s) marcado(s) como infused",
                    agent_id,
                    cursor.rowcount,
                )
            return cursor.rowcount or 0

    async def trim(self, agent_id: str, keep_last: int) -> None:
        if keep_last <= 0:
            return
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            cursor = await conn.execute(
                """
                DELETE FROM history
                WHERE agent_id = ?
                  AND id NOT IN (
                    SELECT id FROM history
                    WHERE agent_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                  )
                """,
                (agent_id, agent_id, keep_last),
            )
            await conn.commit()
            if cursor.rowcount > 0:
                logger.info(
                    "Historial de '%s' truncado: %d fila(s) borrada(s), últimas %d preservadas",
                    agent_id,
                    cursor.rowcount,
                    keep_last,
                )

    async def clear(self, agent_id: str) -> None:
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            await conn.execute("DELETE FROM history WHERE agent_id = ?", (agent_id,))
            await conn.commit()

    def _row_to_message(self, row: aiosqlite.Row) -> Message:
        ts: datetime | None = None
        try:
            ts = datetime.fromisoformat(row["created_at"])
        except (ValueError, TypeError):
            pass
        return Message(
            role=Role(row["role"]),
            content=row["content"],
            timestamp=ts,
        )
