"""Tests para SqliteTelegramFileRepo (DB real en :memory: vía path tmp)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from adapters.outbound.file_repo.sqlite_telegram_file_repo import (
    SqliteTelegramFileRepo,
)
from core.domain.value_objects.telegram_file import TelegramFileRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record(
    *,
    file_id: str,
    file_unique_id: str | None = None,
    content_type: str = "photo",
    media_group_id: str | None = None,
    received_at: datetime | None = None,
    chat_id: str = "-100",
    history_id: int | None = None,
    mime_type: str | None = "image/jpeg",
    caption: str | None = None,
) -> TelegramFileRecord:
    return TelegramFileRecord(
        agent_id="test",
        channel="telegram",
        chat_id=chat_id,
        content_type=content_type,  # type: ignore[arg-type]
        file_id=file_id,
        file_unique_id=file_unique_id or file_id + "-unique",
        media_group_id=media_group_id,
        caption=caption,
        history_id=history_id,
        mime_type=mime_type,
        received_at=received_at or datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
async def repo(tmp_path: Path) -> SqliteTelegramFileRepo:
    instance = SqliteTelegramFileRepo(tmp_path / "telegram_files.db")
    await instance.ensure_schema()
    return instance


# ---------------------------------------------------------------------------
# save + query simple
# ---------------------------------------------------------------------------


async def test_save_y_query_recent_devuelve_records(repo: SqliteTelegramFileRepo):
    base = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    await repo.save(_record(file_id="A", received_at=base))
    await repo.save(_record(file_id="B", received_at=base + timedelta(minutes=1)))

    out = await repo.query_recent(
        agent_id="test", channel="telegram", chat_id="-100",
        content_type="photo", count=5,
    )

    assert [r.file_id for r in out] == ["B", "A"]


async def test_query_respeta_count(repo: SqliteTelegramFileRepo):
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    for i in range(5):
        await repo.save(_record(file_id=f"f{i}", received_at=base + timedelta(minutes=i)))

    out = await repo.query_recent(
        agent_id="test", channel="telegram", chat_id="-100",
        content_type="photo", count=2,
    )

    assert len(out) == 2
    assert [r.file_id for r in out] == ["f4", "f3"]


async def test_query_aisla_por_agent_id(repo: SqliteTelegramFileRepo):
    await repo.save(_record(file_id="A"))
    other = TelegramFileRecord(
        agent_id="other",
        channel="telegram",
        chat_id="-100",
        content_type="photo",
        file_id="X",
        file_unique_id="X-u",
        received_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    await repo.save(other)

    out = await repo.query_recent(
        agent_id="test", channel="telegram", chat_id="-100",
        content_type="photo", count=10,
    )
    assert [r.file_id for r in out] == ["A"]


async def test_query_aisla_por_chat(repo: SqliteTelegramFileRepo):
    await repo.save(_record(file_id="A", chat_id="-100"))
    await repo.save(_record(file_id="B", chat_id="-200"))

    out = await repo.query_recent(
        agent_id="test", channel="telegram", chat_id="-100",
        content_type="photo", count=10,
    )
    assert [r.file_id for r in out] == ["A"]


# ---------------------------------------------------------------------------
# Filtros temporales
# ---------------------------------------------------------------------------


async def test_query_filtra_por_since(repo: SqliteTelegramFileRepo):
    base = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    await repo.save(_record(file_id="early", received_at=base))
    await repo.save(_record(file_id="late", received_at=base + timedelta(hours=2)))

    out = await repo.query_recent(
        agent_id="test", channel="telegram", chat_id="-100",
        content_type="photo", count=10,
        since=base + timedelta(hours=1),
    )
    assert [r.file_id for r in out] == ["late"]


async def test_query_filtra_por_rango(repo: SqliteTelegramFileRepo):
    base = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    await repo.save(_record(file_id="A", received_at=base))
    await repo.save(_record(file_id="B", received_at=base + timedelta(hours=1)))
    await repo.save(_record(file_id="C", received_at=base + timedelta(hours=2)))

    out = await repo.query_recent(
        agent_id="test", channel="telegram", chat_id="-100",
        content_type="photo", count=10,
        since=base + timedelta(minutes=30),
        until=base + timedelta(hours=1, minutes=30),
    )
    assert [r.file_id for r in out] == ["B"]


async def test_query_rechaza_naive_datetime(repo: SqliteTelegramFileRepo):
    with pytest.raises(ValueError, match="timezone-aware"):
        await repo.query_recent(
            agent_id="test", channel="telegram", chat_id="-100",
            content_type="photo", count=5,
            since=datetime(2026, 5, 1),  # naive
        )


# ---------------------------------------------------------------------------
# Filtro photo excluye álbumes
# ---------------------------------------------------------------------------


async def test_query_photo_excluye_miembros_de_album(repo: SqliteTelegramFileRepo):
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    await repo.save(_record(file_id="solo", received_at=base))
    await repo.save(_record(
        file_id="album1", media_group_id="grupo-1",
        received_at=base + timedelta(minutes=1),
    ))

    out = await repo.query_recent(
        agent_id="test", channel="telegram", chat_id="-100",
        content_type="photo", count=10,
    )
    assert [r.file_id for r in out] == ["solo"]


# ---------------------------------------------------------------------------
# Album queries
# ---------------------------------------------------------------------------


async def test_query_album_devuelve_grupo_completo(repo: SqliteTelegramFileRepo):
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    for i in range(3):
        await repo.save(_record(
            file_id=f"a-{i}",
            media_group_id="grupo-1",
            received_at=base + timedelta(seconds=i),
        ))

    out = await repo.query_recent(
        agent_id="test", channel="telegram", chat_id="-100",
        content_type="album", count=10,
    )
    assert [r.file_id for r in out] == ["a-0", "a-1", "a-2"]
    assert all(r.media_group_id == "grupo-1" for r in out)


async def test_query_album_respeta_count_partial(repo: SqliteTelegramFileRepo):
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    for i in range(4):
        await repo.save(_record(
            file_id=f"a-{i}",
            media_group_id="grupo-1",
            received_at=base + timedelta(seconds=i),
        ))

    out = await repo.query_recent(
        agent_id="test", channel="telegram", chat_id="-100",
        content_type="album", count=2,
    )
    assert len(out) == 2


async def test_query_album_recorre_albums_por_recencia(
    repo: SqliteTelegramFileRepo,
):
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    # álbum viejo
    for i in range(2):
        await repo.save(_record(
            file_id=f"old-{i}", media_group_id="vieja",
            received_at=base + timedelta(seconds=i),
        ))
    # álbum nuevo
    for i in range(3):
        await repo.save(_record(
            file_id=f"new-{i}", media_group_id="nueva",
            received_at=base + timedelta(hours=1, seconds=i),
        ))

    out = await repo.query_recent(
        agent_id="test", channel="telegram", chat_id="-100",
        content_type="album", count=10,
    )
    # primero todos los del álbum más reciente, luego los del viejo
    file_ids = [r.file_id for r in out]
    assert file_ids == ["new-0", "new-1", "new-2", "old-0", "old-1"]


# ---------------------------------------------------------------------------
# Otros content_types
# ---------------------------------------------------------------------------


async def test_query_audio(repo: SqliteTelegramFileRepo):
    await repo.save(_record(file_id="aud", content_type="audio"))
    await repo.save(_record(file_id="pic", content_type="photo"))

    out = await repo.query_recent(
        agent_id="test", channel="telegram", chat_id="-100",
        content_type="audio", count=10,
    )
    assert [r.file_id for r in out] == ["aud"]


async def test_query_count_cero_devuelve_lista_vacia(repo: SqliteTelegramFileRepo):
    await repo.save(_record(file_id="A"))
    out = await repo.query_recent(
        agent_id="test", channel="telegram", chat_id="-100",
        content_type="photo", count=0,
    )
    assert out == []
