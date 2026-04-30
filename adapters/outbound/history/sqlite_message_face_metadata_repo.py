"""
SqliteMessageFaceMetadataRepo — side-table de metadata de caras en history.db.

Una fila por mensaje de foto (keyed por history_id). Persiste en la misma DB
que el historial de conversación (history.db) para reutilizar la conexión de la
app y simplificar backups.

Schema:
  message_face_metadata — una fila por mensaje con caras detectadas.
    - history_id       INTEGER PRIMARY KEY (FK lógica a history.id)
    - agent_id         TEXT NOT NULL
    - channel          TEXT NOT NULL
    - chat_id          TEXT NOT NULL
    - faces_json       TEXT NOT NULL (lista de FaceMatch serializada)
    - embeddings_blob  BLOB NOT NULL (numpy float32 arrays concatenados)
    - created_at       TEXT NOT NULL
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from core.domain.entities.face import BBox, FaceMatch, MatchStatus, MessageFaceMetadata
from core.domain.errors import FaceRegistryError
from core.ports.outbound.message_face_metadata_port import IMessageFaceMetadataRepo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS message_face_metadata (
    history_id      INTEGER PRIMARY KEY,
    agent_id        TEXT    NOT NULL,
    channel         TEXT    NOT NULL DEFAULT '',
    chat_id         TEXT    NOT NULL DEFAULT '',
    faces_json      TEXT    NOT NULL,
    embeddings_blob BLOB    NOT NULL,
    created_at      TEXT    NOT NULL
);
"""

_CREATE_INDEX_THREAD = """
CREATE INDEX IF NOT EXISTS idx_mfm_thread
ON message_face_metadata(agent_id, channel, chat_id, history_id DESC);
"""


# ---------------------------------------------------------------------------
# Serialización de FaceMatch
# ---------------------------------------------------------------------------


def _face_match_to_dict(fm: FaceMatch) -> dict:
    """Serializa un FaceMatch a dict JSON-serializable.

    Nota: ``candidates`` contiene tuplas (Person, float). Para simplificar el
    almacenamiento en JSON serializamos solo los campos esenciales de Person.
    Al deserializar reconstruimos un dict (no Person completo) — el repo no
    necesita el objeto completo, solo la referencia.
    """
    candidatos = []
    for persona, score in fm.candidates:
        candidatos.append(
            {
                "person": {
                    "id": persona.id,
                    "nombre": persona.nombre,
                    "apellido": persona.apellido,
                    "relacion": persona.relacion,
                    "categoria": persona.categoria,
                    "embeddings_count": persona.embeddings_count,
                },
                "score": score,
            }
        )
    return {
        "face_ref": fm.face_ref,
        "bbox": {"x": fm.bbox.x, "y": fm.bbox.y, "w": fm.bbox.w, "h": fm.bbox.h},
        "candidates": candidatos,
        "status": fm.status.value,
        "categoria": fm.categoria,
    }


def _dict_to_face_match(d: dict) -> FaceMatch:
    """Deserializa un dict JSON a FaceMatch (con Person simplificada)."""
    from core.domain.entities.face import Person  # importación local para evitar ciclos

    candidatos = []
    for c in d.get("candidates", []):
        pd = c["person"]
        persona = Person(
            id=pd["id"],
            nombre=pd.get("nombre"),
            apellido=pd.get("apellido"),
            relacion=pd.get("relacion"),
            categoria=pd.get("categoria"),
            embeddings_count=pd.get("embeddings_count", 0),
        )
        candidatos.append((persona, c["score"]))

    bbox_d = d["bbox"]
    return FaceMatch(
        face_ref=d["face_ref"],
        bbox=BBox(x=bbox_d["x"], y=bbox_d["y"], w=bbox_d["w"], h=bbox_d["h"]),
        candidates=candidatos,
        status=MatchStatus(d["status"]),
        categoria=d.get("categoria"),
    )


# ---------------------------------------------------------------------------
# Repositorio
# ---------------------------------------------------------------------------


