"""Tests de BufferingIntermediateSink."""

from __future__ import annotations

from adapters.outbound.intermediate_sinks.buffering import BufferingIntermediateSink


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
