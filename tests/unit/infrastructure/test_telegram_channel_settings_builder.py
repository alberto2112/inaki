"""Guard de ``build_telegram_channel_settings`` — la traducción config → VO del bot.

Esta lógica vivía en ``TelegramBot.__init__`` (donde los tests del bot la
ejercían de rebote) y se mudó al composition root en la Fase 2 del refactor de
config. Los tests del bot ahora alimentan VOs prefabricados, así que la
traducción quedó sin guard propio: estos casos límite fijan la semántica que
el bot tenía con los ``.get()``.
"""

from __future__ import annotations

from infrastructure.config import TelegramChannelConfig
from infrastructure.container import build_telegram_channel_settings


def _cfg(**kwargs) -> TelegramChannelConfig:
    return TelegramChannelConfig(token="T", **kwargs)


def test_sin_canal_devuelve_defaults() -> None:
    vo = build_telegram_channel_settings(None)

    assert vo.token == ""
    assert vo.groups.behavior == "mention"
    assert vo.emit.assistant_response is True


def test_los_ids_se_normalizan_a_tuplas_de_str() -> None:
    """El YAML trae ints; el bot siempre comparó strings."""
    vo = build_telegram_channel_settings(
        _cfg(allowed_user_ids=[123, 456], allowed_chat_ids=[-1001])
    )

    assert vo.allowed_user_ids == ("123", "456")
    assert vo.allowed_chat_ids == ("-1001",)


def test_groups_ausente_hereda_reactions_del_canal() -> None:
    vo = build_telegram_channel_settings(_cfg(reactions=True))

    assert vo.groups.reactions is True, "sin bloque groups, reactions hereda del canal"
    assert vo.groups.behavior == "mention"


def test_groups_reactions_none_hereda_y_false_explicito_pisa() -> None:
    """La trampa: ``None`` es "heredar", ``False`` explícito es un override.

    Un chequeo falsy en vez de ``is None`` los confundiría — era la semántica
    exacta del ``__init__`` viejo del bot."""
    hereda = build_telegram_channel_settings(_cfg(reactions=True, groups={"reactions": None}))
    pisa = build_telegram_channel_settings(_cfg(reactions=True, groups={"reactions": False}))

    assert hereda.groups.reactions is True
    assert pisa.groups.reactions is False


def test_delays_sin_declarar_quedan_none_para_el_default_del_modulo() -> None:
    """La constante del delay es del adapter: el VO NO la duplica."""
    vo = build_telegram_channel_settings(_cfg(groups={"behavior": "autonomous"}))

    assert vo.groups.min_delay is None and vo.groups.max_delay is None
    assert vo.groups.behavior == "autonomous"


def test_emit_sin_broadcast_usa_los_defaults() -> None:
    vo = build_telegram_channel_settings(_cfg())

    assert vo.emit.assistant_response is True
    assert vo.emit.user_input_voice is False


def test_emit_declarado_viaja_al_vo() -> None:
    vo = build_telegram_channel_settings(
        _cfg(
            broadcast={
                "enabled": True,
                "server": {"port": 6499},
                "auth": "s" * 16,
                "emit": {"assistant_response": False, "user_input_voice": True},
            }
        )
    )

    assert vo.emit.assistant_response is False
    assert vo.emit.user_input_voice is True


def test_rate_limiter_y_window_viajan_enteros() -> None:
    vo = build_telegram_channel_settings(
        _cfg(groups={"rate_limiter": 3, "rate_limiter_window": 300})
    )

    assert (vo.groups.rate_limiter, vo.groups.rate_limiter_window) == (3, 300)
