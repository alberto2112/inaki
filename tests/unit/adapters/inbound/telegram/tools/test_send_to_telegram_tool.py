"""Tests para SendToTelegramTool."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.inbound.telegram.tools.send_to_telegram_tool import SendToTelegramTool
from adapters.outbound.messaging.channel_outbound_registry import ChannelOutboundRegistry
from core.domain.value_objects.channel_context import ChannelContext
from core.domain.value_objects.outbound_kind import OutboundKind


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def _make_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.channel_name = "telegram"
    adapter.capabilities.return_value = set(OutboundKind)
    adapter.send = AsyncMock()
    return adapter


def _make_registry(adapter: MagicMock | None = None) -> ChannelOutboundRegistry:
    registry = ChannelOutboundRegistry()
    if adapter is not None:
        registry.register(adapter)
    return registry


def _make_tool(
    workspace: Path,
    *,
    ctx: ChannelContext | None,
    adapter: MagicMock | None = None,
) -> tuple[SendToTelegramTool, MagicMock | None]:
    ad = adapter if adapter is not None else _make_adapter()
    registry = _make_registry(ad)
    tool = SendToTelegramTool(
        registry=registry,
        workspace=workspace,
        containment="strict",
        get_channel_context=lambda: ctx,
    )
    return tool, ad


def _foto(workspace: Path, name: str = "foto.jpg") -> Path:
    p = workspace / name
    p.write_bytes(b"\xff\xd8\xff")
    return p


# ---------------------------------------------------------------------------
# Camino feliz - cada content_type individual
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ct", "expected_kind"),
    [
        ("photo", OutboundKind.PHOTO),
        ("audio", OutboundKind.AUDIO),
        ("video", OutboundKind.VIDEO),
        ("file", OutboundKind.FILE),
    ],
)
async def test_envia_individual(workspace, ct, expected_kind):
    file = _foto(workspace, f"x.{ct}")
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool, adapter = _make_tool(workspace, ctx=ctx)

    result = await tool.execute(content_type=ct, filename=file.name, caption="hola")

    assert result.success is True
    payload = json.loads(result.output)
    assert payload == {"sent": True, "content_type": ct, "count": 1, "chat_id": "-100"}
    adapter.send.assert_awaited_once()
    kwargs = adapter.send.call_args.kwargs
    assert kwargs["chat_id"] == "-100"
    assert kwargs["kind"] == expected_kind
    assert kwargs["sources"] == [file]
    assert kwargs["caption"] == "hola"


async def test_envia_album(workspace):
    f1 = _foto(workspace, "a.jpg")
    f2 = _foto(workspace, "b.jpg")
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool, adapter = _make_tool(workspace, ctx=ctx)

    result = await tool.execute(content_type="album", filename=[f1.name, f2.name], caption="grupo")

    assert result.success is True
    payload = json.loads(result.output)
    assert payload["count"] == 2
    adapter.send.assert_awaited_once()
    kwargs = adapter.send.call_args.kwargs
    assert kwargs["kind"] == OutboundKind.ALBUM
    assert [p.name for p in kwargs["sources"]] == ["a.jpg", "b.jpg"]
    assert kwargs["caption"] == "grupo"


# ---------------------------------------------------------------------------
# Mapping content_type → OutboundKind para los 5 valores
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ct", "expected_kind"),
    [
        ("photo", OutboundKind.PHOTO),
        ("audio", OutboundKind.AUDIO),
        ("video", OutboundKind.VIDEO),
        ("file", OutboundKind.FILE),
        ("album", OutboundKind.ALBUM),
    ],
)
async def test_mapping_content_type_a_outbound_kind(workspace, ct, expected_kind):
    """Verifica el mapeo de los 5 content_type al OutboundKind correcto."""
    if ct == "album":
        f1 = _foto(workspace, "a.jpg")
        filename = [f1.name]
    else:
        f = _foto(workspace, f"x.{ct}")
        filename = f.name

    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool, adapter = _make_tool(workspace, ctx=ctx)

    await tool.execute(content_type=ct, filename=filename)

    kwargs = adapter.send.call_args.kwargs
    assert kwargs["kind"] == expected_kind


# ---------------------------------------------------------------------------
# Validación de parámetros
# ---------------------------------------------------------------------------


async def test_falla_content_type_invalido(workspace):
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool, adapter = _make_tool(workspace, ctx=ctx)
    result = await tool.execute(content_type="raro", filename="x.jpg")
    assert result.success is False
    assert "content_type" in result.error.lower()


async def test_falla_album_sin_lista(workspace):
    _foto(workspace, "x.jpg")
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool, adapter = _make_tool(workspace, ctx=ctx)
    result = await tool.execute(content_type="album", filename="x.jpg")
    assert result.success is False
    assert "lista" in result.error.lower()


async def test_falla_individual_con_lista(workspace):
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool, adapter = _make_tool(workspace, ctx=ctx)
    result = await tool.execute(content_type="photo", filename=["a.jpg", "b.jpg"])
    assert result.success is False
    assert "string" in result.error.lower() or "lista" in result.error.lower()


async def test_falla_sin_channel_context(workspace):
    _foto(workspace, "x.jpg")
    tool, adapter = _make_tool(workspace, ctx=None)
    result = await tool.execute(content_type="photo", filename="x.jpg")
    assert result.success is False


async def test_falla_canal_no_telegram(workspace):
    _foto(workspace, "x.jpg")
    ctx = ChannelContext(channel_type="cli", user_id="local")
    tool, adapter = _make_tool(workspace, ctx=ctx)
    result = await tool.execute(content_type="photo", filename="x.jpg")
    assert result.success is False


async def test_falla_archivo_inexistente(workspace):
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool, adapter = _make_tool(workspace, ctx=ctx)
    result = await tool.execute(content_type="photo", filename="no-existe.jpg")
    assert result.success is False
    assert "no encontrado" in result.error.lower() or "no existe" in result.error.lower()


async def test_album_con_un_solo_path_es_aceptado(workspace):
    f1 = _foto(workspace, "a.jpg")
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool, adapter = _make_tool(workspace, ctx=ctx)
    result = await tool.execute(content_type="album", filename=[f1.name])
    assert result.success is True
    kwargs = adapter.send.call_args.kwargs
    assert kwargs["kind"] == OutboundKind.ALBUM


# ---------------------------------------------------------------------------
# Canal no registrado → falla no retryable
# ---------------------------------------------------------------------------


async def test_falla_canal_no_registrado(workspace):
    _foto(workspace, "x.jpg")
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    # Registry vacío: ningún adapter
    registry = ChannelOutboundRegistry()
    tool = SendToTelegramTool(
        registry=registry,
        workspace=workspace,
        containment="strict",
        get_channel_context=lambda: ctx,
    )
    result = await tool.execute(content_type="photo", filename="x.jpg")
    assert result.success is False
    assert result.retryable is False
    assert "telegram" in result.error.lower()


# ---------------------------------------------------------------------------
# Errores de transport
# ---------------------------------------------------------------------------


async def test_transport_timeout_es_retryable(workspace):
    _foto(workspace, "x.jpg")
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    adapter = _make_adapter()
    adapter.send.side_effect = TimeoutError("timeout")
    tool, _ = _make_tool(workspace, ctx=ctx, adapter=adapter)
    result = await tool.execute(content_type="photo", filename="x.jpg")
    assert result.success is False
    assert result.retryable is True


async def test_value_error_del_adapter_no_retryable(workspace):
    _foto(workspace, "x.jpg")
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    adapter = _make_adapter()
    adapter.send.side_effect = ValueError("chat id mal")
    tool, _ = _make_tool(workspace, ctx=ctx, adapter=adapter)
    result = await tool.execute(content_type="photo", filename="x.jpg")
    assert result.success is False
    assert result.retryable is False


# ---------------------------------------------------------------------------
# La tool NO persiste directamente en historial — lo hace el adapter
# ---------------------------------------------------------------------------


async def test_no_tiene_history_ni_agent_id(workspace):
    """La tool ya no tiene _history ni _agent_id — el adapter los gestiona."""
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool, _ = _make_tool(workspace, ctx=ctx)

    assert not hasattr(tool, "_history")
    assert not hasattr(tool, "_agent_id")


async def test_adapter_send_llamado_con_sources_list(workspace):
    """El adapter recibe sources como lista de Paths — no source individual."""
    f = _foto(workspace, "x.jpg")
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100")
    tool, adapter = _make_tool(workspace, ctx=ctx)

    await tool.execute(content_type="photo", filename=f.name, caption="cap")

    kwargs = adapter.send.call_args.kwargs
    assert isinstance(kwargs["sources"], list)
    assert len(kwargs["sources"]) == 1
    assert isinstance(kwargs["sources"][0], Path)
