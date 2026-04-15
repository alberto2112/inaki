"""Tests para NullSink — descarta silenciosamente."""

from __future__ import annotations

from adapters.outbound.sinks.null_sink import NullSink
from core.domain.value_objects.dispatch_result import DispatchResult


def test_null_sink_tiene_prefix_null() -> None:
    assert NullSink.prefix == "null"


async def test_null_sink_send_retorna_dispatch_result() -> None:
    sink = NullSink()
    result = await sink.send("null:whatever", "hola")
    assert isinstance(result, DispatchResult)


async def test_null_sink_propaga_target_como_original_y_resolved() -> None:
    sink = NullSink()
    result = await sink.send("null:ignored", "texto")
    assert result.original_target == "null:ignored"
    assert result.resolved_target == "null:ignored"


async def test_null_sink_no_lanza_ningun_error() -> None:
    sink = NullSink()
    # No asserts sobre side effects — por definición no hay ninguno.
    await sink.send("null:", "")
