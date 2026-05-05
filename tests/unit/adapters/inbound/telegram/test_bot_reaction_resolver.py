"""``_resolve_reaction``: emoji string → ``ReactionTypeEmoji`` válido o ``None``.

Bug raíz: python-telegram-bot envuelve cualquier string fuera de
``telegram.constants.ReactionEmoji`` como ``ReactionTypeCustomEmoji`` (con
el emoji crudo como ``custom_emoji_id``), causando HTTP 400 en Telegram. La
solución es pasar siempre ``ReactionTypeEmoji`` explícito y mapear los
emojis "deseados pero inválidos" a equivalentes del whitelist.
"""

from __future__ import annotations

import pytest
from telegram import ReactionTypeEmoji
from telegram.constants import ReactionEmoji

from adapters.inbound.telegram.bot import _resolve_reaction


def test_emoji_in_whitelist_passes_through() -> None:
    """``👀`` es uno de los emojis válidos para reacciones de bot."""
    result = _resolve_reaction("👀")
    assert isinstance(result, ReactionTypeEmoji)
    assert result.emoji == "👀"


@pytest.mark.parametrize(
    "input_emoji,expected_emoji",
    [
        ("❌", "👎"),
        ("✅", "👍"),
        ("👁", "👀"),
        ("🔊", "👀"),
    ],
)
def test_invalid_emoji_resolves_to_fallback(input_emoji: str, expected_emoji: str) -> None:
    result = _resolve_reaction(input_emoji)
    assert isinstance(result, ReactionTypeEmoji)
    assert result.emoji == expected_emoji


def test_emoji_with_no_mapping_returns_none() -> None:
    """Un emoji random fuera del whitelist y sin fallback explícito → None.

    Los callers (``_set_reaction``, ``_set_group_reaction``) silencian en este
    caso en lugar de mandar algo que Telegram rechazaría con 400.
    """
    # 🪐 (planet/saturn) confirmado fuera del whitelist y sin fallback definido.
    assert _resolve_reaction("🪐") is None


def test_all_fallback_targets_are_themselves_valid() -> None:
    """Los valores del dict fallback deben estar todos en el whitelist —
    si no, el helper devolvería None aunque haya mapeo."""
    from adapters.inbound.telegram.bot import _REACTION_EMOJI_FALLBACK

    valid = set(ReactionEmoji)
    for src, dst in _REACTION_EMOJI_FALLBACK.items():
        assert dst in valid, (
            f"Fallback {src!r} → {dst!r} pero {dst!r} no está en ReactionEmoji whitelist"
        )
