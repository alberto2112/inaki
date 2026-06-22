"""Discovery REAL de ``LLMProviderFactory`` (sin mockear ``_load``).

Regresión del refactor que introdujo ``OpenAICompatibleProvider``: el
auto-discovery debe registrar la clase CONCRETA definida en cada módulo, nunca
la base de familia importada (que, al estar arriba del archivo, aparece antes
que la clase local en ``vars(module)``). El guard es el filtro
``attr.__module__ == module.__name__`` en ``_load``.
"""

from __future__ import annotations

import pytest

from adapters.outbound.providers.deepseek import DeepSeekProvider
from adapters.outbound.providers.groq import GroqProvider
from adapters.outbound.providers.openai import OpenAIProvider
from adapters.outbound.providers.openai_compatible import OpenAICompatibleProvider
from adapters.outbound.providers.openrouter import OpenRouterProvider
from infrastructure.factories.llm_factory import LLMProviderFactory


@pytest.fixture
def _fresh_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fuerza un re-scan real del paquete de providers (registry vacío →
    ``_load`` re-escanea). monkeypatch restaura el registry original al salir."""
    monkeypatch.setattr(LLMProviderFactory, "_registry", {})


def test_discovery_registra_la_clase_concreta_de_cada_modulo(_fresh_registry: None) -> None:
    LLMProviderFactory._load()

    registry = LLMProviderFactory._registry
    assert registry["openai"] is OpenAIProvider
    assert registry["groq"] is GroqProvider
    assert registry["openrouter"] is OpenRouterProvider
    assert registry["deepseek"] is DeepSeekProvider


def test_discovery_nunca_registra_la_base_de_familia(_fresh_registry: None) -> None:
    """El footgun: ``OpenAICompatibleProvider`` se importa en cada módulo de la
    familia y aparece en ``vars()`` antes que la clase concreta. El filtro por
    ``__module__`` garantiza que jamás se registre la base en lugar del provider."""
    LLMProviderFactory._load()

    assert OpenAICompatibleProvider not in LLMProviderFactory._registry.values()
    # openai_compatible.py no declara PROVIDER_NAME → ni siquiera es candidato.
    assert "openai_compatible" not in LLMProviderFactory._registry


def test_discovery_incluye_providers_de_contrato_propio(_fresh_registry: None) -> None:
    """anthropic, ollama y openai_responses NO son OpenAI-compat (cuelgan directo
    de ``BaseLLMProvider``) pero deben seguir descubriéndose igual."""
    LLMProviderFactory._load()

    for name in ("anthropic", "ollama", "openai_responses"):
        assert name in LLMProviderFactory._registry
