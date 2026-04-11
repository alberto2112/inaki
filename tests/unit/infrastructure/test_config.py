"""Tests para MemoryConfig — campos digest_size / digest_path y expansión de ~."""

from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.config import (
    AppConfig,
    ChatHistoryConfig,
    EmbeddingConfig,
    MemoryConfig,
    SchedulerConfig,
)


def test_default_digest_size() -> None:
    cfg = MemoryConfig()
    assert cfg.digest_size == 14


def test_default_digest_path_is_absolute() -> None:
    cfg = MemoryConfig()
    assert cfg.digest_path.is_absolute()


def test_default_digest_path_no_tilde() -> None:
    cfg = MemoryConfig()
    assert "~" not in str(cfg.digest_path)


def test_default_digest_path_resolves_to_home() -> None:
    cfg = MemoryConfig()
    home = Path.home()
    assert cfg.digest_path == home / ".inaki" / "mem" / "last_memories.md"


def test_explicit_digest_size() -> None:
    cfg = MemoryConfig(digest_size=20)
    assert cfg.digest_size == 20


def test_explicit_digest_path_expands_tilde() -> None:
    cfg = MemoryConfig(digest_path="~/test.md")
    assert cfg.digest_path.is_absolute()
    assert "~" not in str(cfg.digest_path)


def test_explicit_digest_path_is_path_type() -> None:
    cfg = MemoryConfig(digest_path="~/test.md")
    assert isinstance(cfg.digest_path, Path)


def test_tilde_expansion_happens_at_load_time() -> None:
    """El valor devuelto es siempre un Path absoluto, nunca el string original."""
    cfg = MemoryConfig(digest_size=20, digest_path="~/.inaki/mem/x.md")
    assert cfg.digest_path.is_absolute()
    assert cfg.digest_path == Path.home() / ".inaki" / "mem" / "x.md"


# ---------------------------------------------------------------------------
# Expansión de ~ en todos los path-like strings
# ---------------------------------------------------------------------------

HOME = str(Path.home())


def test_memory_db_path_expands_tilde() -> None:
    cfg = MemoryConfig(db_path="~/.inaki/mem/memories.db")
    assert cfg.db_path == f"{HOME}/.inaki/mem/memories.db"
    assert "~" not in cfg.db_path


def test_chat_history_db_path_expands_tilde() -> None:
    cfg = ChatHistoryConfig(db_path="~/.inaki/mem/context.db")
    assert cfg.db_path == f"{HOME}/.inaki/mem/context.db"
    assert "~" not in cfg.db_path


def test_scheduler_db_path_expands_tilde() -> None:
    cfg = SchedulerConfig(db_path="~/.inaki/scheduler.db")
    assert cfg.db_path == f"{HOME}/.inaki/scheduler.db"
    assert "~" not in cfg.db_path


def test_embedding_model_path_expands_tilde() -> None:
    cfg = EmbeddingConfig(model_path="~/.inaki/models/e5")
    assert cfg.model_path == f"{HOME}/.inaki/models/e5"
    assert "~" not in cfg.model_path


def test_app_data_dir_expands_tilde() -> None:
    cfg = AppConfig(data_dir="~/inaki_ws/logs/")
    assert cfg.data_dir.startswith(HOME)
    assert "~" not in cfg.data_dir


def test_app_models_dir_expands_tilde() -> None:
    cfg = AppConfig(models_dir="~/.inaki/models/")
    assert cfg.models_dir.startswith(HOME)
    assert "~" not in cfg.models_dir


def test_app_ext_dirs_expand_tilde_per_element() -> None:
    cfg = AppConfig(ext_dirs=["ext", "~/.inaki/ext", "/abs/path"])
    assert cfg.ext_dirs[0] == "ext"
    assert cfg.ext_dirs[1] == f"{HOME}/.inaki/ext"
    assert cfg.ext_dirs[2] == "/abs/path"
    assert all("~" not in p for p in cfg.ext_dirs)


def test_relative_paths_without_tilde_are_unchanged() -> None:
    """Paths relativos sin ~ pasan tal cual — no se convierten a absolutos."""
    cfg = MemoryConfig(db_path="data/inaki.db")
    assert cfg.db_path == "data/inaki.db"


# ---------------------------------------------------------------------------
# keep_last_messages — sentinel 0 → fallback 84
# ---------------------------------------------------------------------------

def test_keep_last_messages_default_is_zero_sentinel() -> None:
    cfg = MemoryConfig()
    assert cfg.keep_last_messages == 0
    assert cfg.resolved_keep_last_messages() == 84


def test_keep_last_messages_explicit_value_respected() -> None:
    cfg = MemoryConfig(keep_last_messages=50)
    assert cfg.resolved_keep_last_messages() == 50


def test_keep_last_messages_negative_treated_as_sentinel() -> None:
    cfg = MemoryConfig(keep_last_messages=-1)
    assert cfg.resolved_keep_last_messages() == 84
