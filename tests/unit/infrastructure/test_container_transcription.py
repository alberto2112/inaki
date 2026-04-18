"""Tests de wiring de transcription en AgentContainer (task 3.1).

Cubre la validación cruzada entre:
  channels.telegram.voice_enabled  ↔  cfg.transcription

Reglas:
- Agente sin canal `telegram` → no crea provider (`None`), sin error.
- `telegram` presente y `voice_enabled=False` → no crea provider, sin error.
- `telegram` presente y `voice_enabled=True` (o ausente: default True) con
  `cfg.transcription` presente → crea instancia vía factory.
- `telegram` presente y `voice_enabled=True` sin `cfg.transcription` → error claro.

No se testea `__init__` completo (requiere IO real) — se testea el helper
`_resolve_transcription` aislado, como ya hace el resto de `test_container.py`.
"""

from __future__ import annotations

import pytest

from core.domain.errors import IñakiError
from core.ports.outbound.transcription_port import ITranscriptionProvider
from infrastructure.config import (
    AgentConfig,
    ChatHistoryConfig,
    EmbeddingConfig,
    LLMConfig,
    MemoryConfig,
    TranscriptionConfig,
)
from infrastructure.container import AgentContainer


def _mk_cfg(
    *,
    channels: dict | None = None,
    transcription: TranscriptionConfig | None = None,
) -> AgentConfig:
    return AgentConfig(
        id="test-agent",
        name="Test Agent",
        description="agente de test",
        system_prompt="prompt",
        llm=LLMConfig(provider="openrouter", model="m", api_key="k"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_path="models/test"),
        memory=MemoryConfig(db_path=":memory:"),
        chat_history=ChatHistoryConfig(db_path="/tmp/inaki_test/hist.db"),
        transcription=transcription,
        channels=channels or {},
    )


def test_sin_telegram_no_crea_provider() -> None:
    cfg = _mk_cfg(channels={}, transcription=None)
    result = AgentContainer._resolve_transcription(cfg)
    assert result is None


def test_sin_telegram_no_crea_aunque_haya_transcription() -> None:
    """Si el agente no usa telegram, transcription config se ignora: retorna None."""
    cfg = _mk_cfg(
        channels={},
        transcription=TranscriptionConfig(provider="groq", model="m", api_key="k"),
    )
    result = AgentContainer._resolve_transcription(cfg)
    assert result is None


def test_telegram_con_voice_enabled_false_no_crea_provider() -> None:
    cfg = _mk_cfg(
        channels={"telegram": {"token": "t", "voice_enabled": False}},
        transcription=TranscriptionConfig(provider="groq", model="m", api_key="k"),
    )
    result = AgentContainer._resolve_transcription(cfg)
    assert result is None


def test_telegram_voice_enabled_default_true_con_transcription_crea_provider() -> None:
    cfg = _mk_cfg(
        channels={"telegram": {"token": "t"}},  # voice_enabled ausente → default True
        transcription=TranscriptionConfig(provider="groq", model="m", api_key="k"),
    )
    result = AgentContainer._resolve_transcription(cfg)
    assert result is not None
    assert isinstance(result, ITranscriptionProvider)


def test_telegram_voice_enabled_true_explicit_con_transcription_crea_provider() -> None:
    cfg = _mk_cfg(
        channels={"telegram": {"token": "t", "voice_enabled": True}},
        transcription=TranscriptionConfig(provider="groq", model="m", api_key="k"),
    )
    result = AgentContainer._resolve_transcription(cfg)
    assert isinstance(result, ITranscriptionProvider)


def test_telegram_voice_enabled_true_sin_transcription_lanza_error() -> None:
    """voice_enabled=True (default o explícito) exige bloque transcription."""
    cfg = _mk_cfg(
        channels={"telegram": {"token": "t"}},
        transcription=None,
    )
    with pytest.raises(IñakiError) as exc_info:
        AgentContainer._resolve_transcription(cfg)
    msg = str(exc_info.value).lower()
    assert "transcription" in msg
    assert "test-agent" in msg  # debe incluir el agent_id


def test_telegram_voice_enabled_true_explicit_sin_transcription_lanza_error() -> None:
    cfg = _mk_cfg(
        channels={"telegram": {"token": "t", "voice_enabled": True}},
        transcription=None,
    )
    with pytest.raises(IñakiError):
        AgentContainer._resolve_transcription(cfg)
