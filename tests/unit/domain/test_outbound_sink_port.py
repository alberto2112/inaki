"""Tests para IOutboundSink — puerto outbound de envío a canales."""

from __future__ import annotations

import pytest

from core.domain.value_objects.dispatch_result import DispatchResult
from core.ports.outbound.outbound_sink_port import IOutboundSink


class _FakeSink(IOutboundSink):
    """Implementación mínima para probar el contrato del puerto."""

    prefix = "fake"

    def __init__(self) -> None:
        self.llamadas: list[tuple[str, str]] = []

    async def send(self, target: str, text: str) -> DispatchResult:
        self.llamadas.append((target, text))
        return DispatchResult(original_target=target, resolved_target=target)


async def test_implementacion_concreta_satisface_puerto() -> None:
    sink = _FakeSink()
    assert isinstance(sink, IOutboundSink)


async def test_send_retorna_dispatch_result() -> None:
    sink = _FakeSink()
    resultado = await sink.send("fake:x", "hola")
    assert isinstance(resultado, DispatchResult)
    assert resultado.original_target == "fake:x"


async def test_prefix_es_atributo_de_clase() -> None:
    assert _FakeSink.prefix == "fake"


def test_no_se_puede_instanciar_puerto_abstracto() -> None:
    with pytest.raises(TypeError):
        IOutboundSink()  # type: ignore[abstract]
