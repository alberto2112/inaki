"""Tests para `/ratelimit` — override en runtime del rate limiter del broadcast.

Cubre:
- Mostrar valores actuales sin args.
- Cambio de count solo (clamp 1..99, error si <1, error si no es int).
- Cambio de count + window (clamp window 1..900s).
- Reset a defaults de config.
- Autorización vía `allowed_user_ids` (no autorizado → silencio).
- Sin política wired / agente no autónomo → mensaje informativo.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from inaki.channels.telegram.rate_limit import GroupRateLimit
from inaki.channels.telegram.ports import TelegramChannelSettings, TelegramGroupSettings


@pytest.fixture
def mock_container() -> MagicMock:
    return MagicMock()


@pytest.fixture
def agent_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.id = "dev"
    cfg.name = "Inaki"
    cfg.description = "Asistente"
    cfg.telegram = TelegramChannelSettings(
        token="dummy-token",
        allowed_user_ids=("12345",),
        reactions=False,
        groups=TelegramGroupSettings(
            behavior="autonomous",
            rate_limiter=5,
            rate_limiter_window=60,
        ),
    )
    return cfg


@pytest.fixture
def rate_limit() -> GroupRateLimit:
    return GroupRateLimit(enabled=True, agent_id="dev", max_count=5, window_seconds=60)


@pytest.fixture
def bot(agent_cfg, mock_container, rate_limit):
    with patch("inaki.channels.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.connect_timeout.return_value.read_timeout.return_value.write_timeout.return_value.pool_timeout.return_value.build.return_value = mock_app
        from inaki.channels.telegram.bot import TelegramBot

        return TelegramBot(
            settings=agent_cfg,
            ports=mock_container,
            rate_limit=rate_limit,
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

    await bot._commands.cmd_ratelimit(update, context)

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

    await bot._commands.cmd_ratelimit(update, context)

    assert bot._rate_limit.max_count == 3
    # Window NO cambia.
    assert bot._rate_limit.window_seconds == 60
    msg = update.message.reply_text.call_args.args[0]
    assert "count=3" in msg
    assert "window=60s" in msg


async def test_count_clampea_a_99(bot):
    update, context = _make_update_and_context(["150"])

    await bot._commands.cmd_ratelimit(update, context)

    assert bot._rate_limit.max_count == 99
    msg = update.message.reply_text.call_args.args[0]
    assert "count=99" in msg
    assert "clampeado de 150 a 99" in msg


async def test_count_minimo_1(bot):
    update, context = _make_update_and_context(["1"])

    await bot._commands.cmd_ratelimit(update, context)

    assert bot._rate_limit.max_count == 1


async def test_count_menor_a_1_es_rechazado(bot):
    update, context = _make_update_and_context(["0"])

    await bot._commands.cmd_ratelimit(update, context)

    # No mutó.
    assert bot._rate_limit.max_count == 5
    msg = update.message.reply_text.call_args.args[0]
    assert "Count debe ser >= 1" in msg


async def test_count_no_entero_es_rechazado(bot):
    update, context = _make_update_and_context(["foo"])

    await bot._commands.cmd_ratelimit(update, context)

    assert bot._rate_limit.max_count == 5
    msg = update.message.reply_text.call_args.args[0]
    assert "inválido" in msg.lower()


# ---------------------------------------------------------------------------
# Cambio de count + window
# ---------------------------------------------------------------------------


async def test_cambio_de_count_y_window(bot):
    update, context = _make_update_and_context(["7", "300"])

    await bot._commands.cmd_ratelimit(update, context)

    assert bot._rate_limit.max_count == 7
    assert bot._rate_limit.window_seconds == 300
    msg = update.message.reply_text.call_args.args[0]
    assert "count=7" in msg
    assert "window=300s" in msg


async def test_window_clampea_a_900(bot):
    update, context = _make_update_and_context(["5", "1500"])

    await bot._commands.cmd_ratelimit(update, context)

    assert bot._rate_limit.window_seconds == 900
    msg = update.message.reply_text.call_args.args[0]
    assert "window=900s" in msg
    assert "clampeada de 1500s a 900s" in msg


async def test_window_minimo_1(bot):
    update, context = _make_update_and_context(["5", "1"])

    await bot._commands.cmd_ratelimit(update, context)

    assert bot._rate_limit.window_seconds == 1


async def test_window_menor_a_1_es_rechazada(bot):
    update, context = _make_update_and_context(["5", "0"])

    await bot._commands.cmd_ratelimit(update, context)

    # Ni count ni window mutan.
    assert bot._rate_limit.max_count == 5
    assert bot._rate_limit.window_seconds == 60
    msg = update.message.reply_text.call_args.args[0]
    assert "Window debe ser >= 1" in msg


async def test_window_no_entera_es_rechazada(bot):
    update, context = _make_update_and_context(["5", "abc"])

    await bot._commands.cmd_ratelimit(update, context)

    # Ni count ni window mutan.
    assert bot._rate_limit.max_count == 5
    assert bot._rate_limit.window_seconds == 60


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


async def test_reset_vuelve_a_defaults(bot):
    # Mutar primero.
    bot._rate_limit.max_count = 99
    bot._rate_limit.set(99, 900)

    update, context = _make_update_and_context(["reset"])

    await bot._commands.cmd_ratelimit(update, context)

    assert bot._rate_limit.max_count == 5  # default de config
    assert bot._rate_limit.window_seconds == 60  # default de config
    msg = update.message.reply_text.call_args.args[0]
    assert "reseteado" in msg.lower()


async def test_reset_es_case_insensitive(bot):
    bot._rate_limit.max_count = 50
    update, context = _make_update_and_context(["RESET"])

    await bot._commands.cmd_ratelimit(update, context)

    assert bot._rate_limit.max_count == 5


# ---------------------------------------------------------------------------
# Autorización
# ---------------------------------------------------------------------------


async def test_usuario_no_autorizado_es_silencioso(bot):
    update, context = _make_update_and_context(["3"], user_id=99999)

    await bot._commands.cmd_ratelimit(update, context)

    # No hay reply ni mutación.
    update.message.reply_text.assert_not_awaited()
    assert bot._rate_limit.max_count == 5


# ---------------------------------------------------------------------------
# Sin política de rate limit (agente no autónomo)
# ---------------------------------------------------------------------------


async def test_sin_rate_limit_responde_aviso(agent_cfg, mock_container):
    # Bot sin política inyectada → fallback deshabilitado.
    with patch("inaki.channels.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.connect_timeout.return_value.read_timeout.return_value.write_timeout.return_value.pool_timeout.return_value.build.return_value = mock_app
        from inaki.channels.telegram.bot import TelegramBot

        bot = TelegramBot(
            settings=agent_cfg,
            ports=mock_container,
            rate_limit=None,
        )

    update, context = _make_update_and_context([])

    await bot._commands.cmd_ratelimit(update, context)

    msg = update.message.reply_text.call_args.args[0]
    assert "behavior=autonomous" in msg.lower()
