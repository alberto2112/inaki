"""
SqliteFaceRegistryAdapter — registro de personas conocidas con búsqueda vectorial.

Persiste en ~/.inaki/data/faces.db. Schema:

  schema_meta          — una fila con metadatos del schema (dimensión del modelo, etc.)
  persons              — una fila por persona conocida (nombre, categoria, etc.)
  person_embeddings    — embeddings individuales (fuente de verdad)
  person_embeddings_vec — tabla virtual vec0 para búsqueda KNN por cosine similarity

La dimensión de embedding se verifica al arrancar contra el valor guardado en
schema_meta. Si no coincide, se lanza EmbeddingDimensionMismatchError.
"""

from __future__ import annotations

import logging
import struct
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import aiosqlite
import numpy as np
import sqlite_vec

from core.domain.entities.face import BBox, FaceMatch, MatchStatus, Person
from core.domain.errors import EmbeddingDimensionMismatchError, FaceRegistryError
from core.ports.outbound.face_registry_port import IFaceRegistryPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_SCHEMA_META = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_CREATE_PERSONS = """
CREATE TABLE IF NOT EXISTS persons (
    id               TEXT    PRIMARY KEY,
    nombre           TEXT,
    apellido         TEXT,
    fecha_nacimiento TEXT,
    relacion         TEXT,
    notes            TEXT,
    categoria        TEXT    DEFAULT NULL,
    embeddings_count INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL
);
"""

# Índice para list_persons — consultas frecuentes filtradas por categoria
_CREATE_PERSONS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_persons_categoria
ON persons(categoria);
"""

_CREATE_PERSON_EMBEDDINGS = """
CREATE TABLE IF NOT EXISTS person_embeddings (
    id                TEXT    PRIMARY KEY,
    person_id         TEXT    NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    source_history_id INTEGER,
    source_face_ref   TEXT,
    created_at        TEXT    NOT NULL
);
"""

_CREATE_PERSON_EMBEDDINGS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_pemb_person
ON person_embeddings(person_id);
"""

# Tabla virtual vec0 para búsqueda KNN.
# La dimensión se inyecta dinámicamente desde embedding_dim.
_CREATE_VEC_TABLE_TEMPLATE = """
CREATE VIRTUAL TABLE IF NOT EXISTS person_embeddings_vec USING vec0(
    embedding_id TEXT PRIMARY KEY,
    embedding    FLOAT[{dim}]
);
"""


# ---------------------------------------------------------------------------
# Helpers de serialización
# ---------------------------------------------------------------------------


def _pack_vec(embedding: np.ndarray) -> bytes:
    """Serializa un numpy float32 array como bytes (formato sqlite-vec)."""
    arr = embedding.astype(np.float32)
    return struct.pack(f"{len(arr)}f", *arr)


