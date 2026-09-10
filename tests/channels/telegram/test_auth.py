"""``TelegramAuth``: la matriz de autorización, aislada del bot."""

from __future__ import annotations

from unittest.mock import MagicMock

from inaki.channels.telegram.auth import TelegramAuth


def _update(*, chat_type: str, chat_id: int, user_id: int) -> MagicMock:
    u = MagicMock()
    u.effective_chat.type = chat_type
    u.effective_chat.id = chat_id
    u.effective_user.id = user_id
    return u


def test_privado_lista_vacia_permite_a_todos() -> None:
    auth = TelegramAuth((), ())
    assert auth.is_allowed(42)
    assert auth.is_authorized(_update(chat_type="private", chat_id=42, user_id=42))


def test_privado_filtra_por_allowed_user_ids() -> None:
    auth = TelegramAuth(("1",), ())
    assert auth.is_authorized(_update(chat_type="private", chat_id=1, user_id=1))
    assert not auth.is_authorized(_update(chat_type="private", chat_id=2, user_id=2))


def test_grupo_lista_vacia_no_responde_y_allowed_user_ids_no_aplica() -> None:
    """La regla de grupos: manda ``allowed_chat_ids``; un usuario permitido en
    privado no habilita un grupo, y un usuario cualquiera puede hablar en un
    grupo permitido."""
    auth = TelegramAuth(("1",), ())
    assert not auth.is_authorized(_update(chat_type="supergroup", chat_id=-100, user_id=1))

    auth = TelegramAuth(("1",), ("-100",))
    assert auth.is_authorized(_update(chat_type="group", chat_id=-100, user_id=999))
    assert auth.is_allowed_chat(-100) and auth.is_allowed_chat("-100")
    assert not auth.is_allowed_chat(-200)


def test_update_sin_chat_o_sin_user_se_rechaza() -> None:
    auth = TelegramAuth((), ())
    sin_chat = MagicMock()
    sin_chat.effective_chat = None
    sin_user = MagicMock()
    sin_user.effective_user = None
    assert not auth.is_authorized(sin_chat)
    assert not auth.is_authorized(sin_user)
