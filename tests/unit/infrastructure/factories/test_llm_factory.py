"""Tests de LLMProviderFactory — APIs `create` y `create_from_resolved`.

Tras el refactor a providers top-level:
- ``create(llm_cfg, providers)`` resuelve creds desde el registry según
  ``llm_cfg.provider``.
- ``create_from_resolved(resolved)`` instancia el adapter a partir de un
  ``ResolvedLLMConfig`` ya compuesto (p. ej. por ``MemoryConfig.resolved_llm_config``).

La API vieja ``create_from_llm_config(cfg)`` fue removida — las creds ya no
viven en ``LLMConfig``.
"""

from __future__ import annotations

import pytest

from adapters.outbound.providers.base import BaseLLMProvider
from core.domain.errors import ConfigError
from infrastructure.config import (
    LLMConfig,
    ProviderConfig,
    ResolvedLLMConfig,
)
from infrastructure.factories.llm_factory import LLMProviderFactory


class _FakeProvider(BaseLLMProvider):
    """Provider fake que exige creds — default de ``BaseLLMProvider``."""

    REQUIRES_CREDENTIALS = True

    def __init__(self, cfg: ResolvedLLMConfig) -> None:
        self.cfg = cfg

    async def complete(self, messages, system_prompt, tools=None):  # type: ignore[override]
        raise NotImplementedError

    async def stream(self, messages, system_prompt):  # type: ignore[override]
        raise NotImplementedError
        yield  # pragma: no cover


class _FakeLocalProvider(BaseLLMProvider):
    """Provider fake que NO exige creds (modelo local)."""

    REQUIRES_CREDENTIALS = False

    def __init__(self, cfg: ResolvedLLMConfig) -> None:
        self.cfg = cfg

    async def complete(self, messages, system_prompt, tools=None):  # type: ignore[override]
        raise NotImplementedError

    async def stream(self, messages, system_prompt):  # type: ignore[override]
        raise NotImplementedError
        yield  # pragma: no cover


@pytest.fixture(autouse=True)
def _reset_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aísla cada test: registry controlado, sin reescanear módulos."""
    monkeypatch.setattr(
        LLMProviderFactory,
        "_registry",
        {"fake": _FakeProvider, "fake-local": _FakeLocalProvider},
    )
    monkeypatch.setattr(LLMProviderFactory, "_load", classmethod(lambda cls: None))


# ---------------------------------------------------------------------------
# create(llm_cfg, providers)
# ---------------------------------------------------------------------------


def test_create_resuelve_creds_del_registry() -> None:
    cfg = LLMConfig(provider="fake", model="m")
    providers = {"fake": ProviderConfig(api_key="K", base_url="https://x.y")}

    provider = LLMProviderFactory.create(cfg, providers)

    assert isinstance(provider, _FakeProvider)
    assert provider.cfg.provider == "fake"
    assert provider.cfg.model == "m"
    assert provider.cfg.api_key == "K"
    assert provider.cfg.base_url == "https://x.y"


def test_create_sin_entry_en_registry_y_provider_exige_creds_falla() -> None:
    """Provider con ``REQUIRES_CREDENTIALS=True`` y sin entrada en el registry → ConfigError."""
    cfg = LLMConfig(provider="fake", model="m")

    with pytest.raises(ConfigError) as exc_info:
        LLMProviderFactory.create(cfg, providers={})

    mensaje = str(exc_info.value)
    assert "fake" in mensaje
    assert "credenciales" in mensaje.lower() or "providers.fake" in mensaje


def test_create_sin_entry_en_registry_para_provider_local_es_ok() -> None:
    """Provider con ``REQUIRES_CREDENTIALS=False`` arranca sin entry en providers."""
    cfg = LLMConfig(provider="fake-local", model="m")

    provider = LLMProviderFactory.create(cfg, providers={})

    assert isinstance(provider, _FakeLocalProvider)
    assert provider.cfg.api_key is None


def test_create_provider_desconocido_falla() -> None:
    cfg = LLMConfig(provider="no-existe", model="m")

    with pytest.raises(ValueError) as exc_info:
        LLMProviderFactory.create(cfg, providers={})

    mensaje = str(exc_info.value)
    assert "no-existe" in mensaje
    assert "Disponibles" in mensaje
    assert "fake" in mensaje


def test_create_resuelve_type_override_desde_provider_config() -> None:
    """Una entry con ``type`` explícito permite múltiples claves del mismo adapter."""
    cfg = LLMConfig(provider="fake-work", model="m")
    providers = {"fake-work": ProviderConfig(type="fake", api_key="K2")}

    provider = LLMProviderFactory.create(cfg, providers)

    assert isinstance(provider, _FakeProvider)
    assert provider.cfg.api_key == "K2"
    # La key del registry (el provider que pedía el usuario) se preserva en el resolved.
    assert provider.cfg.provider == "fake-work"


# ---------------------------------------------------------------------------
# create_from_resolved(resolved)
# ---------------------------------------------------------------------------


def test_create_from_resolved_instancia_adapter() -> None:
    resolved = ResolvedLLMConfig(
        provider="fake",
        model="m",
        temperature=0.7,
        max_tokens=1024,
        api_key="K",
    )

    provider = LLMProviderFactory.create_from_resolved(resolved)

    assert isinstance(provider, _FakeProvider)
    assert provider.cfg is resolved


def test_create_from_resolved_sin_api_key_para_provider_que_exige_falla() -> None:
    resolved = ResolvedLLMConfig(
        provider="fake",
        model="m",
        temperature=0.7,
        max_tokens=1024,
        api_key=None,
    )

    with pytest.raises(ConfigError) as exc_info:
        LLMProviderFactory.create_from_resolved(resolved)

    mensaje = str(exc_info.value)
    assert "fake" in mensaje


def test_create_from_resolved_provider_local_sin_api_key_es_ok() -> None:
    resolved = ResolvedLLMConfig(
        provider="fake-local",
        model="m",
        temperature=0.7,
        max_tokens=1024,
        api_key=None,
    )

    provider = LLMProviderFactory.create_from_resolved(resolved)

    assert isinstance(provider, _FakeLocalProvider)
    assert provider.cfg.api_key is None


def test_create_from_resolved_provider_desconocido_falla() -> None:
    resolved = ResolvedLLMConfig(
        provider="ghost",
        model="m",
        temperature=0.7,
        max_tokens=1024,
        api_key="K",
    )

    with pytest.raises(ValueError) as exc_info:
        LLMProviderFactory.create_from_resolved(resolved)

    assert "ghost" in str(exc_info.value)
