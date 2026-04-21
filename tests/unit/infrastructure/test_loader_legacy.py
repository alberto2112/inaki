"""Tests del rechazo de shape legacy en el loader de configuración."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.domain.errors import ConfigError
from infrastructure.config import load_agent_config, load_global_config


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _valid_global() -> dict:
    return {
        "app": {"name": "Test"},
        "providers": {"openrouter": {"api_key": "K"}},
        "llm": {"provider": "openrouter", "model": "m"},
        "embedding": {"provider": "e5_onnx", "model_dirname": "models/m"},
    }


@pytest.mark.parametrize(
    "section,key",
    [
        ("llm", "api_key"),
        ("llm", "base_url"),
        ("embedding", "api_key"),
        ("embedding", "base_url"),
        ("transcription", "api_key"),
        ("transcription", "base_url"),
    ],
)
def test_global_legacy_llm_embedding_transcription(tmp_path: Path, section: str, key: str) -> None:
    data = _valid_global()
    data.setdefault(section, {"provider": "x", "model": "m"})[key] = "legacy"
    _write(tmp_path / "global.yaml", data)

    with pytest.raises(ConfigError) as exc_info:
        load_global_config(tmp_path)

    message = str(exc_info.value)
    assert f"{section}.{key}" in message
    assert "providers:" in message  # menciona el shape nuevo


def test_global_legacy_memory_llm_api_key(tmp_path: Path) -> None:
    data = _valid_global()
    data["memory"] = {"llm": {"provider": "openai", "api_key": "legacy"}}
    _write(tmp_path / "global.yaml", data)

    with pytest.raises(ConfigError) as exc_info:
        load_global_config(tmp_path)

    assert "memory.llm.api_key" in str(exc_info.value)


def test_global_legacy_memory_llm_base_url(tmp_path: Path) -> None:
    data = _valid_global()
    data["memory"] = {"llm": {"provider": "openai", "base_url": "http://x"}}
    _write(tmp_path / "global.yaml", data)

    with pytest.raises(ConfigError) as exc_info:
        load_global_config(tmp_path)

    assert "memory.llm.base_url" in str(exc_info.value)


def test_global_shape_nuevo_carga_ok(tmp_path: Path) -> None:
    """Happy path — el shape nuevo se carga limpio."""
    _write(tmp_path / "global.yaml", _valid_global())

    cfg, raw = load_global_config(tmp_path)

    assert cfg.llm.provider == "openrouter"
    assert cfg.providers["openrouter"].api_key == "K"


def test_agent_legacy_llm_api_key(tmp_path: Path) -> None:
    _write(tmp_path / "global.yaml", _valid_global())
    _, global_raw = load_global_config(tmp_path)

    agent_dir = tmp_path / "agents"
    _write(
        agent_dir / "a.yaml",
        {
            "id": "a",
            "name": "A",
            "description": "desc",
            "system_prompt": "p",
            "llm": {"api_key": "legacy"},
        },
    )

    with pytest.raises(ConfigError) as exc_info:
        load_agent_config("a", agent_dir, global_raw)

    assert "llm.api_key" in str(exc_info.value)
