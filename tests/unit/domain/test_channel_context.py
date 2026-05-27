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


# ---------------------------------------------------------------------------
# chat_id opcional
# ---------------------------------------------------------------------------


def test_chat_id_opcional_default_none() -> None:
    ctx = ChannelContext(channel_type="cli", user_id="local")
    assert ctx.chat_id is None


def test_chat_id_explicito_se_preserva() -> None:
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-1001234")
    assert ctx.chat_id == "-1001234"


def test_chat_id_no_afecta_routing_key() -> None:
    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-99")
    assert ctx.routing_key == "telegram:42"


def test_chat_id_vacio_lanza_error() -> None:
    with pytest.raises(ValidationError):
        ChannelContext(channel_type="telegram", user_id="42", chat_id="")


def test_chat_id_solo_espacios_lanza_error() -> None:
    with pytest.raises(ValidationError):
        ChannelContext(channel_type="telegram", user_id="42", chat_id="   ")


# ---------------------------------------------------------------------------
# Sender fields opcionales (sender_name, username, first_name, last_name)
# ---------------------------------------------------------------------------


def test_sender_fields_default_none() -> None:
    """Por defecto los 4 campos de identidad del remitente quedan en None."""
    ctx = ChannelContext(channel_type="telegram", user_id="42")
    assert ctx.sender_name is None
    assert ctx.username is None
    assert ctx.first_name is None
    assert ctx.last_name is None


def test_sender_fields_explicitos_se_preservan() -> None:
    ctx = ChannelContext(
        channel_type="telegram",
        user_id="42",
        sender_name="Juan Pérez (@juan_dev)",
        username="juan_dev",
        first_name="Juan",
        last_name="Pérez",
    )
    assert ctx.sender_name == "Juan Pérez (@juan_dev)"
    assert ctx.username == "juan_dev"
    assert ctx.first_name == "Juan"
    assert ctx.last_name == "Pérez"


def test_sender_name_vacio_lanza_error() -> None:
    with pytest.raises(ValidationError):
        ChannelContext(channel_type="telegram", user_id="42", sender_name="")


def test_sender_name_solo_espacios_lanza_error() -> None:
    with pytest.raises(ValidationError):
        ChannelContext(channel_type="telegram", user_id="42", sender_name="   ")


def test_username_vacio_lanza_error() -> None:
    with pytest.raises(ValidationError):
        ChannelContext(channel_type="telegram", user_id="42", username="")


def test_first_name_vacio_lanza_error() -> None:
    with pytest.raises(ValidationError):
        ChannelContext(channel_type="telegram", user_id="42", first_name="")


def test_last_name_vacio_lanza_error() -> None:
    with pytest.raises(ValidationError):
        ChannelContext(channel_type="telegram", user_id="42", last_name="")


def test_sender_fields_independientes() -> None:
    """Cada campo puede setearse de forma independiente — caso real: usuario sin last_name."""
    ctx = ChannelContext(
        channel_type="telegram",
        user_id="42",
        sender_name="Juan (@juan_dev)",
        username="juan_dev",
        first_name="Juan",
        # last_name omitido a propósito
    )
    assert ctx.first_name == "Juan"
    assert ctx.last_name is None


def test_sender_fields_no_afectan_routing_key() -> None:
    ctx = ChannelContext(
        channel_type="telegram",
        user_id="42",
        sender_name="Juan",
        first_name="Juan",
    )
    assert ctx.routing_key == "telegram:42"
