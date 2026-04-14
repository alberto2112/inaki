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

from unittest.mock import AsyncMock, MagicMock

from adapters.outbound.scheduler.dispatch_adapters import ChannelSenderAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_minimal_app_container() -> object:
    """
    Construye un AppContainer usando __new__ para evitar el constructor completo.
    Inyecta los atributos mínimos necesarios para probar T5.
    """
    from infrastructure.container import AppContainer

    app = AppContainer.__new__(AppContainer)
    app._telegram_bots: dict = {}
    return app


# ---------------------------------------------------------------------------
# Test 1 — ChannelSenderAdapter almacena callable en _get_telegram_bot
# ---------------------------------------------------------------------------


def test_channel_sender_adapter_almacena_callable() -> None:
    """
    ChannelSenderAdapter debe almacenar el callable get_telegram_bot internamente.
    Al inspeccionarlo, debe ser callable.
    """
    bot_mock = AsyncMock()
    get_bot = MagicMock(return_value=bot_mock)

    adapter = ChannelSenderAdapter(get_telegram_bot=get_bot)

    assert adapter._get_telegram_bot is get_bot, (
        "_get_telegram_bot debe referenciar exactamente el callable pasado al constructor"
    )
    assert callable(adapter._get_telegram_bot), (
        "_get_telegram_bot debe ser callable"
    )


# ---------------------------------------------------------------------------
# Test 2 — AppContainer._telegram_bots inicializa vacío
# ---------------------------------------------------------------------------


def test_app_container_telegram_bots_inicializa_vacio() -> None:
    """
    AppContainer debe tener _telegram_bots inicializado como dict vacío.
    """
    app = _build_minimal_app_container()

    assert hasattr(app, "_telegram_bots"), (
        "AppContainer debe tener atributo _telegram_bots"
    )
    assert isinstance(app._telegram_bots, dict), (
        "_telegram_bots debe ser un diccionario"
    )
    assert len(app._telegram_bots) == 0, (
        "_telegram_bots debe inicializar vacío"
    )


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

    assert resultado is bot_mock, (
        "_get_telegram_bot() debe devolver el bot registrado"
    )


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


# ---------------------------------------------------------------------------
# Test 6 — ChannelSenderAdapter con callable de AppContainer refleja bots dinámicos
# ---------------------------------------------------------------------------


def test_channel_sender_adapter_callable_refleja_bots_dinamicos() -> None:
    """
    Al usar el callable de AppContainer._get_telegram_bot en ChannelSenderAdapter,
    el resultado refleja el estado actual de _telegram_bots (lazy evaluation).
    """
    app = _build_minimal_app_container()
    adapter = ChannelSenderAdapter(get_telegram_bot=app._get_telegram_bot)

    # Sin bots → None
    assert adapter._get_telegram_bot() is None

    # Registrar bot
    bot_mock = MagicMock()
    app.register_telegram_bot("agent-z", bot_mock)

    # Ahora debe devolver el bot
    assert adapter._get_telegram_bot() is bot_mock, (
        "El callable debe reflejar el estado dinámico de _telegram_bots"
    )
