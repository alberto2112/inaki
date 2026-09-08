"""Resolución del modo debug: ``--debug`` (override de proceso) gana sobre ``app.debug``."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from inaki.observability import is_debug_enabled, set_debug_override


@pytest.fixture(autouse=True)
def _sin_override() -> Iterator[None]:
    set_debug_override(None)
    yield
    set_debug_override(None)


def test_sin_flag_manda_la_config() -> None:
    assert is_debug_enabled(True) is True
    assert is_debug_enabled(False) is False


def test_la_flag_gana_sobre_la_config() -> None:
    set_debug_override(True)
    assert is_debug_enabled(False) is True

    set_debug_override(False)
    assert is_debug_enabled(True) is False
