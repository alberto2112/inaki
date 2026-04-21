"""Tests de LLMProviderFactory — API `create_from_llm_config`.

Complementa a `test_create` (cubierto indirectamente por tests de container).
Acá nos enfocamos en la API introducida por el cambio `memory-llm-override`:
instanciar un provider a partir de una `LLMConfig` directa (sin envolver en
`AgentConfig`).
"""

from __future__ import annotations

import pytest

from core.ports.outbound.llm_port import ILLMProvider
from infrastructure.config import LLMConfig
from infrastructure.factories.llm_factory import LLMProviderFactory


class _FakeProvider(ILLMProvider):
    """Provider fake para aislar la factory del estado real del registry."""

    def __init__(self, cfg: LLMConfig) -> None:
        self.cfg = cfg

    async def complete(self, messages, system_prompt, tools=None):  # type: ignore[override]
        raise NotImplementedError

    def stream(self, messages, system_prompt):  # type: ignore[override]
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _reset_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aísla cada test: registry controlado, sin llamar a `_load`."""
    monkeypatch.setattr(LLMProviderFactory, "_registry", {"fake": _FakeProvider})
    # Evitar que `_load` intente reescanear y pisar el registry fake.
    monkeypatch.setattr(LLMProviderFactory, "_load", classmethod(lambda cls: None))


def test_create_from_llm_config_retorna_instancia_del_provider_correcto() -> None:
    cfg = LLMConfig(provider="fake", model="m", api_key="K")

    provider = LLMProviderFactory.create_from_llm_config(cfg)

    assert isinstance(provider, _FakeProvider)
    assert provider.cfg is cfg


def test_create_from_llm_config_provider_desconocido_falla() -> None:
    cfg = LLMConfig(provider="no-existe", model="m", api_key="K")

    with pytest.raises(ValueError) as exc_info:
        LLMProviderFactory.create_from_llm_config(cfg)

    mensaje = str(exc_info.value)
    assert "no-existe" in mensaje
    assert "Disponibles" in mensaje
    assert "fake" in mensaje
