"""SqliteTelegramFileRepo — persistencia de TelegramFileRecord en DB dedicada.

DB separada de ``history.db`` para no contaminar el historial de conversación
con metadata de transporte. Path típico: ``~/.inaki/data/telegram_files.db``.

Schema:
  telegram_files — una fila por fichero recibido vía Telegram.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import aiosqlite

from core.domain.value_objects.telegram_file import (
    DownloadableContentType,
    FileContentType,
    TelegramFileRecord,
)
from core.ports.outbound.telegram_file_repo_port import IFileRecordRepo

logger = logging.getLogger(__name__)


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS telegram_files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        TEXT    NOT NULL,
    channel         TEXT    NOT NULL,
    chat_id         TEXT    NOT NULL,
    content_type    TEXT    NOT NULL,
    file_id         TEXT    NOT NULL,
    file_unique_id  TEXT    NOT NULL,
    media_group_id  TEXT,
    caption         TEXT,
    history_id      INTEGER,
    mime_type       TEXT,
    received_at     TEXT    NOT NULL
);
"""

_CREATE_INDEX_CHAT = """
CREATE INDEX IF NOT EXISTS idx_telegram_files_chat
ON telegram_files(agent_id, channel, chat_id, received_at DESC);
"""

_CREATE_INDEX_ALBUM = """
CREATE INDEX IF NOT EXISTS idx_telegram_files_album
ON telegram_files(media_group_id) WHERE media_group_id IS NOT NULL;
"""

_CREATE_INDEX_UNIQUE = """
CREATE INDEX IF NOT EXISTS idx_telegram_files_unique
ON telegram_files(file_unique_id);
"""


class SqliteTelegramFileRepo(IFileRecordRepo):
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(Path(db_path).expanduser())
        self._schema_ready = False

    def _connect(self):
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        return aiosqlite.connect(self._db_path)

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._connect() as db:
            await db.execute(_CREATE_TABLE)
            await db.execute(_CREATE_INDEX_CHAT)
            await db.execute(_CREATE_INDEX_ALBUM)
            await db.execute(_CREATE_INDEX_UNIQUE)
            await db.commit()
        self._schema_ready = True
        logger.debug("telegram_files.db schema ready: %s", self._db_path)

    async def save(self, record: TelegramFileRecord) -> None:
        await self.ensure_schema()
        # received_at se valida UTC en el value object — almacenamos ISO con offset.
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO telegram_files (
                    agent_id, channel, chat_id, content_type, file_id,
                    file_unique_id, media_group_id, caption, history_id,
                    mime_type, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.agent_id,
                    record.channel,
                    record.chat_id,
                    record.content_type,
                    record.file_id,
                    record.file_unique_id,
                    record.media_group_id,
                    record.caption,
                    record.history_id,
                    record.mime_type,
                    record.received_at.isoformat(),
                ),
            )
            await db.commit()

    async def query_recent(
        self,
        *,
        agent_id: str,
        channel: str,
        chat_id: str,
        content_type: DownloadableContentType,
        count: int,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[TelegramFileRecord]:
        await self.ensure_schema()

        if count <= 0:
            return []

        # Validar tz-aware si vienen
        for label, dt in (("since", since), ("until", until)):
            if dt is not None and dt.tzinfo is None:
                raise ValueError(f"{label} debe ser timezone-aware (UTC)")

        if content_type == "album":
            return await self._query_albums(
                agent_id=agent_id,
                channel=channel,
                chat_id=chat_id,
                count=count,
                since=since,
                until=until,
            )

        return await self._query_simple(
            agent_id=agent_id,
            channel=channel,
            chat_id=chat_id,
            content_type=cast(FileContentType, content_type),
            count=count,
            since=since,
            until=until,
        )

    async def _query_simple(
        self,
        *,
        agent_id: str,
        channel: str,
        chat_id: str,
        content_type: FileContentType,
        count: int,
        since: datetime | None,
        until: datetime | None,
    ) -> list[TelegramFileRecord]:
        sql = [
            "SELECT * FROM telegram_files",
            "WHERE agent_id=? AND channel=? AND chat_id=? AND content_type=?",
        ]
        params: list = [agent_id, channel, chat_id, content_type]

        # Para 'photo' excluimos miembros de álbumes (van por el path 'album')
        if content_type == "photo":
            sql.append("AND media_group_id IS NULL")

        if since is not None:
            sql.append("AND received_at >= ?")
            params.append(since.isoformat())
        if until is not None:
            sql.append("AND received_at <= ?")
            params.append(until.isoformat())

        sql.append("ORDER BY received_at DESC LIMIT ?")
        params.append(count)

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(" ".join(sql), params)
            rows = await cursor.fetchall()
        return [_row_to_record(row) for row in rows]

    async def _query_albums(
        self,
        *,
        agent_id: str,
        channel: str,
        chat_id: str,
        count: int,
        since: datetime | None,
        until: datetime | None,
    ) -> list[TelegramFileRecord]:
        """Devuelve hasta ``count`` archivos de álbumes, álbum-por-álbum del más reciente.

        Estrategia: identificamos los ``media_group_id`` ordenados por
        ``MAX(received_at) DESC`` que cumplen los filtros, y para cada uno
        traemos sus miembros hasta llenar ``count`` archivos totales.
        """
        sql = [
            "SELECT media_group_id, MAX(received_at) AS last_at",
            "FROM telegram_files",
            "WHERE agent_id=? AND channel=? AND chat_id=?",
            "AND content_type='photo' AND media_group_id IS NOT NULL",
        ]
        params: list = [agent_id, channel, chat_id]
        if since is not None:
            sql.append("AND received_at >= ?")
            params.append(since.isoformat())
        if until is not None:
            sql.append("AND received_at <= ?")
            params.append(until.isoformat())
        sql.append("GROUP BY media_group_id ORDER BY last_at DESC")

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(" ".join(sql), params)
            grupos = await cursor.fetchall()

            resultado: list[TelegramFileRecord] = []
            for grupo in grupos:
                if len(resultado) >= count:
                    break
                cursor = await db.execute(
                    """
                    SELECT * FROM telegram_files
                    WHERE agent_id=? AND channel=? AND chat_id=?
                      AND content_type='photo' AND media_group_id=?
                    ORDER BY received_at ASC
                    """,
                    (agent_id, channel, chat_id, grupo["media_group_id"]),
                )
                miembros = await cursor.fetchall()
                for fila in miembros:
                    resultado.append(_row_to_record(fila))
                    if len(resultado) >= count:
                        break
        return resultado


def _row_to_record(row) -> TelegramFileRecord:
    received = datetime.fromisoformat(row["received_at"])
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    return TelegramFileRecord(
        agent_id=row["agent_id"],
        channel=row["channel"],
        chat_id=row["chat_id"],
        content_type=row["content_type"],
        file_id=row["file_id"],
        file_unique_id=row["file_unique_id"],
        media_group_id=row["media_group_id"],
        caption=row["caption"],
        history_id=row["history_id"],
        mime_type=row["mime_type"],
        received_at=received,
    )
