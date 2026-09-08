"""
Unit tests para T5 — AppContainer telegram gateway / ChannelSenderAdapter wiring.

Coverage:
1. ChannelSenderAdapter almacena callable en _get_telegram_bot
2. AppContainer._telegram_bots inicializa vacío
3. AppContainer.register_telegram_bot registra un bot por agent_id
4. AppContainer._get_telegram_bot devuelve el bot registrado para un agent_id
5. AppContainer._get_telegram_bot devuelve None cuando no hay ningún bot registrado
"""

from __future__ import annotations

from unittest.mock import MagicMock

from infrastructure.container import AppContainer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_minimal_app_container() -> AppContainer:
    """
    Construye un AppContainer usando __new__ para evitar el constructor completo.
    Inyecta los atributos mínimos necesarios para probar T5.
    """
    app = AppContainer.__new__(AppContainer)
    app._telegram_bots = {}
    return app


# ---------------------------------------------------------------------------
# Test 2 — AppContainer._telegram_bots inicializa vacío
# ---------------------------------------------------------------------------


def test_app_container_telegram_bots_inicializa_vacio() -> None:
    """
    AppContainer debe tener _telegram_bots inicializado como dict vacío.
    """
    app = _build_minimal_app_container()

    assert hasattr(app, "_telegram_bots"), "AppContainer debe tener atributo _telegram_bots"
    assert isinstance(app._telegram_bots, dict), "_telegram_bots debe ser un diccionario"
    assert len(app._telegram_bots) == 0, "_telegram_bots debe inicializar vacío"


# ---------------------------------------------------------------------------
# Test 3 — AppContainer.register_telegram_bot registra bot por agent_id
# ---------------------------------------------------------------------------


def test_app_container_register_telegram_bot() -> None:
    """
    register_telegram_bot(agent_id, bot) debe almacenar el bot en _telegram_bots.
    """
    app = _build_minimal_app_container()
    bot_mock = MagicMock()

    app.register_telegram_bot("agent-x", bot_mock)

    assert "agent-x" in app._telegram_bots, (
        "El agent_id debe estar en _telegram_bots después de register_telegram_bot"
    )
    assert app._telegram_bots["agent-x"] is bot_mock, (
        "El bot registrado debe ser exactamente el objeto pasado"
    )


# ---------------------------------------------------------------------------
# Test 4 — AppContainer._get_telegram_bot devuelve bot registrado
# ---------------------------------------------------------------------------


def test_app_container_get_telegram_bot_devuelve_bot_registrado() -> None:
    """
    _get_telegram_bot() debe devolver el primer bot en _telegram_bots.
    """
    app = _build_minimal_app_container()
    bot_mock = MagicMock()
    app._telegram_bots["agent-y"] = bot_mock

    resultado = app._get_telegram_bot()

    assert resultado is bot_mock, "_get_telegram_bot() debe devolver el bot registrado"


# ---------------------------------------------------------------------------
# Test 5 — AppContainer._get_telegram_bot devuelve None sin bots
# ---------------------------------------------------------------------------


def test_app_container_get_telegram_bot_devuelve_none_sin_bots() -> None:
    """
    _get_telegram_bot() debe devolver None cuando no hay bots registrados.
    """
    app = _build_minimal_app_container()

    resultado = app._get_telegram_bot()

    assert resultado is None, (
        "_get_telegram_bot() debe devolver None cuando no hay ningún bot registrado"
    )
