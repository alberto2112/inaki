"""``/model``, ``/effort`` y ``/temperature``: atajos de la tool ``config`` desde Telegram.

Sin argumento leen (``get``, abierto a quien usa los slash); con argumento cambian
(``set``, solo el operador por privado). No reimplementan nada: el veredicto y el
texto son los de la tool, y el ``set`` corre con el ``ChannelContext`` del chat
publicado para que la tool agende el aviso de vuelta tras el reinicio.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from inaki.channels.telegram.bot import TelegramBot
from inaki.channels.telegram.ports import TelegramChannelSettings
from inaki.kernel.ports.tool_port import ToolResult
from inaki.shared.channel_context import current_channel_context


def _bot(tools, *, operadores: tuple[str, ...] = ()) -> TelegramBot:
    cfg = MagicMock()
    cfg.id = "dev"
    cfg.telegram = TelegramChannelSettings(
        token="dummy-token",
        allowed_user_ids=("12345", "777"),
        operator_user_ids=operadores,
        reactions=False,
    )
    ports = MagicMock()
    ports.tools = tools
    with patch("inaki.channels.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.connect_timeout.return_value.read_timeout.return_value.write_timeout.return_value.pool_timeout.return_value.build.return_value = mock_app
        return TelegramBot(settings=cfg, ports=ports, reloader=None)


def _update(user_id: int = 12345, chat_type: str = "private", chat_id: int = 12345) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    update.effective_chat.type = chat_type
    update.message.reply_text = AsyncMock()
    return update


def _context(*args: str) -> MagicMock:
    context = MagicMock()
    context.args = list(args)
    return context


@pytest.fixture
def tools() -> MagicMock:
    t = MagicMock()
    t.execute = AsyncMock(
        return_value=ToolResult(tool_name="config", output="salida de la tool", success=True)
    )
    return t


@pytest.mark.parametrize(
    ("comando", "path"),
    [
        ("cmd_model", "llm.model"),
        ("cmd_effort", "llm.reasoning_effort"),
        ("cmd_temperature", "llm.temperature"),
    ],
)
async def test_sin_argumento_lee_el_valor_con_get(tools, comando, path):
    update = _update()

    await getattr(_bot(tools)._commands, comando)(update, _context())

    tools.execute.assert_awaited_once_with("config", operation="get", path=path)
    update.message.reply_text.assert_awaited_once_with("salida de la tool")


async def test_leer_no_exige_operador(tools):
    """Leer está abierto a quien usa los slash: en un grupo también responde."""
    update = _update(chat_type="group", chat_id=-100)

    await _bot(tools)._commands.cmd_model(update, _context())

    tools.execute.assert_awaited_once_with("config", operation="get", path="llm.model")


async def test_model_con_argumento_hace_set_con_el_contexto_del_chat(tools):
    vistos = []

    async def _execute(nombre, **kwargs):
        vistos.append(current_channel_context())
        return ToolResult(tool_name=nombre, output="Accepted: ...", success=True)

    tools.execute = AsyncMock(side_effect=_execute)
    update = _update()

    await _bot(tools)._commands.cmd_model(update, _context("claude-opus-5-5"))

    tools.execute.assert_awaited_once_with(
        "config", operation="set", path="llm.model", value="claude-opus-5-5"
    )
    (contexto,) = vistos
    assert contexto is not None
    assert (contexto.channel_type, contexto.context_id) == ("telegram", "12345")
    assert current_channel_context() is None  # se restaura al salir
    update.message.reply_text.assert_awaited_once_with("Accepted: ...")


@pytest.mark.parametrize(
    ("arg", "valor"),
    [("medium", "medium"), ("HIGH", "high"), ("off", None), ("none", None), ("null", None)],
)
async def test_effort_normaliza_y_off_desactiva(tools, arg, valor):
    await _bot(tools)._commands.cmd_effort(_update(), _context(arg))

    tools.execute.assert_awaited_once_with(
        "config", operation="set", path="llm.reasoning_effort", value=valor
    )


@pytest.mark.parametrize(("arg", "valor"), [("0.3", 0.3), ("0,5", 0.5), ("1", 1.0)])
async def test_temperature_parsea_float(tools, arg, valor):
    await _bot(tools)._commands.cmd_temperature(_update(), _context(arg))

    tools.execute.assert_awaited_once_with(
        "config", operation="set", path="llm.temperature", value=valor
    )


async def test_temperature_invalida_no_toca_la_config(tools):
    update = _update()

    await _bot(tools)._commands.cmd_temperature(update, _context("tibia"))

    tools.execute.assert_not_awaited()
    assert "Valor inválido" in update.message.reply_text.await_args.args[0]


async def test_mas_de_un_argumento_es_error_de_uso(tools):
    update = _update()

    await _bot(tools)._commands.cmd_model(update, _context("claude", "opus"))

    tools.execute.assert_not_awaited()
    assert update.message.reply_text.await_args.args[0].startswith("Uso: /model")


async def test_set_en_grupo_se_rechaza(tools):
    update = _update(chat_type="supergroup", chat_id=-100)

    await _bot(tools)._commands.cmd_model(update, _context("otro-modelo"))

    tools.execute.assert_not_awaited()
    assert "solo para el operador" in update.message.reply_text.await_args.args[0]


async def test_set_de_quien_habla_pero_no_es_operador_se_rechaza(tools):
    """777 está en allowed_user_ids (usa los slash) pero no en operator_user_ids."""
    update = _update(user_id=777, chat_id=777)

    await _bot(tools, operadores=("12345",))._commands.cmd_model(update, _context("otro"))

    tools.execute.assert_not_awaited()
    assert "solo para el operador" in update.message.reply_text.await_args.args[0]


async def test_usuario_no_listado_no_hace_nada(tools):
    update = _update(user_id=999, chat_id=999)

    await _bot(tools)._commands.cmd_model(update, _context())

    tools.execute.assert_not_awaited()
    update.message.reply_text.assert_not_awaited()


async def test_sin_tools_avisa_que_no_esta_disponible():
    update = _update()

    await _bot(None)._commands.cmd_model(update, _context())

    assert "no está disponible" in update.message.reply_text.await_args.args[0]


async def test_menu_y_help_sin_clear_all_y_con_los_nuevos(tools):
    bot = _bot(tools)
    bot._app.bot.set_my_commands = AsyncMock()
    await bot._commands.setup_commands()
    nombres = {c.command for c in bot._app.bot.set_my_commands.await_args.args[0]}
    assert "clear_all" not in nombres
    assert {"model", "effort", "temperature"} <= nombres

    update = _update()
    await bot._commands.cmd_help(update, _context())
    texto = update.message.reply_text.await_args.args[0]
    assert "/clear_all" not in texto
    for comando in ("/model", "/effort", "/temperature"):
        assert comando in texto

    handlers = {
        c.args[0].commands
        for c in bot._app.add_handler.call_args_list
        if hasattr(c.args[0], "commands")
    }
    registrados = set().union(*handlers)
    assert "clear_all" not in registrados
    assert {"model", "effort", "temperature"} <= registrados
