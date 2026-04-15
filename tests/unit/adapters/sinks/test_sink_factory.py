"""Tests para SinkFactory — parseo de prefix → sink instanciado."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from adapters.outbound.sinks.file_sink import FileSink
from adapters.outbound.sinks.null_sink import NullSink
from adapters.outbound.sinks.sink_factory import SinkFactory
from adapters.outbound.sinks.telegram_sink import TelegramSink


def _factory() -> SinkFactory:
    return SinkFactory(get_telegram_bot=lambda: AsyncMock())


def test_sink_factory_resuelve_null() -> None:
    sink = _factory().from_target("null:whatever")
    assert isinstance(sink, NullSink)


def test_sink_factory_resuelve_file() -> None:
    sink = _factory().from_target("file:///tmp/x.log")
    assert isinstance(sink, FileSink)


def test_sink_factory_resuelve_telegram() -> None:
    sink = _factory().from_target("telegram:1234")
    assert isinstance(sink, TelegramSink)


def test_sink_factory_prefix_desconocido_lanza_valueerror() -> None:
    with pytest.raises(ValueError, match="desconocido"):
        _factory().from_target("zzz:algo")


def test_sink_factory_target_sin_prefix_lanza_valueerror() -> None:
    with pytest.raises(ValueError):
        _factory().from_target("sin-prefix")
