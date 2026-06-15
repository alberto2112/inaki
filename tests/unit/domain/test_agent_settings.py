"""Tests de los settings VOs de core — lógica de digest scope y keep_last.

Esta lógica vivía en ``infrastructure/config.py`` (``MemoriesConfig``) y se movió
a ``core/domain/value_objects/agent_settings.py`` junto con el refactor purista:
los use cases reciben settings VOs en lugar del ``AgentConfig`` completo.
"""

from __future__ import annotations

from core.domain.value_objects.agent_settings import (
    KEEP_LAST_MESSAGES_FALLBACK,
    ConsolidationSettings,
    MemorySettings,
    sanitize_digest_scope,
)


# ---------------------------------------------------------------------------
# sanitize_digest_scope
# ---------------------------------------------------------------------------


def test_sanitize_digest_scope_none_or_empty_becomes_default() -> None:
    assert sanitize_digest_scope(None) == "default"
    assert sanitize_digest_scope("") == "default"


def test_sanitize_digest_scope_alphanumeric_passthrough() -> None:
    assert sanitize_digest_scope("telegram") == "telegram"
    assert sanitize_digest_scope("abc123") == "abc123"


def test_sanitize_digest_scope_preserves_dashes_and_underscores() -> None:
    # Telegram suele usar IDs negativos tipo "-1001234567"
    assert sanitize_digest_scope("-1001234567") == "-1001234567"
    assert sanitize_digest_scope("foo_bar") == "foo_bar"


def test_sanitize_digest_scope_replaces_unsafe_chars() -> None:
    assert sanitize_digest_scope("foo:bar/baz") == "foo_bar_baz"
    assert sanitize_digest_scope("path with spaces") == "path_with_spaces"
    assert sanitize_digest_scope("héllo") == "h_llo"  # tilde no es ascii safe


# ---------------------------------------------------------------------------
# MemorySettings.resolved_digest_path
# ---------------------------------------------------------------------------


def test_resolved_digest_path_substitutes_placeholders() -> None:
    settings = MemorySettings(digest_template="mem/digest_{channel}_{chat_id}.md")
    p = settings.resolved_digest_path("telegram", "-1001")
    assert p.name == "digest_telegram_-1001.md"


def test_resolved_digest_path_sanitizes_components() -> None:
    settings = MemorySettings(digest_template="mem/digest_{channel}_{chat_id}.md")
    p = settings.resolved_digest_path("tele:gram", None)
    assert p.name == "digest_tele_gram_default.md"


def test_resolved_digest_path_legacy_filename_without_placeholders() -> None:
    """Config legacy sin placeholders → un único archivo para todos los scopes."""
    settings = MemorySettings(digest_template="mem/single.md")
    p1 = settings.resolved_digest_path("telegram", "1")
    p2 = settings.resolved_digest_path("cli", None)
    assert p1 == p2  # mismo archivo — comportamiento de compatibilidad temporal


# ---------------------------------------------------------------------------
# MemorySettings.resolved_keep_last_messages — sentinel 0 → fallback
# ---------------------------------------------------------------------------


def test_keep_last_messages_default_is_zero_sentinel() -> None:
    settings = MemorySettings()
    assert settings.consolidation.keep_last_messages == 0
    assert settings.consolidation.resolved_keep_last_messages() == KEEP_LAST_MESSAGES_FALLBACK


def test_keep_last_messages_explicit_value_respected() -> None:
    settings = MemorySettings(consolidation=ConsolidationSettings(keep_last_messages=50))
    assert settings.consolidation.resolved_keep_last_messages() == 50


def test_keep_last_messages_negative_treated_as_sentinel() -> None:
    settings = MemorySettings(consolidation=ConsolidationSettings(keep_last_messages=-1))
    assert settings.consolidation.resolved_keep_last_messages() == KEEP_LAST_MESSAGES_FALLBACK
