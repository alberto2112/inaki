"""
SQLiteHistoryStore — historial de conversación persistido en SQLite.

Un registro por mensaje: tabla `history` en data/history.db.
Se persisten mensajes user y assistant siempre; los mensajes tool (par
assistant+tool_calls ↔ tool_results) solo cuando el agente activa
`chat_history.persist_tool_calls` (ver nota de migración `persist-tool-calls`).

Schema:
  history     — una fila por mensaje con flag `infused` (0=pendiente, 1=procesado).
                Incluye columnas `channel` y `chat_id` para múltiples canales/grupos,
                y `tool_calls`/`tool_call_id` (NULL salvo en el rastro de tool calls).
  agent_state — una fila por agente con el estado conversacional (sticky TTLs)
                serializado en JSON.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from core.domain.entities.message import Message, Role
from core.domain.value_objects.conversation_state import ConversationState
from core.ports.outbound.history_port import IHistoryStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoryStoreSettings:
    """Settings VO del store — el container lo mapea desde ``ChatHistoryConfig``.

    El adapter declara lo que necesita (path ya resuelto + límite de mensajes)
    sin conocer el schema YAML de infrastructure. ``merge_chats`` no viaja acá:
    es semántica del use case (``MemorySettings``), no del storage.
    """

    db_filename: str
    max_messages: int = 0  # 0 = sin límite; N = últimos N mensajes al LLM


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id     TEXT    NOT NULL,
    role         TEXT    NOT NULL,
    content      TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    infused      INTEGER NOT NULL DEFAULT 0,
    channel      TEXT    NOT NULL DEFAULT '',
    chat_id      TEXT    NOT NULL DEFAULT '',
    tool_calls   TEXT,
    tool_call_id TEXT
);
"""

# Roles que SÍ se persisten. TOOL entra con persist-tool-calls; el resto
# (SYSTEM, TOOL_RESULT) nunca toca la DB.
_PERSISTED_ROLES = (Role.USER, Role.ASSISTANT, Role.TOOL)

# Lista de columnas de los SELECT de mensajes (incluye las de persist-tool-calls).
_COLS = "role, content, created_at, channel, chat_id, tool_calls, tool_call_id"

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
    agent_id   TEXT    NOT NULL,
    channel    TEXT    NOT NULL DEFAULT '',
    chat_id    TEXT    NOT NULL DEFAULT '',
    state_json TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    PRIMARY KEY (agent_id, channel, chat_id)
);
"""


def _escape_like(text: str) -> str:
    """Escapa los comodines de ``LIKE`` (``%`` ``_``) y el propio ``\\`` para
    tratar la query como literal. Se usa junto a ``ESCAPE '\\'`` en el SQL."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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


def _drop_orphan_tool_messages(messages: list[Message]) -> list[Message]:
    """Descarta mensajes ``role=tool`` huérfanos — los que quedaron sin su
    mensaje ``assistant``+tool_calls dentro de la ventana cargada.

    Es la garantía de correctitud del feature persist-tool-calls: cuando ``load``
    recorta a los últimos ``max_messages`` (o cuando ``trim`` borra por scope), un
    tool result puede quedar sin el assistant que lo originó. Mandarlo así al
    provider dispara un 400 (OpenAI y Anthropic exigen que cada ``tool_call_id``
    tenga su assistant emparejado). Escaneo lineal: un ``role=tool`` solo es
    válido si venimos "dentro de un grupo" abierto por un assistant con
    tool_calls; un USER o un ASSISTANT sin tool_calls cierra el grupo. Cubre
    huérfanos al inicio Y en el medio de la ventana.

    No-op cuando no hay mensajes ``role=tool`` (persist-tool-calls desactivado).
    """
    result: list[Message] = []
    in_group = False
    for m in messages:
        if m.role == Role.TOOL:
            if in_group:
                result.append(m)
            # else: huérfano (su assistant quedó fuera de la ventana) → se descarta.
            continue
        # Un assistant con tool_calls abre grupo; cualquier otro mensaje lo cierra.
        in_group = m.role == Role.ASSISTANT and bool(m.tool_calls)
        result.append(m)
    return result


