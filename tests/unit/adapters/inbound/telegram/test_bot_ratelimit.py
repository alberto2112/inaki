"""Tests para `/ratelimit` — override en runtime del rate limiter del broadcast.

Cubre:
- Mostrar valores actuales sin args.
- Cambio de count solo (clamp 1..99, error si <1, error si no es int).
- Cambio de count + window (clamp window 1..900s).
- Reset a defaults de config.
- Autorización vía `allowed_user_ids` (no autorizado → silencio).
- Sin rate_limiter wired (broadcast desactivado) → mensaje informativo.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.domain.services.rate_limiter import FixedWindowRateLimiter


@pytest.fixture
def mock_container() -> MagicMock:
    return MagicMock()


@pytest.fixture
def agent_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.id = "dev"
    cfg.name = "Iñaki"
    cfg.description = "Asistente"
    cfg.channels = {
        "telegram": {
            "token": "dummy-token",
            "allowed_user_ids": [12345],
            "reactions": False,
            "broadcast": {
                "behavior": "autonomous",
                "rate_limiter": 5,
                "rate_limiter_window": 60,
            },
        }
    }
    return cfg


@pytest.fixture
def rate_limiter() -> FixedWindowRateLimiter:
    return FixedWindowRateLimiter(window_seconds=60.0)


@pytest.fixture
def bot(agent_cfg, mock_container, rate_limiter):
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        return TelegramBot(
            agent_cfg=agent_cfg,
            container=mock_container,
            rate_limiter=rate_limiter,
        )


def _make_update_and_context(args: list[str], user_id: int = 12345):
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = args
    return update, context


# ---------------------------------------------------------------------------
# Sin args → mostrar estado
# ---------------------------------------------------------------------------


async def test_sin_args_muestra_estado_actual(bot):
    update, context = _make_update_and_context([])

    await bot._cmd_ratelimit(update, context)

    update.message.reply_text.assert_awaited_once()
    msg = update.message.reply_text.call_args.args[0]
    assert "count = 5" in msg
    assert "window = 60s" in msg
    assert "default: 5" in msg
    assert "default: 60s" in msg


# ---------------------------------------------------------------------------
# Cambio de count
# ---------------------------------------------------------------------------


async def test_cambio_de_count_solo(bot):
    update, context = _make_update_and_context(["3"])

    await bot._cmd_ratelimit(update, context)

    assert bot._rate_limit_max == 3
    # Window NO cambia.
    assert bot._rate_limiter.window_seconds == 60.0
    msg = update.message.reply_text.call_args.args[0]
    assert "count=3" in msg
    assert "window=60s" in msg


async def test_count_clampea_a_99(bot):
    update, context = _make_update_and_context(["150"])

    await bot._cmd_ratelimit(update, context)

    assert bot._rate_limit_max == 99
    msg = update.message.reply_text.call_args.args[0]
    assert "count=99" in msg
    assert "clampeado de 150 a 99" in msg


async def test_count_minimo_1(bot):
    update, context = _make_update_and_context(["1"])

    await bot._cmd_ratelimit(update, context)

    assert bot._rate_limit_max == 1


async def test_count_menor_a_1_es_rechazado(bot):
    update, context = _make_update_and_context(["0"])

    await bot._cmd_ratelimit(update, context)

    # No mutó.
    assert bot._rate_limit_max == 5
    msg = update.message.reply_text.call_args.args[0]
    assert "Count debe ser >= 1" in msg


async def test_count_no_entero_es_rechazado(bot):
    update, context = _make_update_and_context(["foo"])

    await bot._cmd_ratelimit(update, context)

    assert bot._rate_limit_max == 5
    msg = update.message.reply_text.call_args.args[0]
    assert "inválido" in msg.lower()


# ---------------------------------------------------------------------------
# Cambio de count + window
# ---------------------------------------------------------------------------


async def test_cambio_de_count_y_window(bot):
    update, context = _make_update_and_context(["7", "300"])

    await bot._cmd_ratelimit(update, context)

    assert bot._rate_limit_max == 7
    assert bot._rate_limiter.window_seconds == 300.0
    msg = update.message.reply_text.call_args.args[0]
    assert "count=7" in msg
    assert "window=300s" in msg


async def test_window_clampea_a_900(bot):
    update, context = _make_update_and_context(["5", "1500"])

    await bot._cmd_ratelimit(update, context)

    assert bot._rate_limiter.window_seconds == 900.0
    msg = update.message.reply_text.call_args.args[0]
    assert "window=900s" in msg
    assert "clampeada de 1500s a 900s" in msg


async def test_window_minimo_1(bot):
    update, context = _make_update_and_context(["5", "1"])

    await bot._cmd_ratelimit(update, context)

    assert bot._rate_limiter.window_seconds == 1.0


async def test_window_menor_a_1_es_rechazada(bot):
    update, context = _make_update_and_context(["5", "0"])

    await bot._cmd_ratelimit(update, context)

    # Ni count ni window mutan.
    assert bot._rate_limit_max == 5
    assert bot._rate_limiter.window_seconds == 60.0
    msg = update.message.reply_text.call_args.args[0]
    assert "Window debe ser >= 1" in msg


async def test_window_no_entera_es_rechazada(bot):
    update, context = _make_update_and_context(["5", "abc"])

    await bot._cmd_ratelimit(update, context)

    # Ni count ni window mutan.
    assert bot._rate_limit_max == 5
    assert bot._rate_limiter.window_seconds == 60.0


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


async def test_reset_vuelve_a_defaults(bot):
    # Mutar primero.
    bot._rate_limit_max = 99
    bot._rate_limiter.set_window(900.0)

    update, context = _make_update_and_context(["reset"])

    await bot._cmd_ratelimit(update, context)

    assert bot._rate_limit_max == 5  # default de config
    assert bot._rate_limiter.window_seconds == 60.0  # default de config
    msg = update.message.reply_text.call_args.args[0]
    assert "reseteado" in msg.lower()


async def test_reset_es_case_insensitive(bot):
    bot._rate_limit_max = 50
    update, context = _make_update_and_context(["RESET"])

    await bot._cmd_ratelimit(update, context)

    assert bot._rate_limit_max == 5


# ---------------------------------------------------------------------------
# Autorización
# ---------------------------------------------------------------------------


async def test_usuario_no_autorizado_es_silencioso(bot):
    update, context = _make_update_and_context(["3"], user_id=99999)

    await bot._cmd_ratelimit(update, context)

    # No hay reply ni mutación.
    update.message.reply_text.assert_not_awaited()
    assert bot._rate_limit_max == 5


# ---------------------------------------------------------------------------
# Sin rate_limiter (broadcast desactivado)
# ---------------------------------------------------------------------------


async def test_sin_rate_limiter_responde_aviso(agent_cfg, mock_container):
    # Bot sin rate_limiter inyectado.
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        bot = TelegramBot(
            agent_cfg=agent_cfg,
            container=mock_container,
            rate_limiter=None,
        )

    update, context = _make_update_and_context([])

    await bot._cmd_ratelimit(update, context)

    msg = update.message.reply_text.call_args.args[0]
    assert "broadcast no está configurado" in msg.lower()
