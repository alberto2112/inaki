"""``TurnRunner`` traduce la auth de Telegram al bit ``es_operador`` de ``execute()``.

Matriz (feature config-set-efimero): privado ∧ ``allowed_user_ids`` no vacía ∧ usuario
listado → True. Lista vacía, usuario no listado, grupo y flush de grupo → False.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from inaki.channels.telegram.bot import TelegramBot
from inaki.channels.telegram.ports import TelegramChannelSettings


def _make_bot(allowed_user_ids: tuple[str, ...]) -> tuple[TelegramBot, MagicMock]:
    agent_cfg = MagicMock()
    agent_cfg.id = "test-agent"
    agent_cfg.telegram = TelegramChannelSettings(
        token="fake-token",
        allowed_user_ids=allowed_user_ids,
        allowed_chat_ids=("-100",),
        reactions=False,
    )
    container = MagicMock()
    container.run_agent = MagicMock()
    container.run_agent.execute = AsyncMock(return_value="ok")
    container.run_agent.set_extra_system_sections = MagicMock()
    container.history.record_user_message = AsyncMock(return_value=None)
    container.scope_registry = MagicMock()
    container.scope_registry.try_mark_busy = AsyncMock(return_value=True)
    container.scope_registry.mark_idle = AsyncMock(return_value=None)
    with patch("inaki.channels.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.connect_timeout.return_value.read_timeout.return_value.write_timeout.return_value.pool_timeout.return_value.build.return_value = mock_app
        bot = TelegramBot(agent_cfg, container)
    return bot, container


def _update(user_id: int, chat_type: str) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.chat.type = chat_type
    update.message.text = "hola"
    update.message.reply_text = AsyncMock()
    update.message.set_reaction = AsyncMock()
    return update


async def _mensaje(bot: TelegramBot, update: MagicMock) -> None:
    with patch("inaki.channels.telegram.bot.telegram_update_to_input", return_value="hola"):
        await bot._handle_message(update, MagicMock())


async def test_privado_de_usuario_listado_es_operador() -> None:
    bot, container = _make_bot(("123",))
    await _mensaje(bot, _update(123, "private"))

    assert container.run_agent.execute.await_args.kwargs["es_operador"] is True


async def test_privado_con_lista_vacia_no_es_operador() -> None:
    bot, container = _make_bot(())
    await _mensaje(bot, _update(123, "private"))

    container.run_agent.execute.assert_awaited_once()
    assert container.run_agent.execute.await_args.kwargs["es_operador"] is False


async def test_privado_de_usuario_no_listado_no_es_operador() -> None:
    """Un no listado ni llega (``is_authorized`` lo corta) — el turno lo corre el
    runner directo para fijar que, aun así, el veredicto es False."""
    bot, container = _make_bot(("123",))
    await bot._turns.run(_update(456, "private"), "hola", chat_type="private")

    assert container.run_agent.execute.await_args.kwargs["es_operador"] is False


async def test_grupo_nunca_es_operador_aunque_el_usuario_este_listado() -> None:
    bot, container = _make_bot(("123",))
    await bot._turns.run(_update(123, "supergroup"), "hola", chat_type="supergroup")

    assert container.run_agent.execute.await_args.kwargs["es_operador"] is False


async def test_flush_de_grupo_no_pasa_es_operador() -> None:
    """``run_group`` no tiene usuario (es el agente quien habla): jamás es operador."""
    bot, container = _make_bot(("123",))
    await bot._turns.run_group("-100", "supergroup", {})

    kwargs = container.run_agent.execute.await_args.kwargs
    assert kwargs.get("es_operador", False) is False
