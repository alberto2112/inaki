"""Tests del port ITranscriptionProvider (task 1.2).

Contrato estructural:
- Es una ABC (no se puede instanciar sin implementar transcribe()).
- transcribe es abstracto y async, con la firma acordada en el spec.
- Una implementación concreta puede instanciarse y retorna str.
"""

from __future__ import annotations

import inspect

import pytest

from core.ports.outbound.transcription_port import ITranscriptionProvider


def test_port_es_abc_y_no_se_puede_instanciar_directamente() -> None:
    with pytest.raises(TypeError):
        ITranscriptionProvider()  # type: ignore[abstract]


def test_transcribe_es_async_y_abstracto() -> None:
    assert getattr(ITranscriptionProvider.transcribe, "__isabstractmethod__", False) is True
    assert inspect.iscoroutinefunction(ITranscriptionProvider.transcribe)


def test_transcribe_expone_la_firma_del_spec() -> None:
    sig = inspect.signature(ITranscriptionProvider.transcribe)
    params = list(sig.parameters.keys())
    # self + audio + mime + language
    assert params == ["self", "audio", "mime", "language"]


async def test_subclase_concreta_se_puede_instanciar_y_devuelve_str() -> None:
    class _Dummy(ITranscriptionProvider):
        async def transcribe(
            self, audio: bytes, mime: str, language: str | None = None
        ) -> str:
            return "texto"

    provider = _Dummy()
    resultado = await provider.transcribe(b"abc", "audio/ogg", None)
    assert resultado == "texto"
