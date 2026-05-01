"""Tests unitarios para ``BroadcastEmitConfig`` y su integración con ``BroadcastConfig``.

Cubre los 4 escenarios del spec ``config``:
  - defaults cuando no hay bloque ``emit``,
  - flags explícitos (parcial y total),
  - override total a ``false``,
  - tipos inválidos rechazados por validación Pydantic.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from infrastructure.config import BroadcastConfig, BroadcastEmitConfig


# ---------------------------------------------------------------------------
# Defaults — sin bloque emit explícito
# ---------------------------------------------------------------------------


def test_emit_config_defaults_solo_assistant_response_true():
    """Sin override, solo ``assistant_response`` está activo (backward-compat)."""
    cfg = BroadcastEmitConfig()
    assert cfg.assistant_response is True
    assert cfg.user_input_voice is False
    assert cfg.user_input_photo is False


def test_broadcast_config_sin_emit_usa_defaults():
    """Un BroadcastConfig sin bloque ``emit`` instancia un BroadcastEmitConfig default."""
    cfg = BroadcastConfig(port=9000, auth="x" * 16)
    assert isinstance(cfg.emit, BroadcastEmitConfig)
    assert cfg.emit.assistant_response is True
    assert cfg.emit.user_input_voice is False
    assert cfg.emit.user_input_photo is False


# ---------------------------------------------------------------------------
# Flags explícitos
# ---------------------------------------------------------------------------


def test_emit_config_activa_user_input_voice_y_photo_explicitos():
    """Activar ``user_input_voice`` y ``user_input_photo`` mantiene ``assistant_response`` true por default."""
    cfg = BroadcastEmitConfig(user_input_voice=True, user_input_photo=True)
    assert cfg.assistant_response is True
    assert cfg.user_input_voice is True
    assert cfg.user_input_photo is True


def test_broadcast_config_con_emit_explicito():
    """``broadcast.emit`` configurado explícitamente sobreescribe los defaults."""
    cfg = BroadcastConfig(
        port=9000,
        auth="x" * 16,
        emit=BroadcastEmitConfig(user_input_voice=True),
    )
    assert cfg.emit.user_input_voice is True
    assert cfg.emit.user_input_photo is False
    assert cfg.emit.assistant_response is True


# ---------------------------------------------------------------------------
# Override total — modo solo-receiver
# ---------------------------------------------------------------------------


def test_emit_config_todos_false_modo_solo_receiver():
    """Con todos los flags en false, el agente no emite ningún broadcast."""
    cfg = BroadcastEmitConfig(
        assistant_response=False,
        user_input_voice=False,
        user_input_photo=False,
    )
    assert cfg.assistant_response is False
    assert cfg.user_input_voice is False
    assert cfg.user_input_photo is False


# ---------------------------------------------------------------------------
# Validación de tipos
# ---------------------------------------------------------------------------


def test_emit_config_rechaza_string_en_lugar_de_bool():
    """Pydantic rechaza valores no-booleanos en los flags."""
    with pytest.raises(ValidationError):
        BroadcastEmitConfig(user_input_voice="yes")  # type: ignore[arg-type]


def test_emit_config_rechaza_int_no_booleano():
    """Pydantic rechaza enteros no-coercibles a bool."""
    with pytest.raises(ValidationError):
        BroadcastEmitConfig(user_input_photo=2)  # type: ignore[arg-type]
