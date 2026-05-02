"""Tests para SendToTelegramTool."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from adapters.inbound.telegram.tools.send_to_telegram_tool import SendToTelegramTool
from core.domain.value_objects.channel_context import ChannelContext


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


@pytest.fixture
def sender() -> AsyncMock:
    return AsyncMock()


def _make_tool(
    sender: AsyncMock,
    workspace: Path,
    *,
    ctx: ChannelContext | None,
) -> SendToTelegramTool:
    return SendToTelegramTool(
        sender=sender,
        workspace=workspace,
        containment="strict",
        get_channel_context=lambda: ctx,
    )


def _foto(workspace: Path, name: str = "foto.jpg") -> Path:
    p = workspace / name
    p.write_bytes(b"\xff\xd8\xff")
    return p


# ---------------------------------------------------------------------------
# Camino feliz - cada content_type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ct", ["photo", "audio", "video", "file"])
async def test_envia_individual(sender, workspace, ct):
    file = _foto(workspace, f"x.{ct}")
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(sender, workspace, ctx=ctx)

    result = await tool.execute(content_type=ct, filename=file.name, caption="hola")

    assert result.success is True
    payload = json.loads(result.output)
    assert payload == {"sent": True, "content_type": ct, "count": 1, "chat_id": "-100"}
    sender.send.assert_awaited_once()
    kwargs = sender.send.call_args.kwargs
    assert kwargs["chat_id"] == "-100"
    assert kwargs["content_type"] == ct
    assert kwargs["source"].name == file.name
    assert kwargs["caption"] == "hola"
    sender.send_album.assert_not_awaited()


async def test_envia_album(sender, workspace):
    f1 = _foto(workspace, "a.jpg")
    f2 = _foto(workspace, "b.jpg")
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(sender, workspace, ctx=ctx)

    result = await tool.execute(
        content_type="album", filename=[f1.name, f2.name], caption="grupo"
    )

    assert result.success is True
    payload = json.loads(result.output)
    assert payload["count"] == 2
    sender.send_album.assert_awaited_once()
    kwargs = sender.send_album.call_args.kwargs
    assert [p.name for p in kwargs["sources"]] == ["a.jpg", "b.jpg"]
    assert kwargs["caption"] == "grupo"
    sender.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------


async def test_falla_content_type_invalido(sender, workspace):
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(sender, workspace, ctx=ctx)
    result = await tool.execute(content_type="raro", filename="x.jpg")
    assert result.success is False
    assert "content_type" in result.error.lower()


async def test_falla_album_sin_lista(sender, workspace):
    _foto(workspace, "x.jpg")
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(sender, workspace, ctx=ctx)
    result = await tool.execute(content_type="album", filename="x.jpg")
    assert result.success is False
    assert "lista" in result.error.lower()


async def test_falla_individual_con_lista(sender, workspace):
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(sender, workspace, ctx=ctx)
    result = await tool.execute(content_type="photo", filename=["a.jpg", "b.jpg"])
    assert result.success is False
    assert "string" in result.error.lower() or "lista" in result.error.lower()


async def test_falla_sin_channel_context(sender, workspace):
    _foto(workspace, "x.jpg")
    tool = _make_tool(sender, workspace, ctx=None)
    result = await tool.execute(content_type="photo", filename="x.jpg")
    assert result.success is False


async def test_falla_canal_no_telegram(sender, workspace):
    _foto(workspace, "x.jpg")
    ctx = ChannelContext(channel_type="cli", user_id="local")
    tool = _make_tool(sender, workspace, ctx=ctx)
    result = await tool.execute(content_type="photo", filename="x.jpg")
    assert result.success is False


async def test_falla_archivo_inexistente(sender, workspace):
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(sender, workspace, ctx=ctx)
    result = await tool.execute(content_type="photo", filename="no-existe.jpg")
    assert result.success is False
    assert "no encontrado" in result.error.lower() or "no existe" in result.error.lower()


async def test_album_con_un_solo_path_es_aceptado(sender, workspace):
    f1 = _foto(workspace, "a.jpg")
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(sender, workspace, ctx=ctx)
    result = await tool.execute(content_type="album", filename=[f1.name])
    assert result.success is True
    sender.send_album.assert_awaited_once()


# ---------------------------------------------------------------------------
# Errores de transport
# ---------------------------------------------------------------------------


async def test_transport_timeout_es_retryable(sender, workspace):
    sender.send.side_effect = TimeoutError("timeout")
    _foto(workspace, "x.jpg")
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(sender, workspace, ctx=ctx)
    result = await tool.execute(content_type="photo", filename="x.jpg")
    assert result.success is False
    assert result.retryable is True


async def test_value_error_del_sender_no_retryable(sender, workspace):
    sender.send.side_effect = ValueError("chat id mal")
    _foto(workspace, "x.jpg")
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool = _make_tool(sender, workspace, ctx=ctx)
    result = await tool.execute(content_type="photo", filename="x.jpg")
    assert result.success is False
    assert result.retryable is False