class SQLiteHistoryStore(IHistoryStore):
    def __init__(self, cfg: HistoryStoreSettings) -> None:
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
        await self._ensure_history_columns(conn)
        await conn.execute(_CREATE_INDEX)
        await conn.execute(_CREATE_INFUSED_INDEX)
        await conn.execute(_CREATE_CHANNEL_CHAT_INDEX)
        await self._ensure_agent_state_schema(conn)
        await conn.commit()

    async def _ensure_history_columns(self, conn: aiosqlite.Connection) -> None:
        """Migra en caliente la tabla ``history`` agregando las columnas del feature
        persist-tool-calls (``tool_calls``, ``tool_call_id``) si faltan.

        Idempotente: detecta las columnas existentes vía ``PRAGMA table_info`` y
        solo aplica el ``ALTER TABLE ADD COLUMN`` de las que falten. Las DBs
        creadas por ``_CREATE_TABLE`` ya las traen; solo las viejas necesitan el
        ALTER. Las filas preexistentes quedan con ``NULL`` (mensajes de texto).
        """
        rows = await conn.execute_fetchall("PRAGMA table_info(history)")
        existing = {r["name"] for r in rows}
        for column in ("tool_calls", "tool_call_id"):
            if column not in existing:
                await conn.execute(f"ALTER TABLE history ADD COLUMN {column} TEXT")
                logger.info("Migración history: columna '%s' agregada (persist-tool-calls)", column)

    async def _ensure_agent_state_schema(self, conn: aiosqlite.Connection) -> None:
        """Crea o migra la tabla agent_state al schema scoped por (channel, chat_id).

        Detecta la versión legacy (PK = agent_id solo, sin columnas channel/chat_id)
        y la migra en caliente sin pérdida de datos — el estado existente se preserva
        como scope (agent_id, '', '') para mantener compatibilidad.
        """
        rows = await conn.execute_fetchall("PRAGMA table_info(agent_state)")
        column_names = {r["name"] for r in rows}

        if not rows:
            # Tabla no existe aún → CREATE la nueva directamente.
            await conn.execute(_CREATE_STATE_TABLE)
            return

        if "channel" in column_names:
            # Ya tiene el schema nuevo, nada que hacer.
            return

        # Schema legacy detectado: migrar preservando los registros existentes.
        logger.info("Migrando agent_state al schema scoped (channel, chat_id, updated_at)…")
        await conn.execute("ALTER TABLE agent_state RENAME TO agent_state_legacy")
        await conn.execute(_CREATE_STATE_TABLE)
        await conn.execute(
            """
            INSERT OR IGNORE INTO agent_state (agent_id, channel, chat_id, state_json, updated_at)
            SELECT agent_id, '', '', state_json, datetime('now')
            FROM agent_state_legacy
            """
        )
        await conn.execute("DROP TABLE agent_state_legacy")
        logger.info("Migración agent_state completada.")

    async def append(
        self,
        agent_id: str,
        message: Message,
        channel: str = "",
        chat_id: str = "",
    ) -> int | None:
        if message.role not in _PERSISTED_ROLES:
            return None

        if message.timestamp is None:
            message.timestamp = datetime.now(timezone.utc)

        ts = message.timestamp.isoformat()
        # tool_calls viaja como JSON; NULL cuando el mensaje no lleva (texto normal).
        tool_calls_json = (
            json.dumps(message.tool_calls, ensure_ascii=False) if message.tool_calls else None
        )

        async with self._conn() as conn:
            await self._ensure_schema(conn)
            cursor = await conn.execute(
                "INSERT INTO history "
                "(agent_id, role, content, created_at, channel, chat_id, tool_calls, tool_call_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    agent_id,
                    message.role.value,
                    message.content,
                    ts,
                    channel,
                    chat_id,
                    tool_calls_json,
                    message.tool_call_id,
                ),
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
                rows = list(
                    await conn.execute_fetchall(
                        f"SELECT {_COLS} FROM history WHERE {filtros} ORDER BY id DESC LIMIT ?",
                        (*params, self._max_n),
                    )
                )
                msgs = [self._row_to_message(r) for r in reversed(rows)]
            else:
                rows = list(
                    await conn.execute_fetchall(
                        f"SELECT {_COLS} FROM history WHERE {filtros} ORDER BY id ASC",
                        params,
                    )
                )
                msgs = [self._row_to_message(r) for r in rows]
        return _drop_orphan_tool_messages(msgs)

    async def load_full(self, agent_id: str) -> list[Message]:
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            rows = await conn.execute_fetchall(
                f"SELECT {_COLS} FROM history WHERE agent_id = ? ORDER BY id ASC",
                (agent_id,),
            )
        return [self._row_to_message(r) for r in rows]

    async def search(
        self,
        agent_id: str,
        query: str | None = None,
        role: str | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        limit: int = 20,
    ) -> list[Message]:
        # Reusa el builder de filtros base (agent_id obligatorio + channel/chat_id
        # opcionales) y le agrega el filtro de texto y rol propios de la búsqueda.
        filtros, params_base = _build_where_filters(agent_id, channel=channel, chat_id=chat_id)
        condiciones = [filtros]
        params: list = list(params_base)

        if query:
            # Escapamos los comodines para tratar la query como literal: buscar
            # "50%" no debe matchear todo. ESCAPE define el carácter de escape.
            condiciones.append("content LIKE ? ESCAPE '\\'")
            params.append(f"%{_escape_like(query)}%")

        if role:
            condiciones.append("role = ?")
            params.append(role)
        else:
            # Sin filtro de rol explícito ocultamos el plumbing de tool calls
            # (mensajes role=tool de persist-tool-calls): una búsqueda de texto es
            # para la conversación humana, no para el rastro de herramientas.
            condiciones.append("role != ?")
            params.append(Role.TOOL.value)

        where = " AND ".join(condiciones)
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            rows = await conn.execute_fetchall(
                f"SELECT {_COLS} FROM history WHERE {where} ORDER BY id DESC LIMIT ?",
                (*params, max(1, limit)),
            )
        # DESC en el SQL → más recientes primero (lo que se quiere en una búsqueda).
        return [self._row_to_message(r) for r in rows]

    async def load_uninfused(
        self,
        agent_id: str,
        channels: list[str] | None = None,
    ) -> list[Message]:
        base_sql = f"SELECT {_COLS} FROM history WHERE agent_id = ? AND infused = 0"
        params: list = [agent_id]

        # Filtro opcional por canal: solo si la lista tiene al menos un elemento.
        if channels:
            placeholders = ", ".join("?" * len(channels))
            base_sql += f" AND channel IN ({placeholders})"
            params.extend(channels)

        base_sql += " ORDER BY id ASC"

        async with self._conn() as conn:
            await self._ensure_schema(conn)
            rows = list(await conn.execute_fetchall(base_sql, tuple(params)))

        logger.info(
            "load_uninfused: db=%s agent_id=%r channels=%r encontrados=%d",
            self._db_path,
            agent_id,
            channels,
            len(rows),
        )
        return [self._row_to_message(r) for r in rows]

    async def mark_infused(
        self,
        agent_id: str,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> int:
        # Construir el WHERE dinámicamente.
        # Para channel/chat_id con valor None se usa IS NULL (matchea NULLs en la BD).
        # Para valores no-None se usa = ? (matchea el valor exacto).
        # Nunca se omite el filtro — None NO significa "sin filtro".
        sql = "UPDATE history SET infused = 1 WHERE agent_id = ? AND infused = 0"
        params: list = [agent_id]

        if channel is None:
            sql += " AND channel IS NULL"
        else:
            sql += " AND channel = ?"
            params.append(channel)

        if chat_id is None:
            sql += " AND chat_id IS NULL"
        else:
            sql += " AND chat_id = ?"
            params.append(chat_id)

        async with self._conn() as conn:
            await self._ensure_schema(conn)
            cursor = await conn.execute(sql, tuple(params))
            await conn.commit()
            if cursor.rowcount > 0:
                logger.info(
                    "Historial de '%s' scope=(channel=%r, chat_id=%r): %d mensaje(s) marcado(s) como infused",
                    agent_id,
                    channel,
                    chat_id,
                    cursor.rowcount,
                )
            return cursor.rowcount or 0

    async def trim(self, agent_id: str, keep_last: int) -> None:
        if keep_last <= 0:
            return
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            # ROW_NUMBER() OVER (PARTITION BY channel, chat_id …) garantiza que
            # se preservan los últimos `keep_last` mensajes POR SCOPE. Sin esto,
            # el LIMIT global dejaba vacíos los chats menos activos.
            cursor = await conn.execute(
                """
                DELETE FROM history
                WHERE agent_id = ?
                  AND id NOT IN (
                    SELECT id FROM (
                      SELECT id,
                             ROW_NUMBER() OVER (
                               PARTITION BY channel, chat_id
                               ORDER BY id DESC
                             ) AS rn
                      FROM history
                      WHERE agent_id = ?
                    )
                    WHERE rn <= ?
                  )
                """,
                (agent_id, agent_id, keep_last),
            )
            await conn.commit()
            if cursor.rowcount > 0:
                logger.info(
                    "Historial de '%s' truncado: %d fila(s) borrada(s), últimas %d por scope preservadas",
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
                # Limpieza total: history + todos los agent_state del agente.
                await conn.execute("DELETE FROM history WHERE agent_id = ?", (agent_id,))
                await conn.execute("DELETE FROM agent_state WHERE agent_id = ?", (agent_id,))
            else:
                # Limpieza scoped: history + agent_state del scope (channel, chat_id).
                filtros, params = _build_where_filters(agent_id, channel=channel, chat_id=chat_id)
                await conn.execute(f"DELETE FROM history WHERE {filtros}", params)
                await conn.execute(
                    "DELETE FROM agent_state WHERE agent_id = ? AND channel = ? AND chat_id = ?",
                    (agent_id, channel or "", chat_id or ""),
                )
            await conn.commit()

    async def load_state(
        self,
        agent_id: str,
        channel: str = "",
        chat_id: str = "",
    ) -> ConversationState:
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            async with conn.execute(
                "SELECT state_json FROM agent_state "
                "WHERE agent_id = ? AND channel = ? AND chat_id = ?",
                (agent_id, channel, chat_id),
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return ConversationState()

        try:
            data = json.loads(row["state_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "state_json corrupto para agente '%s' scope=(%r, %r) (%s) — estado vacío",
                agent_id,
                channel,
                chat_id,
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

    async def save_state(
        self,
        agent_id: str,
        state: ConversationState,
        channel: str = "",
        chat_id: str = "",
    ) -> None:
        payload = json.dumps(
            {
                "sticky_skills": state.sticky_skills,
                "sticky_tools": state.sticky_tools,
            },
            ensure_ascii=False,
        )
        updated_at = datetime.now(timezone.utc).isoformat()
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            await conn.execute(
                "INSERT INTO agent_state (agent_id, channel, chat_id, state_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(agent_id, channel, chat_id) DO UPDATE SET "
                "state_json = excluded.state_json, updated_at = excluded.updated_at",
                (agent_id, channel, chat_id, payload, updated_at),
            )
            await conn.commit()

    def _row_to_message(self, row: aiosqlite.Row) -> Message:
        # ``channel``/``chat_id`` pueden no estar presentes en el SELECT (algunas
        # queries internas no los pidan). Defensivo contra eso.
        try:
            channel = row["channel"]
        except (KeyError, IndexError):
            channel = None
        try:
            chat_id = row["chat_id"]
        except (KeyError, IndexError):
            chat_id = None
        # Columnas de persist-tool-calls: NULL en filas viejas / mensajes de texto.
        # Defensivo contra SELECTs internos que no las pidan (mismo criterio que
        # channel/chat_id). ``tool_calls`` se deserializa de JSON a list[dict].
        try:
            tool_calls_raw = row["tool_calls"]
        except (KeyError, IndexError):
            tool_calls_raw = None
        try:
            tool_call_id = row["tool_call_id"]
        except (KeyError, IndexError):
            tool_call_id = None
        tool_calls = None
        if tool_calls_raw:
            try:
                tool_calls = json.loads(tool_calls_raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning("tool_calls corrupto en history (role=%s) — ignorado", row["role"])
        return Message(
            role=Role(row["role"]),
            content=row["content"],
            timestamp=datetime.fromisoformat(row["created_at"]),
            channel=channel,
            chat_id=chat_id,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
        )