def _row_to_person(row: aiosqlite.Row) -> Person:
    return Person(
        id=row["id"],
        nombre=row["nombre"],
        apellido=row["apellido"],
        fecha_nacimiento=row["fecha_nacimiento"],
        relacion=row["relacion"],
        notes=row["notes"],
        categoria=row["categoria"],
        embeddings_count=row["embeddings_count"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


# ---------------------------------------------------------------------------
# Adaptador
# ---------------------------------------------------------------------------


class SqliteFaceRegistryAdapter(IFaceRegistryPort):
    """Registro de personas conocidas con búsqueda vectorial vía sqlite-vec.

    No usa ``aiosqlite.connect()`` directamente en ``__init__`` — la conexión
    se abre por operación via ``_conn()``. El método ``initialize()`` debe ser
    llamado explícitamente antes de usar el adaptador (patrón async-init).
    """

    def __init__(self, db_path: str, embedding_dim: int = 512) -> None:
        self._db_path = db_path
        self._embedding_dim = embedding_dim
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[aiosqlite.Connection]:
        """Context manager que abre, carga sqlite-vec y cierra la conexión."""
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.enable_load_extension(True)
            await conn.load_extension(sqlite_vec.loadable_path())
            await conn.enable_load_extension(False)
            yield conn

    async def initialize(self) -> None:
        """Crea el schema y valida la dimensión de embedding.

        Raises:
            EmbeddingDimensionMismatchError: Si la DB existente tiene una dimensión
                distinta a la configurada en ``embedding_dim``.
        """
        async with self._conn() as conn:
            await self._init_db(conn)

    async def _init_db(self, conn: aiosqlite.Connection) -> None:
        """Crea tablas y verifica dimensión del modelo contra schema_meta."""
        # Crear todas las tablas
        await conn.execute(_CREATE_SCHEMA_META)
        await conn.execute(_CREATE_PERSONS)
        await conn.execute(_CREATE_PERSONS_INDEX)
        await conn.execute(_CREATE_PERSON_EMBEDDINGS)
        await conn.execute(_CREATE_PERSON_EMBEDDINGS_INDEX)
        await conn.execute(
            _CREATE_VEC_TABLE_TEMPLATE.format(dim=self._embedding_dim)
        )
        await conn.commit()

        # Verificar/guardar dimensión en schema_meta
        async with conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'embedding_dim'"
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            # Primera vez: guardar la dimensión
            await conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('embedding_dim', ?)",
                (str(self._embedding_dim),),
            )
            await conn.commit()
            logger.info(
                "faces.db inicializada: embedding_dim=%d", self._embedding_dim
            )
        else:
            # Validar que coincide con lo guardado
            guardada = int(row["value"])
            if guardada != self._embedding_dim:
                raise EmbeddingDimensionMismatchError(
                    esperada=guardada,
                    encontrada=self._embedding_dim,
                    modelo=f"configurado-dim-{self._embedding_dim}",
                )

    # ------------------------------------------------------------------
    # register_person
    # ------------------------------------------------------------------

    async def register_person(
        self,
        nombre: str | None,
        apellido: str | None,
        fecha_nacimiento: str | None,
        relacion: str | None,
        embedding: np.ndarray,
        source_history_id: int,
        source_face_ref: str,
        categoria: str | None = None,
    ) -> Person:
        """Registra una nueva persona con su embedding inicial."""
        ahora = datetime.now(timezone.utc).isoformat()
        person_id = str(uuid.uuid4())
        embedding_id = str(uuid.uuid4())

        async with self._conn() as conn:
            await self._init_db(conn)

            # Insertar persona
            await conn.execute(
                """
                INSERT INTO persons
                    (id, nombre, apellido, fecha_nacimiento, relacion, notes,
                     categoria, embeddings_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, NULL, ?, 1, ?, ?)
                """,
                (
                    person_id,
                    nombre,
                    apellido,
                    fecha_nacimiento,
                    relacion,
                    categoria,
                    ahora,
                    ahora,
                ),
            )

            # Insertar embedding en person_embeddings
            await conn.execute(
                """
                INSERT INTO person_embeddings
                    (id, person_id, source_history_id, source_face_ref, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (embedding_id, person_id, source_history_id, source_face_ref, ahora),
            )

            # Insertar en la tabla vec0
            vec_bytes = _pack_vec(embedding)
            await conn.execute(
                "INSERT INTO person_embeddings_vec (embedding_id, embedding) VALUES (?, ?)",
                (embedding_id, vec_bytes),
            )

            await conn.commit()

        logger.debug("Persona registrada: id=%s nombre=%r", person_id, nombre)

        return Person(
            id=person_id,
            nombre=nombre,
            apellido=apellido,
            fecha_nacimiento=fecha_nacimiento,
            relacion=relacion,
            notes=None,
            categoria=categoria,
            embeddings_count=1,
            created_at=datetime.fromisoformat(ahora),
            updated_at=datetime.fromisoformat(ahora),
        )

    # ------------------------------------------------------------------
    # add_embedding_to_person
    # ------------------------------------------------------------------

    async def add_embedding_to_person(
        self,
        person_id: str,
        embedding: np.ndarray,
        source_history_id: int,
        source_face_ref: str,
    ) -> None:
        """Agrega un embedding adicional a una persona existente."""
        ahora = datetime.now(timezone.utc).isoformat()
        embedding_id = str(uuid.uuid4())
        vec_bytes = _pack_vec(embedding)

        async with self._conn() as conn:
            await self._init_db(conn)

            # Verificar que la persona existe
            async with conn.execute(
                "SELECT id FROM persons WHERE id = ?", (person_id,)
            ) as cursor:
                if await cursor.fetchone() is None:
                    raise FaceRegistryError(
                        f"Persona no encontrada: {person_id!r}"
                    )

            # Insertar embedding
            await conn.execute(
                """
                INSERT INTO person_embeddings
                    (id, person_id, source_history_id, source_face_ref, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (embedding_id, person_id, source_history_id, source_face_ref, ahora),
            )

            # Insertar en vec0
            await conn.execute(
                "INSERT INTO person_embeddings_vec (embedding_id, embedding) VALUES (?, ?)",
                (embedding_id, vec_bytes),
            )

            # Actualizar contador
            await conn.execute(
                "UPDATE persons SET embeddings_count = embeddings_count + 1, updated_at = ? WHERE id = ?",
                (ahora, person_id),
            )

            await conn.commit()

        logger.debug("Embedding añadido a persona: person_id=%s", person_id)

    # ------------------------------------------------------------------
    # find_matches
    # ------------------------------------------------------------------

    async def find_matches(
        self,
        embedding: np.ndarray,
        k: int = 3,
    ) -> list[FaceMatch]:
        """Busca las k personas más similares al embedding dado vía sqlite-vec KNN.

        Retorna una lista con un FaceMatch por resultado. Cada FaceMatch tiene
        exactamente un candidato (la persona encontrada). El use case puede
        agrupar varios candidatos por cara si lo necesita.

        Incluye ignoradas — el use case decide cómo filtrar.
        """
        vec_bytes = _pack_vec(embedding)

        async with self._conn() as conn:
            await self._init_db(conn)

            # Verificar que hay embeddings en el registro
            async with conn.execute(
                "SELECT COUNT(*) as cnt FROM person_embeddings_vec"
            ) as cursor:
                row = await cursor.fetchone()
                if row["cnt"] == 0:
                    return []

            # KNN search via vec0: MATCH + k
            rows = await conn.execute_fetchall(
                """
                SELECT v.embedding_id, v.distance,
                       pe.person_id,
                       p.id, p.nombre, p.apellido, p.fecha_nacimiento,
                       p.relacion, p.notes, p.categoria, p.embeddings_count,
                       p.created_at, p.updated_at
                FROM person_embeddings_vec v
                JOIN person_embeddings pe ON v.embedding_id = pe.id
                JOIN persons p ON pe.person_id = p.id
                WHERE v.embedding MATCH ?
                  AND k = ?
                ORDER BY v.distance
                """,
                (vec_bytes, k),
            )

        if not rows:
            return []

        # Construir FaceMatch: un candidato por resultado KNN
        # (el face_ref se construye con un placeholder — el use case lo completa)
        resultado: list[FaceMatch] = []
        for row in rows:
            persona = _row_to_person(row)
            distancia = row["distance"]
            # score coseno: para vectores unitarios, dist_L2² = 2(1 - cosθ)
            score = 1.0 - (distancia ** 2) / 2.0

            face_match = FaceMatch(
                face_ref="",  # Placeholder — el use case asigna el face_ref real
                bbox=BBox(x=0, y=0, w=0, h=0),  # Placeholder
                candidates=[(persona, float(score))],
                status=MatchStatus.UNKNOWN,  # El use case aplica los thresholds
                categoria=persona.categoria,
            )
            resultado.append(face_match)

        return resultado

    # ------------------------------------------------------------------
    # list_persons
    # ------------------------------------------------------------------

    async def list_persons(
        self,
        incluir_ignoradas: bool = False,
    ) -> list[Person]:
        """Lista todas las personas registradas."""
        async with self._conn() as conn:
            await self._init_db(conn)

            if incluir_ignoradas:
                rows = await conn.execute_fetchall(
                    "SELECT * FROM persons ORDER BY nombre ASC NULLS LAST"
                )
            else:
                rows = await conn.execute_fetchall(
                    "SELECT * FROM persons WHERE categoria != 'ignorada' OR categoria IS NULL "
                    "ORDER BY nombre ASC"
                )

        return [_row_to_person(row) for row in rows]

    # ------------------------------------------------------------------
    # forget_person
    # ------------------------------------------------------------------

    async def forget_person(self, person_id: str) -> None:
        """Elimina una persona y todos sus embeddings del registro."""
        async with self._conn() as conn:
            await self._init_db(conn)

            # Verificar que existe
            async with conn.execute(
                "SELECT id FROM persons WHERE id = ?", (person_id,)
            ) as cursor:
                if await cursor.fetchone() is None:
                    raise FaceRegistryError(
                        f"Persona no encontrada para borrar: {person_id!r}"
                    )

            # Obtener todos los embedding_ids de esta persona para borrar de vec0
            embedding_ids = await conn.execute_fetchall(
                "SELECT id FROM person_embeddings WHERE person_id = ?",
                (person_id,),
            )

            # Borrar de vec0 primero (no tiene FK cascade automático en sqlite-vec)
            for emb_row in embedding_ids:
                await conn.execute(
                    "DELETE FROM person_embeddings_vec WHERE embedding_id = ?",
                    (emb_row["id"],),
                )

            # Borrar person_embeddings (FK ON DELETE CASCADE desde persons → auto, pero
            # borramos explícitamente para evitar depender del CASCADE en sqlite-vec)
            await conn.execute(
                "DELETE FROM person_embeddings WHERE person_id = ?",
                (person_id,),
            )

            # Borrar la persona
            await conn.execute(
                "DELETE FROM persons WHERE id = ?",
                (person_id,),
            )

            await conn.commit()

        logger.debug("Persona eliminada: person_id=%s", person_id)

    # ------------------------------------------------------------------
    # merge_persons
    # ------------------------------------------------------------------

    async def merge_persons(self, source_id: str, target_id: str) -> Person:
        """Fusiona todos los embeddings de source en target y elimina source."""
        async with self._conn() as conn:
            await self._init_db(conn)

            # Verificar ambas personas existen
            for pid in (source_id, target_id):
                async with conn.execute(
                    "SELECT id FROM persons WHERE id = ?", (pid,)
                ) as cursor:
                    if await cursor.fetchone() is None:
                        raise FaceRegistryError(
                            f"Persona no encontrada para merge: {pid!r}"
                        )

            # Re-asignar embeddings de source a target
            await conn.execute(
                "UPDATE person_embeddings SET person_id = ? WHERE person_id = ?",
                (target_id, source_id),
            )

            # Contar embeddings totales del target
            async with conn.execute(
                "SELECT COUNT(*) as cnt FROM person_embeddings WHERE person_id = ?",
                (target_id,),
            ) as cursor:
                row = await cursor.fetchone()
                total_embeddings = row["cnt"]

            ahora = datetime.now(timezone.utc).isoformat()

            # Actualizar contador del target
            await conn.execute(
                "UPDATE persons SET embeddings_count = ?, updated_at = ? WHERE id = ?",
                (total_embeddings, ahora, target_id),
            )

            # Borrar la persona source (sin borrar sus embeddings — ya fueron re-asignados)
            await conn.execute("DELETE FROM persons WHERE id = ?", (source_id,))

            await conn.commit()

        persona_target = await self.get_person(target_id)
        if persona_target is None:
            raise FaceRegistryError(
                f"Error inesperado: target persona {target_id!r} no encontrada después del merge"
            )

        logger.debug(
            "Merge completado: source=%s → target=%s (%d embeddings)",
            source_id,
            target_id,
            total_embeddings,
        )
        return persona_target

    # ------------------------------------------------------------------
    # update_person_metadata
    # ------------------------------------------------------------------

    async def update_person_metadata(
        self,
        person_id: str,
        **fields: object,
    ) -> Person:
        """Actualiza campos de metadata de una persona."""
        campos_permitidos = {
            "nombre", "apellido", "fecha_nacimiento", "relacion", "notes", "categoria"
        }
        campos_a_actualizar = {k: v for k, v in fields.items() if k in campos_permitidos}

        if not campos_a_actualizar:
            # Sin cambios — retornar la persona tal como está
            persona = await self.get_person(person_id)
            if persona is None:
                raise FaceRegistryError(f"Persona no encontrada: {person_id!r}")
            return persona

        ahora = datetime.now(timezone.utc).isoformat()
        campos_a_actualizar["updated_at"] = ahora

        set_clause = ", ".join(f"{k} = ?" for k in campos_a_actualizar)
        valores = list(campos_a_actualizar.values()) + [person_id]

        async with self._conn() as conn:
            await self._init_db(conn)

            cursor = await conn.execute(
                f"UPDATE persons SET {set_clause} WHERE id = ?",
                valores,
            )
            if cursor.rowcount == 0:
                raise FaceRegistryError(f"Persona no encontrada: {person_id!r}")

            await conn.commit()

        persona = await self.get_person(person_id)
        if persona is None:
            raise FaceRegistryError(
                f"Error inesperado: persona {person_id!r} no encontrada después de actualizar"
            )
        return persona

    # ------------------------------------------------------------------
    # get_person
    # ------------------------------------------------------------------

    async def get_person(self, person_id: str) -> Person | None:
        """Recupera una persona por su ID."""
        async with self._conn() as conn:
            await self._init_db(conn)
            async with conn.execute(
                "SELECT * FROM persons WHERE id = ?", (person_id,)
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return None
        return _row_to_person(row)

    # ------------------------------------------------------------------
    # get_centroid
    # ------------------------------------------------------------------

    async def get_centroid(self, person_id: str) -> np.ndarray | None:
        """Calcula el centroide (promedio) de todos los embeddings de una persona.

        Los embeddings se leen desde person_embeddings_vec como bytes y se
        deserializan a numpy arrays para calcular el promedio.
        """
        async with self._conn() as conn:
            await self._init_db(conn)

            # Obtener los embedding_ids de la persona
            embedding_ids_rows = await conn.execute_fetchall(
                "SELECT id FROM person_embeddings WHERE person_id = ?",
                (person_id,),
            )

        if not embedding_ids_rows:
            return None

        embedding_ids = [row["id"] for row in embedding_ids_rows]

        # Leer los vectores de la tabla vec0
        # Nota: sqlite-vec no permite SELECT embedding FROM vec0 directamente en modo batch;
        # hacemos queries individuales para cada embedding_id.
        vectores: list[np.ndarray] = []

        async with self._conn() as conn:
            for emb_id in embedding_ids:
                async with conn.execute(
                    "SELECT embedding FROM person_embeddings_vec WHERE embedding_id = ?",
                    (emb_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                if row is not None:
                    raw = bytes(row["embedding"])
                    n = len(raw) // 4  # 4 bytes por float32
                    arr = np.frombuffer(raw, dtype=np.float32).copy()
                    vectores.append(arr)

        if not vectores:
            return None

        return np.mean(vectores, axis=0).astype(np.float32)
