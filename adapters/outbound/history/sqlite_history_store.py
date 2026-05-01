"""
SQLiteHistoryStore — historial de conversación persistido en SQLite.

Un registro por mensaje: tabla `history` en data/history.db.
Solo se persisten mensajes user y assistant — nunca tool calls.

Schema:
  history     — una fila por mensaje con flag `infused` (0=pendiente, 1=procesado).
                Incluye columnas `channel` y `chat_id` para soportar múltiples
                canales y grupos dentro del mismo agente.
  agent_state — una fila por agente con el estado conversacional (sticky TTLs)
                serializado en JSON.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from core.domain.entities.message import Message, Role
from core.domain.value_objects.conversation_state import ConversationState
from core.ports.outbound.history_port import IHistoryStore
from infrastructure.config import ChatHistoryConfig

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    infused    INTEGER NOT NULL DEFAULT 0,
    channel    TEXT    NOT NULL DEFAULT '',
    chat_id    TEXT    NOT NULL DEFAULT ''
);
"""

# Índice principal: consultas filtradas por agente + canal + chat + posición.
_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_history_agent_channel
ON history(agent_id, channel, chat_id, id);
"""

_CREATE_INFUSED_INDEX = """
CREATE INDEX IF NOT EXISTS idx_history_uninfused ON history(agent_id, infused);
"""

# Índice secundario para consultas cross-agent por canal/chat (p. ej. auditoría).
_CREATE_CHANNEL_CHAT_INDEX = """
CREATE INDEX IF NOT EXISTS idx_history_channel_chat ON history(channel, chat_id);
"""

_CREATE_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS agent_state (
    agent_id   TEXT PRIMARY KEY,
    state_json TEXT NOT NULL
);
"""


def _build_where_filters(
    agent_id: str,
    channel: str | None = None,
    chat_id: str | None = None,
) -> tuple[str, tuple]:
    """
    Construye la cláusula WHERE para consultas de historial.

    Siempre filtra por ``agent_id``. Agrega filtros opcionales por ``channel``
    y ``chat_id`` cuando se proveen valores no-None.

    Retorna la cadena de condiciones y la tupla de parámetros para sqlite.
    """
    condiciones = ["agent_id = ?"]
    params: list = [agent_id]

    if channel is not None:
        condiciones.append("channel = ?")
        params.append(channel)

    if chat_id is not None:
        condiciones.append("chat_id = ?")
        params.append(chat_id)

    return " AND ".join(condiciones), tuple(params)


