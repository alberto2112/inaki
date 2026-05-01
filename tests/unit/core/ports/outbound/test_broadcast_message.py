"""Tests unitarios para BroadcastMessage.

Verifica el shape extendido con event_type (Literal de 3 valores), sender,
y el campo `content` (renombrado desde `message`). Cada test ejerce el
constructor del dataclass y asserta sobre los atributos producidos.
"""

from __future__ import annotations

import pytest

from core.ports.outbound.broadcast_port import BroadcastMessage


def test_assistant_response_construye_con_sender_vacio_por_default():
    """assistant_response no requiere sender — default vacío."""
    msg = BroadcastMessage(
        timestamp=1000.0,
        agent_id="agente_a",
        chat_id="chat_1",
        event_type="assistant_response",
        content="hola",
    )
    assert msg.event_type == "assistant_response"
    assert msg.sender == ""
    assert msg.content == "hola"


def test_user_input_voice_lleva_sender_y_content():
    """user_input_voice carga el nombre humano y la transcripción."""
    msg = BroadcastMessage(
        timestamp=1000.0,
        agent_id="agente_a",
        chat_id="chat_1",
        event_type="user_input_voice",
        content="cuánto es 5+5",
        sender="alberto",
    )
    assert msg.event_type == "user_input_voice"
    assert msg.sender == "alberto"
    assert msg.content == "cuánto es 5+5"


def test_user_input_photo_lleva_sender_y_descripcion():
    """user_input_photo carga el nombre humano y la descripción de escena."""
    msg = BroadcastMessage(
        timestamp=1000.0,
        agent_id="agente_a",
        chat_id="chat_1",
        event_type="user_input_photo",
        content="persona caminando hacia la cámara",
        sender="alberto",
    )
    assert msg.event_type == "user_input_photo"
    assert msg.sender == "alberto"
    assert msg.content == "persona caminando hacia la cámara"


def test_message_es_inmutable():
    """BroadcastMessage es frozen — no se pueden modificar sus campos."""
    msg = BroadcastMessage(
        timestamp=1000.0,
        agent_id="a",
        chat_id="c",
        event_type="assistant_response",
        content="x",
    )
    with pytest.raises(Exception):  # FrozenInstanceError o similar
        msg.content = "y"  # type: ignore[misc]


def test_chat_id_siempre_es_string():
    """chat_id debe ser string — consistente con TelegramChannelConfig."""
    msg = BroadcastMessage(
        timestamp=1000.0,
        agent_id="a",
        chat_id="-100123",
        event_type="assistant_response",
        content="x",
    )
    assert isinstance(msg.chat_id, str)
    assert msg.chat_id == "-100123"
