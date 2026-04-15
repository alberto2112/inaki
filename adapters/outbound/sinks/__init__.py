"""Sinks outbound del scheduler — destinos concretos para routing de mensajes."""

from adapters.outbound.sinks.file_sink import FileSink
from adapters.outbound.sinks.null_sink import NullSink
from adapters.outbound.sinks.sink_factory import SinkFactory
from adapters.outbound.sinks.telegram_sink import TelegramSink

__all__ = ["FileSink", "NullSink", "SinkFactory", "TelegramSink"]
