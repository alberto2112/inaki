"""Tests para SqliteMessageFaceMetadataRepo.

Cubre:
- SC-01: save + get_by_history_id (round-trip básico)
- SC-02: get_by_history_id para history_id inexistente → None
- SC-03: save duplicado → upsert (no viola PK)
- SC-04: find_recent_for_thread — N más recientes ordenados DESC
- SC-05: find_recent_for_thread — sin resultados → lista vacía
- SC-06: find_recent_for_thread — respeta scope (agent_id, channel, chat_id)
- SC-07: resolve_face_ref — resuelve face_ref válido
- SC-08: resolve_face_ref — history_id inexistente → None
- SC-09: resolve_face_ref — face_idx out of range → None
"""

from __future__ import annotations

import struct
from datetime import datetime, timezone

import numpy as np
import pytest

from adapters.outbound.history.sqlite_message_face_metadata_repo import (
    SqliteMessageFaceMetadataRepo,
)
from core.domain.entities.face import (
    BBox,
    FaceMatch,
    MatchStatus,
    MessageFaceMetadata,
)


# ---------------------------------------------------------------------------
# Helpers de construcción
# ---------------------------------------------------------------------------


def _make_embedding(dim: int = 512, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.random(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def _pack_embeddings_blob(embeddings: list[np.ndarray]) -> bytes:
    """Serializa lista de embeddings como blob binario (float32 concatenados)."""
    arr = np.array(embeddings, dtype=np.float32)
    return arr.tobytes()


def _make_face_match(face_ref: str, status: MatchStatus = MatchStatus.UNKNOWN) -> FaceMatch:
    return FaceMatch(
        face_ref=face_ref,
        bbox=BBox(x=10, y=20, w=50, h=60),
        candidates=[],
        status=status,
        categoria=None,
    )


def _make_metadata(
    history_id: int,
    agent_id: str = "agente1",
    channel: str = "telegram",
    chat_id: str = "chat_123",
    n_faces: int = 1,
    seed: int = 0,
) -> MessageFaceMetadata:
    embeddings = [_make_embedding(seed=seed + i) for i in range(n_faces)]
    faces = [_make_face_match(f"{history_id}#{i}") for i in range(n_faces)]
    blob = _pack_embeddings_blob(embeddings)
    return MessageFaceMetadata(
        history_id=history_id,
        agent_id=agent_id,
        channel=channel,
        chat_id=chat_id,
        faces=faces,
        embeddings_blob=blob,
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "history.db")
    return SqliteMessageFaceMetadataRepo(db_path=db_path)


# ---------------------------------------------------------------------------
# SC-01: save + get_by_history_id
# ---------------------------------------------------------------------------


async def test_save_y_get_by_history_id(repo):
    """Round-trip básico: save guarda, get_by_history_id recupera idéntico."""
    meta = _make_metadata(history_id=42)
    await repo.save(meta)

    result = await repo.get_by_history_id(42)

    assert result is not None
    assert result.history_id == 42
    assert result.agent_id == "agente1"
    assert result.channel == "telegram"
    assert result.chat_id == "chat_123"
    assert len(result.faces) == 1
    assert result.faces[0].face_ref == "42#0"
    # El blob debe round-trippear
    assert result.embeddings_blob == meta.embeddings_blob


# ---------------------------------------------------------------------------
# SC-02: get_by_history_id para history_id inexistente
# ---------------------------------------------------------------------------


async def test_get_by_history_id_inexistente_retorna_none(repo):
    result = await repo.get_by_history_id(9999)
    assert result is None


# ---------------------------------------------------------------------------
# SC-03: save duplicado → upsert (no falla)
# ---------------------------------------------------------------------------


async def test_save_duplicado_upsert(repo):
    meta_v1 = _make_metadata(history_id=10, n_faces=1, seed=0)
    meta_v2 = _make_metadata(history_id=10, n_faces=2, seed=10)

    await repo.save(meta_v1)
    await repo.save(meta_v2)  # no debe fallar (upsert)

    result = await repo.get_by_history_id(10)
    assert result is not None
    # El último save gana
    assert len(result.faces) == 2


# ---------------------------------------------------------------------------
# SC-04: find_recent_for_thread — N más recientes DESC
# ---------------------------------------------------------------------------


async def test_find_recent_for_thread_ordena_desc(repo):
    # Guardar 3 metadatas con history_ids distintos
    for hid in [100, 101, 102]:
        await repo.save(_make_metadata(history_id=hid))

    results = await repo.find_recent_for_thread(
        agent_id="agente1", channel="telegram", chat_id="chat_123", limit=10
    )

    assert len(results) == 3
    # Orden DESC por history_id (rows más recientes = IDs mayores)
    assert results[0].history_id > results[1].history_id
    assert results[1].history_id > results[2].history_id


# ---------------------------------------------------------------------------
# SC-05: find_recent_for_thread — sin resultados
# ---------------------------------------------------------------------------


async def test_find_recent_for_thread_sin_resultados(repo):
    results = await repo.find_recent_for_thread(
        agent_id="agente1", channel="telegram", chat_id="chat_nada", limit=5
    )
    assert results == []


# ---------------------------------------------------------------------------
# SC-06: find_recent_for_thread — respeta scope
# ---------------------------------------------------------------------------


async def test_find_recent_for_thread_respeta_scope(repo):
    # Metadata en chat A
    await repo.save(_make_metadata(history_id=200, chat_id="chat_A"))
    # Metadata en chat B (mismo agente, mismo canal)
    await repo.save(_make_metadata(history_id=201, chat_id="chat_B"))

    results_a = await repo.find_recent_for_thread(
        agent_id="agente1", channel="telegram", chat_id="chat_A", limit=10
    )
    results_b = await repo.find_recent_for_thread(
        agent_id="agente1", channel="telegram", chat_id="chat_B", limit=10
    )

    assert len(results_a) == 1
    assert results_a[0].history_id == 200
    assert len(results_b) == 1
    assert results_b[0].history_id == 201


# ---------------------------------------------------------------------------
# SC-07: resolve_face_ref — resuelve face_ref válido
# ---------------------------------------------------------------------------


async def test_resolve_face_ref_valido(repo):
    meta = _make_metadata(history_id=300, n_faces=2)
    await repo.save(meta)

    result = await repo.resolve_face_ref(
        agent_id="agente1", channel="telegram", chat_id="chat_123", face_ref="300#1"
    )

    assert result is not None
    recuperada_meta, face_idx = result
    assert recuperada_meta.history_id == 300
    assert face_idx == 1


# ---------------------------------------------------------------------------
# SC-08: resolve_face_ref — history_id inexistente
# ---------------------------------------------------------------------------


async def test_resolve_face_ref_history_inexistente(repo):
    result = await repo.resolve_face_ref(
        agent_id="agente1", channel="telegram", chat_id="chat_123", face_ref="9999#0"
    )
    assert result is None


# ---------------------------------------------------------------------------
# SC-09: resolve_face_ref — face_idx out of range
# ---------------------------------------------------------------------------


async def test_resolve_face_ref_idx_out_of_range(repo):
    meta = _make_metadata(history_id=400, n_faces=1)
    await repo.save(meta)

    result = await repo.resolve_face_ref(
        agent_id="agente1", channel="telegram", chat_id="chat_123", face_ref="400#99"
    )
    assert result is None
