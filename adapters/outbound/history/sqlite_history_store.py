"""
SQLiteHistoryStore — historial de conversación persistido en SQLite.

Un registro por mensaje: tabla `history` en data/history.db.
Solo se persisten mensajes user y assistant — nunca tool calls.

Schema:
  history — una fila por mensaje, con soft-delete para archive
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from core.domain.entities.message import Message, Role
from core.domain.errors import HistoryError
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
    archived   INTEGER NOT NULL DEFAULT 0
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_history_agent ON history(agent_id, archived);
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
                    "WHERE agent_id = ? AND archived = 0 "
                    "ORDER BY id DESC LIMIT ?",
                    (agent_id, self._max_n),
                )
                return [self._row_to_message(r) for r in reversed(rows)]
            else:
                rows = await conn.execute_fetchall(
                    "SELECT role, content, created_at FROM history "
                    "WHERE agent_id = ? AND archived = 0 "
                    "ORDER BY id ASC",
                    (agent_id,),
                )
                return [self._row_to_message(r) for r in rows]

    async def load_full(self, agent_id: str) -> list[Message]:
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            rows = await conn.execute_fetchall(
                "SELECT role, content, created_at FROM history "
                "WHERE agent_id = ? AND archived = 0 "
                "ORDER BY id ASC",
                (agent_id,),
            )
        return [self._row_to_message(r) for r in rows]

    async def archive(self, agent_id: str) -> str:
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            cursor = await conn.execute(
                "UPDATE history SET archived = 1 WHERE agent_id = ? AND archived = 0",
                (agent_id,),
            )
            await conn.commit()
            if cursor.rowcount == 0:
                raise HistoryError(f"No hay historial activo para '{agent_id}'")

        logger.info("Historial de '%s' archivado (%d filas)", agent_id, cursor.rowcount)
        return f"Historial de '{agent_id}' archivado."

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
