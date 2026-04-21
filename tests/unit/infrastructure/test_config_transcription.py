"""Tests de TranscriptionConfig y su integración en GlobalConfig/AgentConfig.

Tras el refactor a providers top-level:
- ``TranscriptionConfig`` ya NO tiene ``api_key`` / ``base_url``. Las creds
  viven en el registry ``providers:`` nivel superior.
- El merge 4-layer del agente aporta el registry ``providers`` (global + override
  del agente + secrets).

Cubre:
- Defaults de TranscriptionConfig (sin creds).
- Composición manual de GlobalConfig con/ sin transcripcion.
- _render_default_global_yaml incluye el bloque `transcription:` sin api_key.
- Merge 4-layer con el bloque ``providers`` aportando creds para transcription.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from infrastructure.config import (
    EmbeddingConfig,
    GlobalConfig,
    LLMConfig,
    TranscriptionConfig,
    _render_default_global_yaml,
    load_agent_config,
    load_global_config,
)


def test_defaults_de_transcription_config() -> None:
    cfg = TranscriptionConfig()
    assert cfg.provider == "groq"
    assert cfg.model == "whisper-large-v3-turbo"
    assert cfg.language is None
    assert cfg.timeout_seconds == 60
    assert cfg.max_audio_mb == 25


def test_transcription_config_acepta_override_de_parametros_de_feature() -> None:
    """Los params de feature (timeout, max_audio, language, model) se overridean.
    Las creds NO viven acá — están en `providers:` top-level.
    """
    cfg = TranscriptionConfig(
        provider="openai",
        model="whisper-1",
        language="es",
        timeout_seconds=30,
        max_audio_mb=10,
    )
    assert cfg.provider == "openai"
    assert cfg.language == "es"
    assert cfg.timeout_seconds == 30
    assert cfg.max_audio_mb == 10


def test_global_config_transcription_es_opcional_y_default_none() -> None:
    gc = GlobalConfig(
        app=__import__("infrastructure.config", fromlist=["AppConfig"]).AppConfig(),
        llm=LLMConfig(),
        embedding=EmbeddingConfig(),
        memory=__import__("infrastructure.config", fromlist=["MemoryConfig"]).MemoryConfig(),
        chat_history=__import__(
            "infrastructure.config", fromlist=["ChatHistoryConfig"]
        ).ChatHistoryConfig(),
    )
    assert gc.transcription is None


def test_render_default_global_yaml_incluye_bloque_transcription() -> None:
    yaml_text = _render_default_global_yaml()
    data = yaml.safe_load(
        "\n".join(line for line in yaml_text.splitlines() if not line.startswith("#"))
    )
    assert "transcription" in data
    assert data["transcription"]["provider"] == "groq"
    assert data["transcription"]["model"] == "whisper-large-v3-turbo"
    # api_key NUNCA se serializa en el default (ya no existe en TranscriptionConfig).
    assert "api_key" not in data["transcription"]
    assert "base_url" not in data["transcription"]


def test_load_global_config_parsea_bloque_transcription(tmp_path: Path) -> None:
    """Global define ``transcription`` (feature-only) y ``providers`` aporta creds."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "global.yaml").write_text(
        "app: {name: Test}\n"
        "llm: {provider: groq, model: m}\n"
        "embedding: {provider: e5_onnx, model_dirname: /tmp/m}\n"
        "memory: {db_filename: ':memory:'}\n"
        "chat_history: {db_filename: /tmp/h.db}\n"
        "transcription: {provider: groq, model: whisper-large-v3}\n"
        "providers:\n"
        "  groq: {api_key: sk-g}\n",
        encoding="utf-8",
    )
    global_cfg, raw = load_global_config(cfg_dir)
    assert global_cfg.transcription is not None
    assert global_cfg.transcription.provider == "groq"
    assert global_cfg.transcription.model == "whisper-large-v3"
    # Creds viven en el registry de providers — indexado por la key del vendor.
    assert "groq" in global_cfg.providers
    assert global_cfg.providers["groq"].api_key == "sk-g"
    assert raw["providers"]["groq"]["api_key"] == "sk-g"


def test_load_agent_config_mergea_transcription_y_providers_4_layers(tmp_path: Path) -> None:
    """Global define provider+model; el agente overridea model; secrets aporta api_key al registry."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    (cfg_dir / "global.yaml").write_text(
        "app: {name: Test}\n"
        "llm: {provider: groq, model: m}\n"
        "embedding: {provider: e5_onnx, model_dirname: /tmp/m}\n"
        "memory: {db_filename: ':memory:'}\n"
        "chat_history: {db_filename: /tmp/h.db}\n"
        "transcription: {provider: groq, model: whisper-large-v3-turbo}\n",
        encoding="utf-8",
    )
    _, global_raw = load_global_config(cfg_dir)

    (agents_dir / "dev.yaml").write_text(
        "id: dev\n"
        "name: Dev\n"
        "description: d\n"
        "system_prompt: p\n"
        "transcription: {model: whisper-large-v3}\n",
        encoding="utf-8",
    )
    (agents_dir / "dev.secrets.yaml").write_text(
        "providers:\n  groq: {api_key: sk-agent}\n",
        encoding="utf-8",
    )

    agent = load_agent_config("dev", agents_dir, global_raw)
    assert agent is not None
    assert agent.transcription is not None
    # provider heredado, model overrideado por agente
    assert agent.transcription.provider == "groq"
    assert agent.transcription.model == "whisper-large-v3"
    # api_key vive en el registry — mergeado desde los secrets del agente.
    assert "groq" in agent.providers
    assert agent.providers["groq"].api_key == "sk-agent"


def test_agent_config_sin_transcription_queda_en_none(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    (cfg_dir / "global.yaml").write_text(
        "app: {name: Test}\n"
        "llm: {provider: groq, model: m}\n"
        "embedding: {provider: e5_onnx, model_dirname: /tmp/m}\n"
        "memory: {db_filename: ':memory:'}\n"
        "chat_history: {db_filename: /tmp/h.db}\n",
        encoding="utf-8",
    )
    _, global_raw = load_global_config(cfg_dir)

    (agents_dir / "dev.yaml").write_text(
        "id: dev\nname: Dev\ndescription: d\nsystem_prompt: p\n",
        encoding="utf-8",
    )
    (agents_dir / "dev.secrets.yaml").write_text("", encoding="utf-8")

    agent = load_agent_config("dev", agents_dir, global_raw)
    assert agent is not None
    assert agent.transcription is None
