"""Tests de la matriz de autorización del TelegramBot.

Cubre la regla de auth granular:

- Privado: ``allowed_user_ids`` filtra por usuario (vacío = todos).
- Grupo: SOLO ``allowed_chat_ids`` filtra por chat; ``allowed_user_ids`` se
  ignora (cualquier usuario del grupo autorizado puede hablar).
- ``allowed_chat_ids`` vacío = el bot NO responde en grupos (solo privados).

El guardián único es ``_is_authorized(update)``, que compone los building
blocks ``_is_allowed(user_id)`` (privado) y ``_is_allowed_chat(chat_id)`` (grupo).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_bot(*, allowed_user_ids: list[int], allowed_chat_ids: list[int]):
    cfg = MagicMock()
    cfg.id = "inaki"
    cfg.name = "Inaki"
    cfg.description = "Asistente"
    cfg.telegram = {
        "token": "dummy-token",
        "allowed_user_ids": allowed_user_ids,
        "allowed_chat_ids": allowed_chat_ids,
        "reactions": False,
    }
    with patch("adapters.inbound.telegram.bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.concurrent_updates.return_value.build.return_value = mock_app
        from adapters.inbound.telegram.bot import TelegramBot

        return TelegramBot(settings=cfg, ports=MagicMock())


def _private_update(*, user_id: int) -> MagicMock:
    update = MagicMock()
    update.effective_chat.type = "private"
    update.effective_chat.id = user_id
    update.effective_user.id = user_id
    return update


def _group_update(*, chat_id: int, user_id: int) -> MagicMock:
    update = MagicMock()
    update.effective_chat.type = "supergroup"
    update.effective_chat.id = chat_id
    update.effective_user.id = user_id
    return update


# ---------------------------------------------------------------------------
# _is_allowed_chat — nueva semántica del vacío
# ---------------------------------------------------------------------------


def test_is_allowed_chat_vacio_rechaza():
    """SCN-2.1: lista vacía => ningún grupo autorizado (cambio de comportamiento)."""
    bot = _build_bot(allowed_user_ids=[], allowed_chat_ids=[])
    assert bot._is_allowed_chat(-100123) is False


def test_is_allowed_chat_en_lista_acepta():
    bot = _build_bot(allowed_user_ids=[], allowed_chat_ids=[-100123])
    assert bot._is_allowed_chat(-100123) is True
    assert bot._is_allowed_chat(-100999) is False


# ---------------------------------------------------------------------------
# _is_authorized — privado (REQ-AUTH-1)
# ---------------------------------------------------------------------------


def test_privado_lista_vacia_acepta_todos():
    """SCN-1.1: privado + allowed_user_ids vacío => cualquier usuario."""
    bot = _build_bot(allowed_user_ids=[], allowed_chat_ids=[])
    assert bot._is_authorized(_private_update(user_id=777)) is True


def test_privado_user_en_lista_acepta():
    """SCN-1.2."""
    bot = _build_bot(allowed_user_ids=[123], allowed_chat_ids=[])
    assert bot._is_authorized(_private_update(user_id=123)) is True


def test_privado_user_fuera_de_lista_rechaza():
    """SCN-1.3."""
    bot = _build_bot(allowed_user_ids=[123], allowed_chat_ids=[])
    assert bot._is_authorized(_private_update(user_id=456)) is False


# ---------------------------------------------------------------------------
# _is_authorized — grupo (REQ-AUTH-2)
# ---------------------------------------------------------------------------


def test_grupo_chat_ids_vacio_rechaza():
    """SCN-2.1: grupo + allowed_chat_ids vacío => solo privados."""
    bot = _build_bot(allowed_user_ids=[], allowed_chat_ids=[])
    assert bot._is_authorized(_group_update(chat_id=-100123, user_id=42)) is False


def test_grupo_chat_en_lista_user_dentro_acepta():
    """SCN-2.2: chat autorizado + user en allowed_user_ids => acepta."""
    bot = _build_bot(allowed_user_ids=[42], allowed_chat_ids=[-100123])
    assert bot._is_authorized(_group_update(chat_id=-100123, user_id=42)) is True


def test_grupo_chat_en_lista_user_fuera_acepta_igual():
    """SCN-2.3 (clave): en grupo autorizado el filtro allowed_user_ids se IGNORA."""
    bot = _build_bot(allowed_user_ids=[42], allowed_chat_ids=[-100123])
    assert bot._is_authorized(_group_update(chat_id=-100123, user_id=999)) is True


def test_grupo_chat_fuera_de_lista_rechaza():
    """SCN-2.4: grupo no whitelisted."""
    bot = _build_bot(allowed_user_ids=[], allowed_chat_ids=[-100123])
    assert bot._is_authorized(_group_update(chat_id=-100999, user_id=42)) is False


# ---------------------------------------------------------------------------
# Edge defensivo
# ---------------------------------------------------------------------------


def test_update_sin_user_rechaza():
    bot = _build_bot(allowed_user_ids=[], allowed_chat_ids=[])
    update = _private_update(user_id=1)
    update.effective_user = None
    assert bot._is_authorized(update) is False


def test_update_sin_chat_rechaza():
    bot = _build_bot(allowed_user_ids=[], allowed_chat_ids=[])
    update = _private_update(user_id=1)
    update.effective_chat = None
    assert bot._is_authorized(update) is False
