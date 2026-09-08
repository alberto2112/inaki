"""Tests de BufferingIntermediateSink."""

from __future__ import annotations

from core.ports.outbound.channel_port import BufferingIntermediateSink


async def test_buffering_sink_empieza_vacio():
    sink = BufferingIntermediateSink()
    assert sink.messages == []


async def test_buffering_sink_acumula_en_orden():
    sink = BufferingIntermediateSink()
    await sink.emit("uno")
    await sink.emit("dos")
    await sink.emit("tres")
    assert sink.messages == ["uno", "dos", "tres"]


async def test_buffering_sink_messages_devuelve_copia():
    """`messages` no debe permitir mutar el estado interno del sink."""
    sink = BufferingIntermediateSink()
    await sink.emit("a")
    snapshot = sink.messages
    snapshot.append("contaminación")
    assert sink.messages == ["a"]


# ---------------------------------------------------------------------------
# OutboundIntermediateSink — la vista "intermedio" del outbound
# ---------------------------------------------------------------------------


async def test_outbound_intermediate_sink_manda_texto_sin_historial() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from core.domain.value_objects.outbound_kind import OutboundKind
    from core.ports.outbound.channel_port import OutboundIntermediateSink

    outbound = MagicMock()
    outbound.send = AsyncMock()

    await OutboundIntermediateSink(outbound, "42").emit("voy a buscar...")

    outbound.send.assert_awaited_once_with(
        chat_id="42", kind=OutboundKind.TEXT, text="voy a buscar...", record_history=False
    )


async def test_outbound_intermediate_sink_traga_fallos_del_transporte(caplog) -> None:
    import logging
    from unittest.mock import AsyncMock, MagicMock

    from core.ports.outbound.channel_port import OutboundIntermediateSink

    outbound = MagicMock()
    outbound.channel_name = "telegram"
    outbound.send = AsyncMock(side_effect=RuntimeError("red caída"))

    with caplog.at_level(logging.WARNING):
        await OutboundIntermediateSink(outbound, "42").emit("x")  # no lanza

    assert "Intermedio no entregado" in caplog.text
