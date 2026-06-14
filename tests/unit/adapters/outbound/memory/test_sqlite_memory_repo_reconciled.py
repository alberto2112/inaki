"""
Tests del flag ``reconciled`` y los métodos ``load_unreconciled`` /
``mark_reconciled`` en ``SQLiteMemoryRepository``.

Usan SQLite real en disco temporal (sqlite-vec cargado por el adapter), no mocks.
Cubren el contrato del port: filtrado por reconciled+deleted, filtrado por scope,
granularidad de mark_reconciled, no-op con lista vacía, y migración en caliente
sobre una DB legacy sin la columna.
"""

from __future__ import annotations

import aiosqlite
import sqlite_vec

from datetime import datetime, timezone

import pytest

from adapters.outbound.memory.sqlite_memory_repo import SQLiteMemoryRepository
from core.domain.entities.memory import MemoryEntry


class _FakeEmbedder:
    """Embedder dummy: devuelve vectores deterministas de dim=384."""

    async def embed_passage(self, text: str) -> list[float]:
        h = abs(hash(text))
        v = [(h >> i) & 1 for i in range(384)]
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    async def embed_query(self, text: str) -> list[float]:
        return await self.embed_passage(text)


@pytest.fixture
def repo(tmp_path):
    db = tmp_path / "test_reconciled.db"
    return SQLiteMemoryRepository(db_path=str(db), embedder=_FakeEmbedder())


def _entry(
    content: str,
    *,
    agent_id: str = "agente",
    channel: str | None = "telegram",
    chat_id: str | None = "123",
) -> MemoryEntry:
    return MemoryEntry(
        content=content,
        embedding=[0.1] * 384,
        relevance=0.8,
        tags=[],
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        agent_id=agent_id,
        channel=channel,
        chat_id=chat_id,
    )


# ---------------------------------------------------------------------------
# load_unreconciled — filtrado básico
# ---------------------------------------------------------------------------


async def test_load_unreconciled_devuelve_solo_no_reconciliados(repo):
    e1 = _entry("recuerdo pendiente")
    e2 = _entry("ya reconciliado")
    await repo.store(e1)
    await repo.store(e2)
    # Marcamos e2 como reconciliado
    count = await repo.mark_reconciled([e2.id])
    assert count == 1

    resultado = await repo.load_unreconciled("agente")

    ids = {e.id for e in resultado}
    assert e1.id in ids
    assert e2.id not in ids


async def test_load_unreconciled_excluye_soft_deleted(repo):
    e_activo = _entry("activo pendiente")
    e_borrado = _entry("borrado pendiente")
    await repo.store(e_activo)
    await repo.store(e_borrado)
    await repo.delete(e_borrado.id)

    resultado = await repo.load_unreconciled("agente")

    ids = {e.id for e in resultado}
    assert e_activo.id in ids
    assert e_borrado.id not in ids


async def test_load_unreconciled_devuelve_vacio_si_todos_reconciliados(repo):
    e = _entry("ya procesado")
    await repo.store(e)
    await repo.mark_reconciled([e.id])

    resultado = await repo.load_unreconciled("agente")

    assert resultado == []


async def test_load_unreconciled_order_created_at_asc(repo):
    """Los recuerdos se devuelven de más viejo a más nuevo."""
    viejo = MemoryEntry(
        content="primero",
        embedding=[0.1] * 384,
        relevance=0.5,
        tags=[],
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        agent_id="agente",
        channel="telegram",
        chat_id="123",
    )
    nuevo = MemoryEntry(
        content="segundo",
        embedding=[0.1] * 384,
        relevance=0.5,
        tags=[],
        created_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
        agent_id="agente",
        channel="telegram",
        chat_id="123",
    )
    await repo.store(nuevo)
    await repo.store(viejo)

    resultado = await repo.load_unreconciled("agente")

    assert len(resultado) == 2
    assert resultado[0].id == viejo.id
    assert resultado[1].id == nuevo.id


# ---------------------------------------------------------------------------
# load_unreconciled — filtrado por scope
# ---------------------------------------------------------------------------


async def test_load_unreconciled_filtra_por_channel(repo):
    e_tg = _entry("telegram", channel="telegram", chat_id="1")
    e_cli = _entry("cli", channel="cli", chat_id="1")
    await repo.store(e_tg)
    await repo.store(e_cli)

    resultado = await repo.load_unreconciled("agente", channel="telegram")

    ids = {e.id for e in resultado}
    assert e_tg.id in ids
    assert e_cli.id not in ids


async def test_load_unreconciled_filtra_por_chat_id(repo):
    e1 = _entry("chat 1", channel="telegram", chat_id="chat1")
    e2 = _entry("chat 2", channel="telegram", chat_id="chat2")
    await repo.store(e1)
    await repo.store(e2)

    resultado = await repo.load_unreconciled("agente", channel="telegram", chat_id="chat1")

    ids = {e.id for e in resultado}
    assert e1.id in ids
    assert e2.id not in ids


async def test_load_unreconciled_sin_filtro_scope_devuelve_todos_del_agente(repo):
    e1 = _entry("c1", channel="telegram", chat_id="1")
    e2 = _entry("c2", channel="cli", chat_id="user")
    otro = _entry("otro agente", agent_id="otro")
    await repo.store(e1)
    await repo.store(e2)
    await repo.store(otro)

    resultado = await repo.load_unreconciled("agente")

    ids = {e.id for e in resultado}
    assert e1.id in ids
    assert e2.id in ids
    assert otro.id not in ids  # pertenece a otro agente


