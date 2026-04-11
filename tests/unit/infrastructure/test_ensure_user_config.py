"""Tests para `ensure_user_config()` y el render del template."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from infrastructure.config import (
    _render_default_global_yaml,
    ensure_user_config,
)


def test_first_run_creates_all_artifacts(tmp_path: Path) -> None:
    config_dir = tmp_path / ".inaki" / "config"
    agents_dir = tmp_path / ".inaki" / "agents"

    ensure_user_config(config_dir, agents_dir)

    assert config_dir.is_dir()
    assert agents_dir.is_dir()
    assert (config_dir / "global.yaml").is_file()
    assert (config_dir / "global.secrets.yaml").is_file()


def test_is_idempotent(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    agents_dir = tmp_path / "agents"

    ensure_user_config(config_dir, agents_dir)

    global_yaml = config_dir / "global.yaml"
    secrets_yaml = config_dir / "global.secrets.yaml"
    global_yaml.write_text("user: edited\n", encoding="utf-8")
    secrets_yaml.write_text("custom: secret\n", encoding="utf-8")

    ensure_user_config(config_dir, agents_dir)

    assert global_yaml.read_text(encoding="utf-8") == "user: edited\n"
    assert secrets_yaml.read_text(encoding="utf-8") == "custom: secret\n"


def test_agents_dir_created_empty(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    agents_dir = tmp_path / "agents"

    ensure_user_config(config_dir, agents_dir)

    assert list(agents_dir.iterdir()) == []


def test_permission_error_re_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(self: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(Path, "mkdir", _boom)

    with pytest.raises(PermissionError):
        ensure_user_config(tmp_path / "config", tmp_path / "agents")


def test_render_default_global_yaml_is_parseable() -> None:
    rendered = _render_default_global_yaml()

    parsed = yaml.safe_load(rendered)

    assert isinstance(parsed, dict)
    for key in ("app", "llm", "embedding", "memory", "history", "skills", "tools", "scheduler"):
        assert key in parsed, f"missing top-level key: {key}"


def test_render_default_global_yaml_excludes_api_keys() -> None:
    rendered = _render_default_global_yaml()
    parsed = yaml.safe_load(rendered)

    assert "api_key" not in parsed["llm"]
    assert "api_key" not in parsed["embedding"]


def test_render_default_global_yaml_has_header() -> None:
    rendered = _render_default_global_yaml()

    assert rendered.startswith("#")
    assert "Iñaki" in rendered
