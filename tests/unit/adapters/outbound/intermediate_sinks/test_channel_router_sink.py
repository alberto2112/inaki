"""Tests de ChannelRouterIntermediateSink."""

from __future__ import annotations

from unittest.mock import AsyncMock

from adapters.outbound.intermediate_sinks.channel_router import (
    ChannelRouterIntermediateSink,
)


async def test_emit_delega_en_router_send_message():
    router = AsyncMock()
    sink = ChannelRouterIntermediateSink(router=router, target="telegram:42")

    await sink.emit("ok, voy a buscar")

    router.send_message.assert_awaited_once_with("telegram:42", "ok, voy a buscar")


async def test_emit_no_propaga_excepciones_del_router():
    """Un fallo en el router no debe romper el tool loop del agente."""
    router = AsyncMock()
    router.send_message.side_effect = RuntimeError("sink explotó")
    sink = ChannelRouterIntermediateSink(router=router, target="file:///tmp/x.log")

    # No debe levantar
    await sink.emit("mensaje")


async def test_usa_siempre_el_mismo_target():
    router = AsyncMock()
    sink = ChannelRouterIntermediateSink(router=router, target="cli:alberto")

    await sink.emit("uno")
    await sink.emit("dos")

    calls = router.send_message.await_args_list
    assert len(calls) == 2
    assert all(c.args[0] == "cli:alberto" for c in calls)
