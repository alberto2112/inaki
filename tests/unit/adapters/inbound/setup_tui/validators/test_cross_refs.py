"""
Tests unitarios para los validadores de referencias cruzadas.

No requieren I/O ni fixtures de sistema de archivos — todo es puro.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from adapters.inbound.setup_tui.validators.cross_refs import (
    _PROVIDERS_LOCALES,
    validate_default_agent_exists,
    validate_global_config,
    validate_provider_reference,
)
from core.domain.errors import ReferenciaInvalidaError


# ---------------------------------------------------------------------------
# Helpers de fixture
# ---------------------------------------------------------------------------


def _make_global_config(
    default_agent: str = "general",
    llm_provider: str = "openrouter",
    emb_provider: str = "e5_onnx",
    transcription: Any = None,
    memory_llm_provider: str | None = None,
) -> Any:
    """
    Construye un objeto que imita la estructura de ``GlobalConfig`` usando
    ``SimpleNamespace`` — sin importar de ``infrastructure.config``.
    """
    memory_llm = None
    if memory_llm_provider is not None:
        memory_llm = SimpleNamespace(provider=memory_llm_provider)

    return SimpleNamespace(
        app=SimpleNamespace(default_agent=default_agent),
        llm=SimpleNamespace(provider=llm_provider),
        embedding=SimpleNamespace(provider=emb_provider),
        transcription=transcription,
        memory=SimpleNamespace(llm=memory_llm),
    )


def _make_transcription(provider: str = "groq") -> Any:
    return SimpleNamespace(provider=provider)


# ---------------------------------------------------------------------------
# Tests: validate_default_agent_exists
# ---------------------------------------------------------------------------


def test_validate_default_agent_existe_ok() -> None:
    """No lanza error si el default_agent está en la lista."""
    validate_default_agent_exists("general", ["dev", "general", "researcher"])


def test_validate_default_agent_no_existe_levanta_error() -> None:
    """Lanza ReferenciaInvalidaError si el agente no está disponible."""
    with pytest.raises(ReferenciaInvalidaError) as exc_info:
        validate_default_agent_exists("fantasma", ["dev", "general"])

    error = exc_info.value
    assert error.campo == "app.default_agent"
    assert error.valor == "fantasma"
    assert "dev" in error.disponibles
    assert "general" in error.disponibles


def test_validate_default_agent_lista_vacia_levanta_error() -> None:
    """Si no hay agentes, cualquier valor es inválido."""
    with pytest.raises(ReferenciaInvalidaError):
        validate_default_agent_exists("general", [])


def test_validate_default_agent_disponibles_ordenados() -> None:
    """Los disponibles en el error deben estar ordenados."""
    with pytest.raises(ReferenciaInvalidaError) as exc_info:
        validate_default_agent_exists("nope", ["zz", "aa", "mm"])

    assert exc_info.value.disponibles == ["aa", "mm", "zz"]


# ---------------------------------------------------------------------------
# Tests: validate_provider_reference
# ---------------------------------------------------------------------------


def test_validate_provider_reference_existe_ok() -> None:
    """No lanza error si el provider está en la lista."""
    validate_provider_reference("openrouter", ["openrouter", "groq", "openai"])


def test_validate_provider_reference_no_existe_levanta_error() -> None:
    """Lanza ReferenciaInvalidaError si el provider no está disponible."""
    with pytest.raises(ReferenciaInvalidaError) as exc_info:
        validate_provider_reference("groq", ["openrouter", "openai"])

    error = exc_info.value
    assert error.campo == "providers"
    assert error.valor == "groq"
    assert "openai" in error.disponibles
    assert "openrouter" in error.disponibles


def test_validate_provider_reference_disponibles_ordenados() -> None:
    """Los disponibles en el error deben estar ordenados alfabéticamente."""
    with pytest.raises(ReferenciaInvalidaError) as exc_info:
        validate_provider_reference("nope", ["zzz", "aaa"])

    assert exc_info.value.disponibles == ["aaa", "zzz"]


# ---------------------------------------------------------------------------
# Tests: validate_global_config — casos OK
# ---------------------------------------------------------------------------


def test_validate_global_config_ok_sin_transcription() -> None:
    """Configuración válida sin bloque de transcripción pasa sin error."""
    cfg = _make_global_config(
        default_agent="general",
        llm_provider="openrouter",
        emb_provider="e5_onnx",
    )
    validate_global_config(
        cfg=cfg,
        available_agents=["general", "dev"],
        available_providers=["openrouter", "groq"],
    )


def test_validate_global_config_ok_con_transcription() -> None:
    """Configuración válida con transcripción pasa sin error."""
    cfg = _make_global_config(
        default_agent="general",
        llm_provider="openrouter",
        emb_provider="e5_onnx",
        transcription=_make_transcription(provider="groq"),
    )
    validate_global_config(
        cfg=cfg,
        available_agents=["general"],
        available_providers=["openrouter", "groq"],
    )


def test_validate_global_config_ok_con_memory_llm_override() -> None:
    """Override de LLM en memory con provider válido pasa sin error."""
    cfg = _make_global_config(
        default_agent="general",
        llm_provider="openrouter",
        emb_provider="e5_onnx",
        memory_llm_provider="groq",
    )
    validate_global_config(
        cfg=cfg,
        available_agents=["general"],
        available_providers=["openrouter", "groq"],
    )


def test_validate_global_config_emb_local_no_requiere_provider() -> None:
    """e5_onnx y ollama son providers locales y no se validan contra el registry."""
    for local_provider in _PROVIDERS_LOCALES:
        cfg = _make_global_config(
            default_agent="general",
            llm_provider="openrouter",
            emb_provider=local_provider,
        )
        validate_global_config(
            cfg=cfg,
            available_agents=["general"],
            available_providers=["openrouter"],  # local no está en la lista, pero no importa
        )


# ---------------------------------------------------------------------------
# Tests: validate_global_config — errores
# ---------------------------------------------------------------------------


def test_validate_global_config_default_agent_invalido() -> None:
    """Falla si app.default_agent apunta a un agente inexistente."""
    cfg = _make_global_config(default_agent="fantasma")
    with pytest.raises(ReferenciaInvalidaError) as exc_info:
        validate_global_config(
            cfg=cfg,
            available_agents=["general"],
            available_providers=["openrouter"],
        )
    assert exc_info.value.campo == "app.default_agent"
    assert exc_info.value.valor == "fantasma"


def test_validate_global_config_llm_provider_invalido() -> None:
    """Falla si llm.provider no está en el registry de providers."""
    cfg = _make_global_config(
        default_agent="general",
        llm_provider="groq_inexistente",
        emb_provider="e5_onnx",
    )
    with pytest.raises(ReferenciaInvalidaError) as exc_info:
        validate_global_config(
            cfg=cfg,
            available_agents=["general"],
            available_providers=["openrouter"],
        )
    assert exc_info.value.campo == "llm.provider"
    assert exc_info.value.valor == "groq_inexistente"


def test_validate_global_config_emb_provider_invalido() -> None:
    """Falla si embedding.provider no es local y no está en el registry."""
    cfg = _make_global_config(
        default_agent="general",
        llm_provider="openrouter",
        emb_provider="proveedor_desconocido",
    )
    with pytest.raises(ReferenciaInvalidaError) as exc_info:
        validate_global_config(
            cfg=cfg,
            available_agents=["general"],
            available_providers=["openrouter"],
        )
    assert exc_info.value.campo == "embedding.provider"
    assert exc_info.value.valor == "proveedor_desconocido"


def test_validate_global_config_transcription_provider_invalido() -> None:
    """Falla si transcription.provider no está en el registry."""
    cfg = _make_global_config(
        default_agent="general",
        llm_provider="openrouter",
        emb_provider="e5_onnx",
        transcription=_make_transcription(provider="proveedor_roto"),
    )
    with pytest.raises(ReferenciaInvalidaError) as exc_info:
        validate_global_config(
            cfg=cfg,
            available_agents=["general"],
            available_providers=["openrouter", "groq"],
        )
    assert exc_info.value.campo == "transcription.provider"
    assert exc_info.value.valor == "proveedor_roto"


def test_validate_global_config_memory_llm_provider_invalido() -> None:
    """Falla si memory.llm.provider override apunta a provider inexistente."""
    cfg = _make_global_config(
        default_agent="general",
        llm_provider="openrouter",
        emb_provider="e5_onnx",
        memory_llm_provider="proveedor_fantasma",
    )
    with pytest.raises(ReferenciaInvalidaError) as exc_info:
        validate_global_config(
            cfg=cfg,
            available_agents=["general"],
            available_providers=["openrouter", "groq"],
        )
    assert exc_info.value.campo == "memory.llm.provider"
    assert exc_info.value.valor == "proveedor_fantasma"


def test_validate_global_config_primer_error_gana() -> None:
    """
    validate_global_config lanza el PRIMER error encontrado.
    El orden de validación es: default_agent → llm.provider → embedding.provider.
    """
    cfg = _make_global_config(
        default_agent="agente_roto",
        llm_provider="provider_roto",
        emb_provider="e5_onnx",
    )
    with pytest.raises(ReferenciaInvalidaError) as exc_info:
        validate_global_config(
            cfg=cfg,
            available_agents=["general"],
            available_providers=["openrouter"],
        )
    # El primer error debe ser default_agent, no llm.provider
    assert exc_info.value.campo == "app.default_agent"


def test_validate_global_config_memory_llm_none_no_valida() -> None:
    """Si memory.llm es None (sin override), no se valida el provider."""
    cfg = _make_global_config(
        default_agent="general",
        llm_provider="openrouter",
        emb_provider="e5_onnx",
        memory_llm_provider=None,  # sin override
    )
    validate_global_config(
        cfg=cfg,
        available_agents=["general"],
        available_providers=["openrouter"],
    )


def test_validate_global_config_transcription_none_no_valida() -> None:
    """Si transcription es None (sin bloque), no se valida el provider."""
    cfg = _make_global_config(
        default_agent="general",
        llm_provider="openrouter",
        emb_provider="e5_onnx",
        transcription=None,
    )
    validate_global_config(
        cfg=cfg,
        available_agents=["general"],
        available_providers=["openrouter"],
    )
