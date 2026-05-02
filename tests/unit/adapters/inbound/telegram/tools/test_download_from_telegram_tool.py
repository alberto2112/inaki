"""Tests para DownloadFromTelegramTool."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from adapters.inbound.telegram.tools.download_from_telegram_tool import (
    DownloadFromTelegramTool,
)
from core.domain.value_objects.channel_context import ChannelContext
from core.domain.value_objects.telegram_file import TelegramFileRecord


def _record(**kwargs) -> TelegramFileRecord:
    base = dict(
        agent_id="test",
        channel="telegram",
        chat_id="-100",
        content_type="photo",
        file_id="F-1",
        file_unique_id="U-1",
        media_group_id=None,
        caption=None,
        history_id=None,
        mime_type="image/jpeg",
        received_at=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
    )
    base.update(kwargs)
    return TelegramFileRecord(**base)  # type: ignore[arg-type]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


@pytest.fixture
def repo() -> AsyncMock:
    return AsyncMock()


class _FakeDownloader:
    """Implementación concreta — más predecible que AsyncMock con async side_effect."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Path]] = []
        self.side_effect: Exception | None = None

    async def download(self, *, file_id: str, dest: Path) -> None:
        self.calls.append((file_id, dest))
        if self.side_effect is not None:
            raise self.side_effect
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"binary")

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def assert_not_awaited(self) -> None:
        assert not self.calls, f"esperaba 0 calls, hubo {len(self.calls)}"


@pytest.fixture
def downloader() -> _FakeDownloader:
    return _FakeDownloader()


def _make_tool(
    *, repo, downloader, workspace, ctx: ChannelContext | None
) -> DownloadFromTelegramTool:
    return DownloadFromTelegramTool(
        repo=repo,
        downloader=downloader,
        workspace=workspace,
        agent_id="test",
        get_channel_context=lambda: ctx,
    )


# ---------------------------------------------------------------------------
# Camino feliz
# ---------------------------------------------------------------------------


async def test_descarga_y_devuelve_paths(repo, downloader, workspace):
    repo.query_recent.return_value = [_record(file_unique_id="U1"), _record(file_unique_id="U2")]
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(repo=repo, downloader=downloader, workspace=workspace, ctx=ctx)

    result = await tool.execute(content_type="photo", count=2)

    assert result.success is True
    payload = json.loads(result.output)
    assert payload["count"] == 2
    paths = [Path(f["path"]) for f in payload["files"]]
    assert all(p.exists() for p in paths)
    assert all(p.parent == workspace / "telegram" for p in paths)
    assert downloader.call_count == 2


async def test_cache_hit_no_re_descarga(repo, downloader, workspace):
    repo.query_recent.return_value = [_record(file_unique_id="U1")]
    # Pre-creo el archivo destino
    (workspace / "telegram").mkdir()
    (workspace / "telegram" / "U1.jpg").write_bytes(b"viejo")

    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(repo=repo, downloader=downloader, workspace=workspace, ctx=ctx)

    result = await tool.execute(content_type="photo")
    assert result.success is True
    # No descargó
    downloader.assert_not_awaited()
    # Y mantuvo los bytes viejos
    assert (workspace / "telegram" / "U1.jpg").read_bytes() == b"viejo"


async def test_extension_inferida_de_mime(repo, downloader, workspace):
    repo.query_recent.return_value = [
        _record(file_unique_id="U-png", mime_type="image/png"),
        _record(file_unique_id="U-pdf", content_type="file", mime_type="application/pdf"),
    ]
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(repo=repo, downloader=downloader, workspace=workspace, ctx=ctx)

    result = await tool.execute(content_type="photo")
    assert result.success is True
    payload = json.loads(result.output)
    nombres = sorted(Path(f["path"]).name for f in payload["files"])
    assert nombres == ["U-pdf.pdf", "U-png.png"]


async def test_extension_default_si_mime_desconocido(repo, downloader, workspace):
    repo.query_recent.return_value = [_record(file_unique_id="U", mime_type=None)]
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(repo=repo, downloader=downloader, workspace=workspace, ctx=ctx)

    result = await tool.execute(content_type="photo")
    assert result.success is True
    payload = json.loads(result.output)
    assert Path(payload["files"][0]["path"]).suffix == ".jpg"


async def test_devuelve_received_at_con_z(repo, downloader, workspace):
    repo.query_recent.return_value = [_record(
        received_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
    )]
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(repo=repo, downloader=downloader, workspace=workspace, ctx=ctx)

    result = await tool.execute(content_type="photo")
    payload = json.loads(result.output)
    assert payload["files"][0]["received_at"].endswith("Z")


# ---------------------------------------------------------------------------
# Filtros temporales
# ---------------------------------------------------------------------------


