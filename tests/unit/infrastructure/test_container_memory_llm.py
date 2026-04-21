"""Tests del wiring del LLM de consolidación en `AgentContainer`.

Cubre el helper estático `AgentContainer._resolve_memory_llm`, que decide si
la consolidación reutiliza el LLM del agente o instancia uno dedicado según
`cfg.memory.llm`. Los tests son aislados: no corren `__init__` completo
(requiere IO real). Mismo patrón que `test_container_transcription.py`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from infrastructure.config import (
    AgentConfig,
    ChatHistoryConfig,
    EmbeddingConfig,
    LLMConfig,
    MemoryConfig,
    MemoryLLMOverride,
)
from infrastructure.container import AgentContainer
from infrastructure.factories.llm_factory import LLMProviderFactory


def _mk_cfg(
    *,
    memory_llm: MemoryLLMOverride | None = None,
    base_model: str = "openai/gpt-oss-120b",
    base_api_key: str = "KEY_BASE",
    base_provider: str = "groq",
) -> AgentConfig:
    return AgentConfig(
        id="test-agent",
        name="Test Agent",
        description="agente de test",
        system_prompt="prompt",
        llm=LLMConfig(
            provider=base_provider,
            model=base_model,
            temperature=0.7,
            max_tokens=2048,
            reasoning_effort="high",
            api_key=base_api_key,
        ),
        embedding=EmbeddingConfig(provider="e5_onnx", model_dirname="models/test"),
        memory=MemoryConfig(db_filename=":memory:", llm=memory_llm),
        chat_history=ChatHistoryConfig(db_filename="/tmp/inaki_test/hist.db"),
    )


def test_resolve_memory_llm_sin_override_reusa_instancia_base() -> None:
    cfg = _mk_cfg(memory_llm=None)
    base_llm = MagicMock()

    resultado = AgentContainer._resolve_memory_llm(cfg, base_llm)

    assert resultado is base_llm


def test_resolve_memory_llm_override_que_coincide_con_base_reusa() -> None:
    """
    Si el override existe pero resuelve a una config idéntica al base
    (todos los campos del override matchean), se reusa la instancia base.
    """
    cfg = _mk_cfg(
        memory_llm=MemoryLLMOverride(
            provider="groq",
            model="openai/gpt-oss-120b",
            temperature=0.7,
            max_tokens=2048,
            reasoning_effort="high",
            api_key="KEY_BASE",
        ),
    )
    base_llm = MagicMock()

    resultado = AgentContainer._resolve_memory_llm(cfg, base_llm)

    assert resultado is base_llm


def test_resolve_memory_llm_override_distinto_instancia_provider_nuevo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _mk_cfg(
        memory_llm=MemoryLLMOverride(
            model="llama-3.3-70b-versatile",
            reasoning_effort=None,
            max_tokens=8192,
        ),
    )
    base_llm = MagicMock(name="base_llm")
    instancia_dedicada = MagicMock(name="dedicada")

    cfg_pasado_a_factory: list[LLMConfig] = []

    def fake_create(llm_cfg: LLMConfig):
        cfg_pasado_a_factory.append(llm_cfg)
        return instancia_dedicada

    monkeypatch.setattr(
        LLMProviderFactory,
        "create_from_llm_config",
        classmethod(lambda cls, llm_cfg: fake_create(llm_cfg)),
    )

    resultado = AgentContainer._resolve_memory_llm(cfg, base_llm)

    assert resultado is instancia_dedicada
    assert resultado is not base_llm
    # La factory recibió la config efectiva ya mergeada (override aplicado).
    assert len(cfg_pasado_a_factory) == 1
    efectiva = cfg_pasado_a_factory[0]
    assert efectiva.model == "llama-3.3-70b-versatile"
    assert efectiva.reasoning_effort is None
    assert efectiva.max_tokens == 8192
    assert efectiva.provider == "groq"  # heredado
    assert efectiva.api_key == "KEY_BASE"  # heredado


def test_resolve_memory_llm_propaga_config_error_de_validacion() -> None:
    from core.domain.errors import ConfigError

    cfg = _mk_cfg(
        memory_llm=MemoryLLMOverride(provider="openai", model="gpt-4o-mini"),
    )
    base_llm = MagicMock()

    with pytest.raises(ConfigError):
        AgentContainer._resolve_memory_llm(cfg, base_llm)
