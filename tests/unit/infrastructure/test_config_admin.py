"""Tests para AdminConfig — configuración del admin server."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from infrastructure.config import AdminConfig, GlobalConfig, load_global_config


# ---------------------------------------------------------------------------
# AdminConfig — defaults
# ---------------------------------------------------------------------------


def test_admin_config_default_port() -> None:
    cfg = AdminConfig()
    assert cfg.port == 6497


def test_admin_config_default_host() -> None:
    cfg = AdminConfig()
    assert cfg.host == "127.0.0.1"


def test_admin_config_default_auth_key_is_none() -> None:
    cfg = AdminConfig()
    assert cfg.auth_key is None


def test_admin_config_override_port() -> None:
    cfg = AdminConfig(port=9000)
    assert cfg.port == 9000


def test_admin_config_override_host() -> None:
    cfg = AdminConfig(host="0.0.0.0")
    assert cfg.host == "0.0.0.0"


def test_admin_config_override_auth_key() -> None:
    cfg = AdminConfig(auth_key="super-secret")
    assert cfg.auth_key == "super-secret"


# ---------------------------------------------------------------------------
# GlobalConfig — admin field
# ---------------------------------------------------------------------------


def test_global_config_has_admin_field() -> None:
    from infrastructure.config import (
        AppConfig,
        ChatHistoryConfig,
        EmbeddingConfig,
        LLMConfig,
        MemoryConfig,
    )

    cfg = GlobalConfig(
        app=AppConfig(),
        llm=LLMConfig(),
        embedding=EmbeddingConfig(),
        memory=MemoryConfig(),
        chat_history=ChatHistoryConfig(),
    )
    assert hasattr(cfg, "admin")
    assert isinstance(cfg.admin, AdminConfig)


def test_global_config_admin_uses_defaults_when_absent() -> None:
    from infrastructure.config import (
        AppConfig,
        ChatHistoryConfig,
        EmbeddingConfig,
        LLMConfig,
        MemoryConfig,
    )

    cfg = GlobalConfig(
        app=AppConfig(),
        llm=LLMConfig(),
        embedding=EmbeddingConfig(),
        memory=MemoryConfig(),
        chat_history=ChatHistoryConfig(),
    )
    assert cfg.admin.port == 6497
    assert cfg.admin.host == "127.0.0.1"
    assert cfg.admin.auth_key is None


# ---------------------------------------------------------------------------
# load_global_config — parsea sección admin
# ---------------------------------------------------------------------------


def test_load_global_config_parses_admin_section(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    global_yaml = config_dir / "global.yaml"
    global_yaml.write_text(
        yaml.safe_dump({
            "app": {"name": "Test"},
            "admin": {"port": 7777, "host": "0.0.0.0"},
        }),
        encoding="utf-8",
    )
    cfg, _ = load_global_config(config_dir)
    assert cfg.admin.port == 7777
    assert cfg.admin.host == "0.0.0.0"


def test_load_global_config_absent_admin_uses_defaults(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    global_yaml = config_dir / "global.yaml"
    global_yaml.write_text(
        yaml.safe_dump({"app": {"name": "Test"}}),
        encoding="utf-8",
    )
    cfg, _ = load_global_config(config_dir)
    assert cfg.admin.port == 6497
    assert cfg.admin.auth_key is None


def test_load_global_config_admin_auth_key_from_secrets(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "global.yaml").write_text(
        yaml.safe_dump({"app": {"name": "Test"}, "admin": {"port": 6497}}),
        encoding="utf-8",
    )
    (config_dir / "global.secrets.yaml").write_text(
        yaml.safe_dump({"admin": {"auth_key": "my-secret-key"}}),
        encoding="utf-8",
    )
    cfg, _ = load_global_config(config_dir)
    assert cfg.admin.auth_key == "my-secret-key"
    assert cfg.admin.port == 6497
