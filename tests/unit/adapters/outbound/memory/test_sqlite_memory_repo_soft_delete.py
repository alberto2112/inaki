"""
Tests del soft-delete y update en `SQLiteMemoryRepository`.

Usan SQLite ``:memory:`` real (sqlite-vec cargado por el adapter), no mocks.
Cubren el contrato nuevo del port: ``delete``, ``update``, y el filtrado
``deleted=0`` en ``search_with_scores`` y ``get_recent``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from adapters.outbound.memory.sqlite_memory_repo import SQLiteMemoryRepository
from core.domain.entities.memory import MemoryEntry


class _FakeEmbedder:
    """Embedder dummy: devuelve vectores deterministas; suficiente para tests."""

    async def embed_passage(self, text: str) -> list[float]:
        # Vector unitario simple basado en hash del texto, dim=384
        h = abs(hash(text))
        v = [(h >> i) & 1 for i in range(384)]
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    async def embed_query(self, text: str) -> list[float]:
        return await self.embed_passage(text)


@pytest.fixture
def repo(tmp_path):
    db = tmp_path / "test.db"
    embedder = _FakeEmbedder()
    return SQLiteMemoryRepository(db_path=str(db), embedder=embedder)


def _entry(content: str, *, agent_id: str = "test", channel: str | None = None) -> MemoryEntry:
    return MemoryEntry(
        content=content,
        embedding=[0.1] * 384,
        relevance=0.9,
        tags=["t"],
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        agent_id=agent_id,
        channel=channel,
    )


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


async def test_delete_marks_entry_as_deleted_and_returns_it(repo):
    e = _entry("recuerdo original")
    await repo.store(e)

    result = await repo.delete(e.id)

    assert result is not None
    assert result.id == e.id
    assert result.content == "recuerdo original"
    assert result.deleted is True


async def test_delete_unknown_id_returns_none(repo):
    result = await repo.delete("00000000-0000-0000-0000-000000000000")
    assert result is None


async def test_delete_is_idempotent(repo):
    e = _entry("x")
    await repo.store(e)

    first = await repo.delete(e.id)
    second = await repo.delete(e.id)

    assert first is not None
    assert second is None  # ya estaba borrado → None


async def test_get_recent_excludes_soft_deleted(repo):
    e1 = _entry("activa")
    e2 = _entry("borrada")
    await repo.store(e1)
    await repo.store(e2)
    await repo.delete(e2.id)

    recent = await repo.get_recent(10, agent_id="test")

    ids = {e.id for e in recent}
    assert e1.id in ids
    assert e2.id not in ids


async def test_search_with_scores_excludes_soft_deleted(repo):
    e1 = _entry("python tooling")
    e2 = _entry("python tooling también")
    await repo.store(e1)
    await repo.store(e2)
    await repo.delete(e2.id)

    embedder = _FakeEmbedder()
    qvec = await embedder.embed_query("python tooling")
    results = await repo.search_with_scores(qvec, top_k=5)

    ids = {entry.id for entry, _ in results}
    assert e1.id in ids
    assert e2.id not in ids


async def test_search_excludes_soft_deleted(repo):
    e = _entry("borrame")
    await repo.store(e)
    await repo.delete(e.id)

    embedder = _FakeEmbedder()
    qvec = await embedder.embed_query("borrame")
    results = await repo.search(qvec, top_k=5)

    assert all(r.id != e.id for r in results)


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


async def test_update_partial_content_only(repo):
    e = _entry("contenido viejo")
    await repo.store(e)

    embedder = _FakeEmbedder()
    new_emb = await embedder.embed_passage("contenido nuevo")
    result = await repo.update(e.id, content="contenido nuevo", embedding=new_emb)

    assert result is not None
    assert result.content == "contenido nuevo"
    assert result.relevance == 0.9  # sin cambios
    assert result.tags == ["t"]


async def test_update_partial_tags_only(repo):
    e = _entry("x")
    await repo.store(e)

    result = await repo.update(e.id, tags=["nuevo", "tag"])

    assert result is not None
    assert result.tags == ["nuevo", "tag"]
    assert result.content == "x"


async def test_update_partial_relevance_only(repo):
    e = _entry("x")
    await repo.store(e)

    result = await repo.update(e.id, relevance=0.4)

    assert result is not None
    assert result.relevance == 0.4


async def test_update_unknown_id_returns_none(repo):
    result = await repo.update("00000000-0000-0000-0000-000000000000", content="x")
    assert result is None


async def test_update_deleted_entry_returns_none(repo):
    e = _entry("x")
    await repo.store(e)
    await repo.delete(e.id)

    result = await repo.update(e.id, content="nuevo")

    assert result is None


async def test_update_no_op_when_no_fields_provided(repo):
    e = _entry("x")
    await repo.store(e)

    result = await repo.update(e.id)

    # Sin campos ni embedding → devuelve la entry actual sin cambios.
    assert result is not None
    assert result.content == "x"