async def test_sin_since_no_pasa_filtros_al_repo(repo, downloader, workspace):
    repo.query_recent.return_value = []
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(repo=repo, downloader=downloader, workspace=workspace, ctx=ctx)

    await tool.execute(content_type="photo")

    kwargs = repo.query_recent.call_args.kwargs
    assert kwargs["since"] is None
    assert kwargs["until"] is None


async def test_until_solo_se_ignora(repo, downloader, workspace):
    repo.query_recent.return_value = []
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(repo=repo, downloader=downloader, workspace=workspace, ctx=ctx)

    await tool.execute(content_type="photo", until="2026-05-01T12:00:00")

    kwargs = repo.query_recent.call_args.kwargs
    assert kwargs["since"] is None
    assert kwargs["until"] is None


async def test_since_solo_aplica_until_default_ahora(repo, downloader, workspace):
    repo.query_recent.return_value = []
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(repo=repo, downloader=downloader, workspace=workspace, ctx=ctx)

    await tool.execute(content_type="photo", since="2026-05-01T10:00:00")

    kwargs = repo.query_recent.call_args.kwargs
    assert kwargs["since"] == datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    assert kwargs["until"] is not None
    assert kwargs["until"].tzinfo == timezone.utc


async def test_since_naive_se_interpreta_utc(repo, downloader, workspace):
    repo.query_recent.return_value = []
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(repo=repo, downloader=downloader, workspace=workspace, ctx=ctx)

    await tool.execute(content_type="photo", since="2026-05-01T08:00:00")

    kwargs = repo.query_recent.call_args.kwargs
    assert kwargs["since"] == datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)


async def test_since_con_offset_se_normaliza_a_utc(repo, downloader, workspace):
    repo.query_recent.return_value = []
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(repo=repo, downloader=downloader, workspace=workspace, ctx=ctx)

    await tool.execute(content_type="photo", since="2026-05-01T10:00:00+02:00")

    kwargs = repo.query_recent.call_args.kwargs
    # +02:00 → 08:00 UTC
    assert kwargs["since"] == datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)


async def test_until_menor_que_since_falla(repo, downloader, workspace):
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(repo=repo, downloader=downloader, workspace=workspace, ctx=ctx)

    result = await tool.execute(
        content_type="photo",
        since="2026-05-02T10:00:00",
        until="2026-05-01T10:00:00",
    )
    assert result.success is False
    assert ">=" in result.error or "since" in result.error.lower()


async def test_iso_invalido_falla(repo, downloader, workspace):
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(repo=repo, downloader=downloader, workspace=workspace, ctx=ctx)

    result = await tool.execute(content_type="photo", since="ayer")
    assert result.success is False
    assert "iso" in result.error.lower()


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------


async def test_falla_content_type_invalido(repo, downloader, workspace):
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(repo=repo, downloader=downloader, workspace=workspace, ctx=ctx)
    result = await tool.execute(content_type="raro")
    assert result.success is False


async def test_count_invalido(repo, downloader, workspace):
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(repo=repo, downloader=downloader, workspace=workspace, ctx=ctx)
    result = await tool.execute(content_type="photo", count=0)
    assert result.success is False


async def test_falla_sin_channel_context(repo, downloader, workspace):
    tool = _make_tool(repo=repo, downloader=downloader, workspace=workspace, ctx=None)
    result = await tool.execute(content_type="photo")
    assert result.success is False


async def test_falla_canal_no_telegram(repo, downloader, workspace):
    ctx = ChannelContext(channel_type="cli", user_id="local")
    tool = _make_tool(repo=repo, downloader=downloader, workspace=workspace, ctx=ctx)
    result = await tool.execute(content_type="photo")
    assert result.success is False


async def test_pasa_chat_id_y_agent_id_al_repo(repo, downloader, workspace):
    repo.query_recent.return_value = []
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-555")
    tool = _make_tool(repo=repo, downloader=downloader, workspace=workspace, ctx=ctx)

    await tool.execute(content_type="audio", count=3)

    kwargs = repo.query_recent.call_args.kwargs
    assert kwargs["agent_id"] == "test"
    assert kwargs["chat_id"] == "-555"
    assert kwargs["channel"] == "telegram"
    assert kwargs["content_type"] == "audio"
    assert kwargs["count"] == 3


async def test_error_de_descarga_es_retryable(repo, workspace):
    repo.query_recent.return_value = [_record()]
    downloader = _FakeDownloader()
    downloader.side_effect = TimeoutError("net down")
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(repo=repo, downloader=downloader, workspace=workspace, ctx=ctx)

    result = await tool.execute(content_type="photo")
    assert result.success is False
    assert result.retryable is True
