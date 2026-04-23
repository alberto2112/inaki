"""Tests unitarios para format_group_message y detect_mention.

Usa SimpleNamespace como stub liviano — no importa telegram.* en el módulo de test.
Verifica que el código de producción usa duck-typing con getattr (lo hace).
"""

from __future__ import annotations

from types import SimpleNamespace

from adapters.inbound.telegram.message_mapper import (
    detect_mention,
    format_group_message,
)


# ---------------------------------------------------------------------------
# Helpers — stubs de Telegram con SimpleNamespace
# ---------------------------------------------------------------------------


def _user(username: str | None = None, first_name: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(username=username, first_name=first_name)


def _entity(
    tipo: str, offset: int, length: int, user: SimpleNamespace | None = None
) -> SimpleNamespace:
    return SimpleNamespace(type=tipo, offset=offset, length=length, user=user)


def _message(
    text: str = "hola",
    from_user: SimpleNamespace | None = None,
    entities: list | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(text=text, from_user=from_user, entities=entities)


# ---------------------------------------------------------------------------
# format_group_message
# ---------------------------------------------------------------------------


def test_format_group_message_con_username():
    """Con username presente usa ese como prefijo (sin @)."""
    msg = _message(text="esto es un mensaje", from_user=_user(username="juan_perez"))
    resultado = format_group_message(msg)
    assert resultado == "juan_perez: esto es un mensaje"


def test_format_group_message_sin_username_con_first_name():
    """Sin username pero con first_name, usa first_name como prefijo."""
    msg = _message(text="otro mensaje", from_user=_user(username=None, first_name="María"))
    resultado = format_group_message(msg)
    assert resultado == "María: otro mensaje"


def test_format_group_message_sin_username_ni_first_name():
    """Sin username ni first_name, usa 'anonimo'."""
    msg = _message(text="mensaje sin nombre", from_user=_user(username=None, first_name=None))
    resultado = format_group_message(msg)
    assert resultado == "anonimo: mensaje sin nombre"


def test_format_group_message_sin_from_user():
    """Sin from_user (None), usa 'anonimo'."""
    msg = _message(text="sin usuario", from_user=None)
    resultado = format_group_message(msg)
    assert resultado == "anonimo: sin usuario"


def test_format_group_message_texto_vacio():
    """Texto vacío resulta en 'username: ' (sin texto pero con prefijo)."""
    msg = _message(text="", from_user=_user(username="bot"))
    resultado = format_group_message(msg)
    assert resultado == "bot: "


def test_format_group_message_username_tiene_prioridad_sobre_first_name():
    """Si hay ambos username y first_name, el username tiene prioridad."""
    msg = _message(text="test", from_user=_user(username="mi_user", first_name="Mi Nombre"))
    resultado = format_group_message(msg)
    assert resultado == "mi_user: test"


# ---------------------------------------------------------------------------
# detect_mention — tipo "mention" (texto @username)
# ---------------------------------------------------------------------------


def test_detect_mention_entity_mention_coincide():
    """Entidad tipo 'mention' con texto '@bot_username' → True."""
    texto = "hola @mi_bot como estás"
    # '@mi_bot' empieza en offset=5 y tiene length=7
    offset = texto.index("@mi_bot")
    entity = _entity("mention", offset=offset, length=len("@mi_bot"))
    msg = _message(text=texto, entities=[entity])

    assert detect_mention(msg, "mi_bot") is True


def test_detect_mention_entity_mention_otro_bot():
    """Entidad tipo 'mention' con texto '@otro_bot' → False."""
    texto = "hola @otro_bot"
    offset = texto.index("@otro_bot")
    entity = _entity("mention", offset=offset, length=len("@otro_bot"))
    msg = _message(text=texto, entities=[entity])

    assert detect_mention(msg, "mi_bot") is False


def test_detect_mention_sin_entidades():
    """Sin entities → False."""
    msg = _message(text="hola bot, cómo estás", entities=None)
    assert detect_mention(msg, "mi_bot") is False


def test_detect_mention_entidades_vacias():
    """entities=[] → False."""
    msg = _message(text="hola bot", entities=[])
    assert detect_mention(msg, "mi_bot") is False


# ---------------------------------------------------------------------------
# detect_mention — tipo "text_mention" (usuario sin @username público)
# ---------------------------------------------------------------------------


def test_detect_mention_text_mention_coincide():
    """Entidad tipo 'text_mention' con user.username == bot_username → True."""
    usuario = _user(username="mi_bot")
    entity = _entity("text_mention", offset=0, length=5, user=usuario)
    msg = _message(text="hola.", entities=[entity])

    assert detect_mention(msg, "mi_bot") is True


def test_detect_mention_text_mention_otro_username():
    """Entidad tipo 'text_mention' con user.username diferente → False."""
    usuario = _user(username="otro_usuario")
    entity = _entity("text_mention", offset=0, length=5, user=usuario)
    msg = _message(text="hola.", entities=[entity])

    assert detect_mention(msg, "mi_bot") is False


def test_detect_mention_text_mention_user_none():
    """text_mention con user=None → False (no lanza AttributeError)."""
    entity = _entity("text_mention", offset=0, length=5, user=None)
    msg = _message(text="hola.", entities=[entity])

    assert detect_mention(msg, "mi_bot") is False


# ---------------------------------------------------------------------------
# detect_mention — múltiples entidades
# ---------------------------------------------------------------------------


def test_detect_mention_multiples_entidades_una_coincide():
    """Varias entidades, solo una coincide → True."""
    texto = "@otro_bot @mi_bot"
    e1 = _entity("mention", offset=0, length=len("@otro_bot"))
    e2 = _entity("mention", offset=len("@otro_bot "), length=len("@mi_bot"))
    msg = _message(text=texto, entities=[e1, e2])

    assert detect_mention(msg, "mi_bot") is True


def test_detect_mention_multiples_entidades_ninguna_coincide():
    """Varias entidades, ninguna coincide → False."""
    texto = "@bot_a @bot_b"
    e1 = _entity("mention", offset=0, length=len("@bot_a"))
    e2 = _entity("mention", offset=len("@bot_a "), length=len("@bot_b"))
    msg = _message(text=texto, entities=[e1, e2])

    assert detect_mention(msg, "mi_bot") is False


def test_detect_mention_tipo_desconocido_ignorado():
    """Entidades de otros tipos (url, hashtag, etc.) son ignoradas."""
    entity = _entity("url", offset=0, length=5)
    msg = _message(text="https://ex.com hola @mi_bot", entities=[entity])

    # La URL no es una mención — no coincide
    assert detect_mention(msg, "mi_bot") is False
