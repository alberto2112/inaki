"""``ConversationHistory``: administrar el historial sin correr un turno."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from inaki.kernel.conversation_history import ConversationHistory
from inaki.shared.message import Message, Role


def _historial(store: AsyncMock | None = None) -> tuple[ConversationHistory, AsyncMock]:
    store = store or AsyncMock()
    return ConversationHistory(store, "dev"), store


async def test_get_history_oculta_el_plumbing_de_tools() -> None:
    uc, store = _historial()
    store.load.return_value = [
        Message(role=Role.USER, content="hola"),
        Message(role=Role.TOOL, content="{...}"),
        Message(role=Role.ASSISTANT, content="chau"),
    ]

    vista = await uc.get_history()

    assert [m.role for m in vista] == [Role.USER, Role.ASSISTANT]
    store.load.assert_awaited_once_with("dev")


async def test_get_history_vacio() -> None:
    uc, store = _historial()
    store.load.return_value = []
    assert await uc.get_history() == []


async def test_clear_history_borra_todo_o_un_scope() -> None:
    uc, store = _historial()

    await uc.clear_history()
    await uc.clear_history(channel="telegram", chat_id="42")

    assert store.clear.await_args_list[0].args == ("dev",)
    assert store.clear.await_args_list[0].kwargs == {"channel": None, "chat_id": None}
    assert store.clear.await_args_list[1].kwargs == {"channel": "telegram", "chat_id": "42"}


async def test_clear_history_propaga_excepciones() -> None:
    uc, store = _historial()
    store.clear.side_effect = RuntimeError("db bloqueada")
    with pytest.raises(RuntimeError, match="db bloqueada"):
        await uc.clear_history()


async def test_record_persiste_con_el_role_correcto_y_la_foto_devuelve_su_id() -> None:
    uc, store = _historial()
    store.append.return_value = 7

    await uc.record_user_message("hola", "telegram", "42")
    history_id = await uc.record_photo_message("@photo ...", "telegram", "42")
    await uc.record_assistant_message("transcripción", "telegram", "42")

    roles = [c.args[1].role for c in store.append.await_args_list]
    assert roles == [Role.USER, Role.USER, Role.ASSISTANT]
    assert history_id == 7
    assert store.append.await_args_list[0].kwargs == {"channel": "telegram", "chat_id": "42"}


async def test_update_message_content_delega_al_store() -> None:
    uc, store = _historial()
    store.update_content.return_value = True

    assert await uc.update_message_content(7, "@photo ... @analysis ...") is True
    store.update_content.assert_awaited_once_with("dev", 7, "@photo ... @analysis ...")
