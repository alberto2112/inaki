"""SinkFactory — parsea un string de target y devuelve el sink concreto.

Stateless respecto al target: la única dependencia inyectada es el callable
``get_telegram_bot`` que ``TelegramSink`` necesita para resolver el bot lazy.
"""

from __future__ import annotations

from collections.abc import Callable

from adapters.outbound.sinks.file_sink import FileSink
from adapters.outbound.sinks.null_sink import NullSink
from adapters.outbound.sinks.telegram_sink import TelegramSink
from core.ports.outbound.outbound_sink_port import IOutboundSink


class SinkFactory:
    """Fabrica sinks a partir de strings con prefix ``<prefix>:<destino>``."""

    def __init__(self, get_telegram_bot: Callable[[], object | None]) -> None:
        self._get_telegram_bot = get_telegram_bot

    def from_target(self, target: str) -> IOutboundSink:
        prefix, sep, _ = target.partition(":")
        if not sep:
            raise ValueError(f"Target sin prefix: '{target}'")
        if prefix == "null":
            return NullSink()
        if prefix == "file":
            return FileSink()
        if prefix == "telegram":
            return TelegramSink(get_telegram_bot=self._get_telegram_bot)
        raise ValueError(f"Prefix de sink desconocido: '{prefix}'")
