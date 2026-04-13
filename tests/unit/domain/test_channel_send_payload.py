"""
Tests unitarios para ChannelSendPayload — T2 del change channel-send-auto-inject.

Cubre:
- Construcción con target + text → válido
- Construcción con target + text + user_id → válido
- user_id por defecto es None
- El campo channel_id ya no existe
"""

from __future__ import annotations

import pytest

from core.domain.entities.task import ChannelSendPayload


def test_construccion_con_target_y_text() -> None:
    """target + text → construcción válida."""
    payload = ChannelSendPayload(target="telegram:123456", text="Hola")
    assert payload.target == "telegram:123456"
    assert payload.text == "Hola"
    assert payload.type == "channel_send"


def test_construccion_con_target_text_y_user_id() -> None:
    """target + text + user_id → construcción válida."""
    payload = ChannelSendPayload(target="telegram:123456", text="Hola", user_id="99")
    assert payload.target == "telegram:123456"
    assert payload.text == "Hola"
    assert payload.user_id == "99"


def test_user_id_por_defecto_none() -> None:
    """user_id es opcional, default None."""
    payload = ChannelSendPayload(target="telegram:123456", text="test")
    assert payload.user_id is None


def test_channel_id_no_existe() -> None:
    """channel_id ya no existe como campo en ChannelSendPayload."""
    payload = ChannelSendPayload(target="telegram:123456", text="test")
    assert not hasattr(payload, "channel_id")


def test_discriminador_type_es_channel_send() -> None:
    """El discriminador type siempre es 'channel_send'."""
    payload = ChannelSendPayload(target="telegram:999", text="msg")
    assert payload.type == "channel_send"
