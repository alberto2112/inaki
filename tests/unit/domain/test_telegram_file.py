"""Tests para TelegramFileRecord (validación)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from core.domain.value_objects.telegram_file import TelegramFileRecord


def _kwargs(**overrides):
    base = dict(
        agent_id="ag",
        channel="telegram",
        chat_id="-100",
        content_type="photo",
        file_id="F",
        file_unique_id="U",
        received_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return base


def test_construccion_minima_ok():
    rec = TelegramFileRecord(**_kwargs())
    assert rec.media_group_id is None
    assert rec.caption is None
    assert rec.history_id is None


def test_received_at_naive_falla():
    with pytest.raises(ValidationError, match="UTC"):
        TelegramFileRecord(**_kwargs(received_at=datetime(2026, 5, 1)))


def test_file_id_vacio_falla():
    with pytest.raises(ValidationError):
        TelegramFileRecord(**_kwargs(file_id=""))


def test_chat_id_vacio_falla():
    with pytest.raises(ValidationError):
        TelegramFileRecord(**_kwargs(chat_id="   "))


def test_es_inmutable():
    rec = TelegramFileRecord(**_kwargs())
    with pytest.raises((ValidationError, TypeError)):
        rec.file_id = "X"  # type: ignore[misc]


def test_content_type_invalido_falla():
    with pytest.raises(ValidationError):
        TelegramFileRecord(**_kwargs(content_type="raro"))
