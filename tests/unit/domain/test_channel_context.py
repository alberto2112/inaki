"""Tests para ChannelContext — value object que encapsula canal y usuario."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.domain.value_objects.channel_context import ChannelContext


# ---------------------------------------------------------------------------
# Construcción válida y routing_key
# ---------------------------------------------------------------------------


def test_construccion_valida_produce_routing_key_correcto() -> None:
    ctx = ChannelContext(channel_type="telegram", user_id="123456")
    assert ctx.routing_key == "telegram:123456"


def test_channel_type_cli_routing_key() -> None:
    ctx = ChannelContext(channel_type="cli", user_id="local")
    assert ctx.routing_key == "cli:local"


def test_channel_type_rest_routing_key() -> None:
    ctx = ChannelContext(channel_type="rest", user_id="anonymous")
    assert ctx.routing_key == "rest:anonymous"


def test_channel_type_daemon_routing_key() -> None:
    ctx = ChannelContext(channel_type="daemon", user_id="system")
    assert ctx.routing_key == "daemon:system"


def test_routing_key_formato_tipo_colon_usuario() -> None:
    ctx = ChannelContext(channel_type="telegram", user_id="999")
    assert ctx.routing_key == f"{ctx.channel_type}:{ctx.user_id}"


# ---------------------------------------------------------------------------
# Inmutabilidad (frozen model)
# ---------------------------------------------------------------------------


def test_no_se_puede_mutar_channel_type() -> None:
    ctx = ChannelContext(channel_type="telegram", user_id="123")
    with pytest.raises((ValidationError, TypeError)):
        ctx.channel_type = "cli"  # type: ignore[misc]


def test_no_se_puede_mutar_user_id() -> None:
    ctx = ChannelContext(channel_type="telegram", user_id="123")
    with pytest.raises((ValidationError, TypeError)):
        ctx.user_id = "999"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Validación: channel_type vacío
# ---------------------------------------------------------------------------


def test_channel_type_vacio_lanza_error() -> None:
    with pytest.raises(ValidationError):
        ChannelContext(channel_type="", user_id="123")


def test_channel_type_solo_espacios_lanza_error() -> None:
    with pytest.raises(ValidationError):
        ChannelContext(channel_type="   ", user_id="123")


def test_channel_type_solo_tabs_lanza_error() -> None:
    with pytest.raises(ValidationError):
        ChannelContext(channel_type="\t\n", user_id="123")


# ---------------------------------------------------------------------------
# Validación: user_id vacío
# ---------------------------------------------------------------------------


def test_user_id_vacio_lanza_error() -> None:
    with pytest.raises(ValidationError):
        ChannelContext(channel_type="telegram", user_id="")


def test_user_id_solo_espacios_lanza_error() -> None:
    with pytest.raises(ValidationError):
        ChannelContext(channel_type="telegram", user_id="   ")


def test_user_id_solo_tabs_lanza_error() -> None:
    with pytest.raises(ValidationError):
        ChannelContext(channel_type="telegram", user_id="\t\n")


# ---------------------------------------------------------------------------
# Validación: ambos campos vacíos
# ---------------------------------------------------------------------------


def test_ambos_campos_vacios_lanza_error() -> None:
    with pytest.raises(ValidationError):
        ChannelContext(channel_type="", user_id="")