class SqliteMessageFaceMetadataRepo(IMessageFaceMetadataRepo):
    """Repositorio de metadata de caras por mensaje, persistido en SQLite.

    Usa la misma DB que SQLiteHistoryStore (history.db) pero con una tabla
    separada. El constructor acepta ``db_path`` directamente para poder
    compartir el path sin acoplar las clases.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            yield conn

    async def _ensure_schema(self, conn: aiosqlite.Connection) -> None:
        await conn.execute(_CREATE_TABLE)
        await conn.execute(_CREATE_INDEX_THREAD)
        await conn.commit()

    # ------------------------------------------------------------------
    # save
    # ------------------------------------------------------------------

    async def save(self, metadata: MessageFaceMetadata) -> None:
        """Persiste la metadata de caras de un mensaje (upsert por history_id)."""
        faces_json = json.dumps(
            [_face_match_to_dict(fm) for fm in metadata.faces],
            ensure_ascii=False,
        )
        created_at_iso = metadata.created_at.isoformat()

        async with self._conn() as conn:
            await self._ensure_schema(conn)
            await conn.execute(
                """
                INSERT INTO message_face_metadata
                    (history_id, agent_id, channel, chat_id, faces_json, embeddings_blob, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(history_id) DO UPDATE SET
                    agent_id        = excluded.agent_id,
                    channel         = excluded.channel,
                    chat_id         = excluded.chat_id,
                    faces_json      = excluded.faces_json,
                    embeddings_blob = excluded.embeddings_blob,
                    created_at      = excluded.created_at
                """,
                (
                    metadata.history_id,
                    metadata.agent_id,
                    metadata.channel,
                    metadata.chat_id,
                    faces_json,
                    metadata.embeddings_blob,
                    created_at_iso,
                ),
            )
            await conn.commit()

        logger.debug(
            "message_face_metadata guardada: history_id=%d caras=%d",
            metadata.history_id,
            len(metadata.faces),
        )

    # ------------------------------------------------------------------
    # get_by_history_id
    # ------------------------------------------------------------------

    async def get_by_history_id(self, history_id: int) -> MessageFaceMetadata | None:
        """Recupera la metadata de caras para un mensaje específico."""
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            async with conn.execute(
                "SELECT * FROM message_face_metadata WHERE history_id = ?",
                (history_id,),
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return None

        return self._row_to_metadata(row)

    # ------------------------------------------------------------------
    # find_recent_for_thread
    # ------------------------------------------------------------------

    async def find_recent_for_thread(
        self,
        agent_id: str,
        channel: str,
        chat_id: str,
        limit: int = 10,
    ) -> list[MessageFaceMetadata]:
        """Recupera las N metadata más recientes para un hilo de conversación.

        Ordena por history_id DESC (proxy de tiempo de inserción).
        """
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            rows = await conn.execute_fetchall(
                """
                SELECT * FROM message_face_metadata
                WHERE agent_id = ? AND channel = ? AND chat_id = ?
                ORDER BY history_id DESC
                LIMIT ?
                """,
                (agent_id, channel, chat_id, limit),
            )

        return [self._row_to_metadata(row) for row in rows]

    # ------------------------------------------------------------------
    # resolve_face_ref
    # ------------------------------------------------------------------

    async def resolve_face_ref(
        self,
        agent_id: str,
        channel: str,
        chat_id: str,
        face_ref: str,
    ) -> tuple[MessageFaceMetadata, int] | None:
        """Resuelve un face_ref al par (metadata, face_idx) correspondiente.

        El face_ref tiene formato '{history_id}#{face_idx}'.
        """
        # Parsear face_ref
        partes = face_ref.split("#", 1)
        if len(partes) != 2:
            raise FaceRegistryError(
                f"face_ref inválido: '{face_ref}'. Formato esperado: '{{history_id}}#{{face_idx}}'"
            )

        try:
            history_id = int(partes[0])
            face_idx = int(partes[1])
        except ValueError:
            raise FaceRegistryError(
                f"face_ref inválido: '{face_ref}'. history_id y face_idx deben ser enteros."
            )

        # Buscar metadata (con scope de seguridad)
        async with self._conn() as conn:
            await self._ensure_schema(conn)
            async with conn.execute(
                """
                SELECT * FROM message_face_metadata
                WHERE history_id = ? AND agent_id = ? AND channel = ? AND chat_id = ?
                """,
                (history_id, agent_id, channel, chat_id),
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return None

        metadata = self._row_to_metadata(row)

        # Validar que el face_idx está en rango
        if face_idx < 0 or face_idx >= len(metadata.faces):
            logger.warning(
                "resolve_face_ref: face_idx=%d fuera de rango (caras=%d) en history_id=%d",
                face_idx,
                len(metadata.faces),
                history_id,
            )
            return None

        return metadata, face_idx

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _row_to_metadata(self, row: aiosqlite.Row) -> MessageFaceMetadata:
        """Convierte una fila SQLite a MessageFaceMetadata."""
        faces_raw = json.loads(row["faces_json"])
        faces = [_dict_to_face_match(d) for d in faces_raw]

        return MessageFaceMetadata(
            history_id=row["history_id"],
            agent_id=row["agent_id"],
            channel=row["channel"],
            chat_id=row["chat_id"],
            faces=faces,
            embeddings_blob=bytes(row["embeddings_blob"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
