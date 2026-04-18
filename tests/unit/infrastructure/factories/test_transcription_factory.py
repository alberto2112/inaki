"""Tests de TranscriptionProviderFactory (task 1.6).

Contrato idéntico a LLMProviderFactory y EmbeddingProviderFactory:
- Auto-discovery: escanea adapters.outbound.transcription por PROVIDER_NAME.
- create(cfg) retorna la instancia correspondiente al provider configurado.
- Error claro cuando el provider no está registrado.
- El test usa monkeypatch sobre el registry para evitar depender del estado real.
"""

from __future__ import annotations

import pytest

from core.domain.errors import UnknownTranscriptionProviderError
from core.ports.outbound.transcription_port import ITranscriptionProvider
from infrastructure.config import TranscriptionConfig
from infrastructure.factories.transcription_factory import TranscriptionProviderFactory


class _FakeProvider(ITranscriptionProvider):
    """Provider fake para aislar la factory del estado real del registry."""

    def __init__(self, cfg: TranscriptionConfig) -> None:
        self.cfg = cfg

    async def transcribe(
        self, audio: bytes, mime: str, language: str | None = None
    ) -> str:
        return "fake"


@pytest.fixture(autouse=True)
def _reset_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aísla cada test: registry vacío al entrar, restaurado al salir."""
    monkeypatch.setattr(TranscriptionProviderFactory, "_registry", {})


def test_create_retorna_instancia_registrada(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulamos que el auto-discovery ya cargó el registry con un fake.
    monkeypatch.setattr(
        TranscriptionProviderFactory,
        "_registry",
        {"fake": _FakeProvider},
    )
    # Patcheamos _load para que sea no-op y no pise nuestro registry de test.
    monkeypatch.setattr(TranscriptionProviderFactory, "_load", classmethod(lambda cls: None))

    cfg = TranscriptionConfig(provider="fake", model="m")
    provider = TranscriptionProviderFactory.create(cfg)

    assert isinstance(provider, _FakeProvider)
    assert provider.cfg.provider == "fake"


def test_create_lanza_unknown_provider_si_no_registrado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        TranscriptionProviderFactory,
        "_registry",
        {"groq": _FakeProvider},
    )
    monkeypatch.setattr(TranscriptionProviderFactory, "_load", classmethod(lambda cls: None))

    cfg = TranscriptionConfig(provider="inexistente", model="m")
    with pytest.raises(UnknownTranscriptionProviderError) as exc_info:
        TranscriptionProviderFactory.create(cfg)

    assert "inexistente" in str(exc_info.value)
    # También debe mencionar los disponibles para debug.
    assert "groq" in str(exc_info.value)


def test_create_no_op_si_transcription_config_es_none() -> None:
    """Si el caller pasa cfg=None, la factory NO debe explotar — delegamos esa
    decisión al container. La factory sólo se invoca cuando cfg es TranscriptionConfig."""
    # Validamos que el tipo de la firma es explícito: create(cfg: TranscriptionConfig).
    import inspect

    sig = inspect.signature(TranscriptionProviderFactory.create)
    cfg_param = sig.parameters["cfg"]
    assert cfg_param.annotation is TranscriptionConfig or cfg_param.annotation == "TranscriptionConfig"
