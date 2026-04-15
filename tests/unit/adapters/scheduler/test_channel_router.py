"""Tests para ChannelRouter — cascada de resolución de targets."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from adapters.outbound.scheduler.dispatch_adapters import ChannelRouter
from adapters.outbound.sinks.file_sink import FileSink
from adapters.outbound.sinks.null_sink import NullSink
from adapters.outbound.sinks.sink_factory import SinkFactory
from adapters.outbound.sinks.telegram_sink import TelegramSink
from core.domain.value_objects.dispatch_result import DispatchResult
from infrastructure.config import ChannelFallbackConfig


def _mk_router(
    native_prefixes: dict[str, object] | None = None,
    fallback: ChannelFallbackConfig | None = None,
) -> ChannelRouter:
    native = native_prefixes or {}
    cfg = fallback or ChannelFallbackConfig()
    factory = SinkFactory(get_telegram_bot=lambda: AsyncMock())
    return ChannelRouter(
        native_sinks=native,
        fallback_config=cfg,
        sink_factory=factory.from_target,
    )


async def test_router_nativo_prefix_telegram_usa_sink_nativo() -> None:
    tg = AsyncMock(spec=TelegramSink)
    tg.send = AsyncMock(
        return_value=DispatchResult(original_target="telegram:1", resolved_target="telegram:1")
    )
    router = _mk_router(native_prefixes={"telegram": tg})
    result = await router.send_message("telegram:1", "hola")
    assert result.original_target == "telegram:1"
    assert result.resolved_target == "telegram:1"
    tg.send.assert_awaited_once()


async def test_router_cli_sin_nativo_usa_override() -> None:
    cfg = ChannelFallbackConfig(overrides={"cli": "null:"})
    router = _mk_router(fallback=cfg)
    result = await router.send_message("cli:local", "texto")
    assert result.original_target == "cli:local"
    assert result.resolved_target == "null:"


async def test_router_cli_sin_nativo_ni_override_usa_default() -> None:
    cfg = ChannelFallbackConfig(default="null:")
    router = _mk_router(fallback=cfg)
    result = await router.send_message("rest:x", "texto")
    assert result.original_target == "rest:x"
    assert result.resolved_target == "null:"


async def test_router_sin_ningun_fallback_usa_hardcoded(tmp_path) -> None:
    # El hardcoded por defecto apunta a /tmp/inaki-schedule-output.log;
    # sobreescribimos via constructor para no tocar el sistema real.
    destino = tmp_path / "hardcoded.log"
    factory = SinkFactory(get_telegram_bot=lambda: None)
    router = ChannelRouter(
        native_sinks={},
        fallback_config=ChannelFallbackConfig(),
        sink_factory=factory.from_target,
        hardcoded_fallback=f"file://{destino}",
    )
    result = await router.send_message("daemon:x", "hola")
    assert result.original_target == "daemon:x"
    assert result.resolved_target == f"file://{destino}"
    assert destino.exists()


async def test_router_override_tiene_prioridad_sobre_default() -> None:
    cfg = ChannelFallbackConfig(default="null:default", overrides={"cli": "null:override"})
    router = _mk_router(fallback=cfg)
    result = await router.send_message("cli:algo", "t")
    assert result.resolved_target == "null:override"


async def test_router_native_tiene_prioridad_sobre_override() -> None:
    tg = AsyncMock(spec=TelegramSink)
    tg.send = AsyncMock(
        return_value=DispatchResult(original_target="telegram:9", resolved_target="telegram:9")
    )
    cfg = ChannelFallbackConfig(overrides={"telegram": "null:"})
    router = _mk_router(native_prefixes={"telegram": tg}, fallback=cfg)
    result = await router.send_message("telegram:9", "x")
    assert result.resolved_target == "telegram:9"


async def test_router_preserva_original_target_tras_fallback() -> None:
    cfg = ChannelFallbackConfig(default="null:")
    router = _mk_router(fallback=cfg)
    result = await router.send_message("cli:lo-que-sea", "t")
    # Incluso si el sink delegado devolviera el target resuelto como original,
    # el router debe sobrescribir el original_target con el que llegó.
    assert result.original_target == "cli:lo-que-sea"


async def test_router_target_sin_prefix_lanza_valueerror() -> None:
    router = _mk_router()
    with pytest.raises(ValueError):
        await router.send_message("sin-prefix", "x")