# ---------------------------------------------------------------------------
# mark_reconciled — granularidad y no-op
# ---------------------------------------------------------------------------


async def test_mark_reconciled_solo_marca_los_ids_dados(repo):
    e1 = _entry("marca este")
    e2 = _entry("no toques este")
    await repo.store(e1)
    await repo.store(e2)

    count = await repo.mark_reconciled([e1.id])

    assert count == 1
    # e2 sigue sin marcar
    pendientes = await repo.load_unreconciled("agente")
    ids_pendientes = {e.id for e in pendientes}
    assert e1.id not in ids_pendientes
    assert e2.id in ids_pendientes


async def test_mark_reconciled_no_toca_otros_agentes(repo):
    """Garantía explícita de granularidad: mark_reconciled no afecta otros agentes."""
    mio = _entry("mio", agent_id="agente_a")
    ajeno = _entry("ajeno", agent_id="agente_b")
    await repo.store(mio)
    await repo.store(ajeno)

    # Aunque pasamos el id de 'mio', el otro agente no debe verse afectado.
    await repo.mark_reconciled([mio.id])

    pendientes_b = await repo.load_unreconciled("agente_b")
    assert any(e.id == ajeno.id for e in pendientes_b)


async def test_mark_reconciled_lista_vacia_es_no_op(repo):
    e = _entry("sin marcar")
    await repo.store(e)

    count = await repo.mark_reconciled([])

    assert count == 0
    pendientes = await repo.load_unreconciled("agente")
    assert any(entry.id == e.id for entry in pendientes)


async def test_mark_reconciled_devuelve_rowcount_correcto(repo):
    e1 = _entry("uno")
    e2 = _entry("dos")
    e3 = _entry("tres")
    await repo.store(e1)
    await repo.store(e2)
    await repo.store(e3)

    count = await repo.mark_reconciled([e1.id, e2.id])

    assert count == 2


async def test_mark_reconciled_id_inexistente_no_cuenta(repo):
    e = _entry("real")
    await repo.store(e)

    count = await repo.mark_reconciled([e.id, "00000000-0000-0000-0000-000000000000"])

    assert count == 1  # solo el real cuenta


# ---------------------------------------------------------------------------
# MemoryEntry.reconciled en round-trip
# ---------------------------------------------------------------------------


async def test_entry_reconciled_false_por_defecto(repo):
    e = _entry("nuevo recuerdo")
    await repo.store(e)

    recuperado = await repo.get_by_id(e.id)

    assert recuperado is not None
    assert recuperado.reconciled is False


async def test_entry_reconciled_true_tras_mark(repo):
    e = _entry("reconciliar")
    await repo.store(e)
    await repo.mark_reconciled([e.id])

    recuperado = await repo.get_by_id(e.id)

    assert recuperado is not None
    assert recuperado.reconciled is True


# ---------------------------------------------------------------------------
# Migración en caliente: DB legacy sin columna reconciled
# ---------------------------------------------------------------------------


async def test_migracion_caliente_agrega_columna_reconciled(tmp_path):
    """Una DB creada sin la columna ``reconciled`` se migra automáticamente al primer uso."""
    db_path = str(tmp_path / "legacy.db")

    # Crear una DB legacy manualmente: tabla memories sin columna reconciled
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.enable_load_extension(True)
        await conn.load_extension(sqlite_vec.loadable_path())
        await conn.enable_load_extension(False)
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id         TEXT PRIMARY KEY,
                content    TEXT NOT NULL,
                relevance  REAL NOT NULL,
                tags       TEXT NOT NULL,
                created_at TEXT NOT NULL,
                agent_id   TEXT,
                channel    TEXT,
                chat_id    TEXT,
                deleted    INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings USING vec0(
                id        TEXT PRIMARY KEY,
                embedding FLOAT[384]
            )
            """
        )
        # Insertar una fila legacy (sin reconciled)
        await conn.execute(
            "INSERT INTO memories (id, content, relevance, tags, created_at, agent_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy-id", "recuerdo viejo", 0.7, "[]", "2025-01-01T00:00:00", "agente"),
        )
        await conn.commit()

    # Ahora instanciamos el repo apuntando a la DB legacy
    repo_nuevo = SQLiteMemoryRepository(db_path=db_path, embedder=_FakeEmbedder())

    # La primera operación debe disparar la migración sin errores
    pendientes = await repo_nuevo.load_unreconciled("agente")

    # La fila legacy aparece como no reconciliada (DEFAULT 0 aplicado por ALTER TABLE)
    assert any(e.id == "legacy-id" for e in pendientes)
    assert all(e.reconciled is False for e in pendientes)


async def test_migracion_caliente_es_idempotente(tmp_path):
    """Llamar _ensure_schema dos veces no falla aunque la columna ya exista."""
    db_path = str(tmp_path / "idem.db")
    repo = SQLiteMemoryRepository(db_path=db_path, embedder=_FakeEmbedder())

    # Primera vez crea el schema completo
    e = _entry("idem")
    await repo.store(e)

    # Segunda operación: _ensure_schema se llama de nuevo — no debe fallar
    await repo.load_unreconciled("agente")  # sin excepción