class SQLiteHistoryStore(IHistoryStore):
    def __init__(self, cfg: ChatHistoryConfig) -> None:
        self._db_path = cfg.db_filename
        self._max_n = cfg.max_messages
        Path(cfg.db_filename).parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            yield conn

    async def _ensure_schema(self, conn: aiosqlite.Connection) -> None:
        await conn.execute(_CREATE_TABLE)
        await conn.execute(_CREATE_INDEX)
        await conn.execute(_CREATE_INFUSED_INDEX)
        await conn.execute(_CREATE_CHANNEL_CHAT_INDEX)
        await conn.execute(_CREATE_STATE_TABLE)
        await conn.commit()

    async def append(
        self,
        agent_id: str,
        message: Message,
        channel: str = "",
        chat_id: str = "",
    ) -> int | None:
        if message.role not in (Role.USER, Role.ASSISTANT):
            return None

        if message.timestamp is None:
            message.timestamp = datetime.now(timezone.utc)

        ts = message.timestamp.isoformat()

        async with self._conn() as conn:
            await self._ensure_schema(conn)
            cursor = await conn.execute(
                "INSERT INTO history (agent_id, role, content, created_at, channel, chat_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (agent_id, message.role.value, message.content, ts, channel, chat_id),
            )
            await conn.commit()
            return cursor.lastrowid

    async def update_content(
        self,
        agent_id: str,
        message_id: int,
        new_content: str,
    ) -> bool:
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            cursor = await conn.execute(
                "UPDATE history SET content = ? WHERE agent_id = ? AND id = ?",
                (new_content, agent_id, message_id),
            )
            await conn.commit()
            return (cursor.rowcount or 0) > 0

    async def load(
        self,
        agent_id: str,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[Message]:
        filtros, params = _build_where_filters(agent_id, channel=channel, chat_id=chat_id)
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            if self._max_n > 0:
                rows = await conn.execute_fetchall(
                    f"SELECT role, content, created_at FROM history "
                    f"WHERE {filtros} "
                    f"ORDER BY id DESC LIMIT ?",
                    (*params, self._max_n),
                )
                return [self._row_to_message(r) for r in reversed(rows)]
            else:
                rows = await conn.execute_fetchall(
                    f"SELECT role, content, created_at FROM history "
                    f"WHERE {filtros} "
                    f"ORDER BY id ASC",
                    params,
                )
                return [self._row_to_message(r) for r in rows]

    async def load_full(self, agent_id: str) -> list[Message]:
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            rows = await conn.execute_fetchall(
                "SELECT role, content, created_at FROM history WHERE agent_id = ? ORDER BY id ASC",
                (agent_id,),
            )
        return [self._row_to_message(r) for r in rows]

    async def load_uninfused(
        self,
        agent_id: str,
        channels: list[str] | None = None,
    ) -> list[Message]:
        base_sql = (
            "SELECT role, content, created_at FROM history WHERE agent_id = ? AND infused = 0"
        )
        params: list = [agent_id]

        # Filtro opcional por canal: solo si la lista tiene al menos un elemento.
        if channels:
            placeholders = ", ".join("?" * len(channels))
            base_sql += f" AND channel IN ({placeholders})"
            params.extend(channels)

        base_sql += " ORDER BY id ASC"

        async with self._conn() as conn:
            await self._ensure_schema(conn)
            rows = await conn.execute_fetchall(base_sql, tuple(params))

        logger.info(
            "load_uninfused: db=%s agent_id=%r channels=%r encontrados=%d",
            self._db_path,
            agent_id,
            channels,
            len(rows),
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

    async def clear(
        self,
        agent_id: str,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            if channel is None and chat_id is None:
                # Limpieza total: history + agent_state (sticky TTLs).
                await conn.execute("DELETE FROM history WHERE agent_id = ?", (agent_id,))
                await conn.execute("DELETE FROM agent_state WHERE agent_id = ?", (agent_id,))
            else:
                # Limpieza scoped por (channel, chat_id). NO se toca agent_state:
                # los sticky skills/tools son per-agente, no per-chat.
                filtros, params = _build_where_filters(
                    agent_id, channel=channel, chat_id=chat_id
                )
                await conn.execute(f"DELETE FROM history WHERE {filtros}", params)
            await conn.commit()

    async def load_state(self, agent_id: str) -> ConversationState:
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            async with conn.execute(
                "SELECT state_json FROM agent_state WHERE agent_id = ?",
                (agent_id,),
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return ConversationState()

        try:
            data = json.loads(row["state_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "state_json corrupto para agente '%s' (%s) — se ignora y se parte de estado vacío",
                agent_id,
                exc,
            )
            return ConversationState()

        sticky_skills = {
            str(k): int(v)
            for k, v in (data.get("sticky_skills") or {}).items()
            if isinstance(v, int) and v > 0
        }
        sticky_tools = {
            str(k): int(v)
            for k, v in (data.get("sticky_tools") or {}).items()
            if isinstance(v, int) and v > 0
        }
        return ConversationState(sticky_skills=sticky_skills, sticky_tools=sticky_tools)

    async def save_state(self, agent_id: str, state: ConversationState) -> None:
        payload = json.dumps(
            {
                "sticky_skills": state.sticky_skills,
                "sticky_tools": state.sticky_tools,
            },
            ensure_ascii=False,
        )
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            await conn.execute(
                "INSERT INTO agent_state (agent_id, state_json) VALUES (?, ?) "
                "ON CONFLICT(agent_id) DO UPDATE SET state_json = excluded.state_json",
                (agent_id, payload),
            )
            await conn.commit()

    def _row_to_message(self, row: aiosqlite.Row) -> Message:
        return Message(
            role=Role(row["role"]),
            content=row["content"],
            timestamp=datetime.fromisoformat(row["created_at"]),
        )
