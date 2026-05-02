"""Tests unitarios para format_group_message y detect_mention.

Usa SimpleNamespace como stub liviano — no importa telegram.* en el módulo de test.
Verifica que el código de producción usa duck-typing con getattr (lo hace).
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from adapters.inbound.telegram.message_mapper import (
    detect_mention,
    dirigido_a,
    es_reply_a,
    es_reply_a_bot,
    extract_sender_name,
    format_group_message,
    hay_destinatario_explicito,
    telegram_update_to_input,
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
    location: SimpleNamespace | None = None,
    date: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        text=text, from_user=from_user, entities=entities, location=location, date=date
    )


def _location(
    latitude: float = 43.251412,
    longitude: float = 2.301256,
    live_period: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(latitude=latitude, longitude=longitude, live_period=live_period)


def _update(message: SimpleNamespace | None) -> SimpleNamespace:
    """Stub mínimo de ``telegram.Update`` para ``telegram_update_to_input``."""
    return SimpleNamespace(message=message)


# ---------------------------------------------------------------------------
# extract_sender_name
# ---------------------------------------------------------------------------


def test_extract_sender_name_con_username():
    """username tiene prioridad cuando está presente."""
    msg = _message(from_user=_user(username="alberto"))
    assert extract_sender_name(msg) == "alberto"


def test_extract_sender_name_username_prioritario_sobre_first_name():
    """Con username y first_name, username gana."""
    msg = _message(from_user=_user(username="alb", first_name="Alberto"))
    assert extract_sender_name(msg) == "alb"


def test_extract_sender_name_fallback_first_name():
    """Sin username pero con first_name, usa first_name."""
    msg = _message(from_user=_user(username=None, first_name="María"))
    assert extract_sender_name(msg) == "María"


def test_extract_sender_name_sin_nada_es_anonimo():
    """Sin username ni first_name, retorna 'anonimo'."""
    msg = _message(from_user=_user(username=None, first_name=None))
    assert extract_sender_name(msg) == "anonimo"


def test_extract_sender_name_sin_from_user_es_anonimo():
    """Sin from_user (None), retorna 'anonimo'."""
    msg = _message(from_user=None)
    assert extract_sender_name(msg) == "anonimo"


# ---------------------------------------------------------------------------
# format_group_message
# ---------------------------------------------------------------------------


def test_format_group_message_con_username():
    """Con username presente usa ese como prefijo (sin @)."""
    msg = _message(text="esto es un mensaje", from_user=_user(username="juan_perez"))
    resultado = format_group_message(msg)
    assert resultado == "juan_perez said: esto es un mensaje"


def test_format_group_message_sin_username_con_first_name():
    """Sin username pero con first_name, usa first_name como prefijo."""
    msg = _message(text="otro mensaje", from_user=_user(username=None, first_name="María"))
    resultado = format_group_message(msg)
    assert resultado == "María said: otro mensaje"


def test_format_group_message_sin_username_ni_first_name():
    """Sin username ni first_name, usa 'anonimo'."""
    msg = _message(text="mensaje sin nombre", from_user=_user(username=None, first_name=None))
    resultado = format_group_message(msg)
    assert resultado == "anonimo said: mensaje sin nombre"


def test_format_group_message_sin_from_user():
    """Sin from_user (None), usa 'anonimo'."""
    msg = _message(text="sin usuario", from_user=None)
    resultado = format_group_message(msg)
    assert resultado == "anonimo said: sin usuario"


def test_format_group_message_texto_vacio():
    """Texto vacío resulta en 'username said: ' (sin texto pero con prefijo)."""
    msg = _message(text="", from_user=_user(username="bot"))
    resultado = format_group_message(msg)
    assert resultado == "bot said: "


def test_format_group_message_username_tiene_prioridad_sobre_first_name():
    """Si hay ambos username y first_name, el username tiene prioridad."""
    msg = _message(text="test", from_user=_user(username="mi_user", first_name="Mi Nombre"))
    resultado = format_group_message(msg)
    assert resultado == "mi_user said: test"


def test_format_group_message_ignora_date():
    """``date`` ya no se usa: el timestamp se inyecta en el use case, no acá."""
    dt = datetime(2026, 4, 12, 19, 32, 5, tzinfo=timezone.utc)
    msg = _message(text="hola", from_user=_user(username="alberto"), date=dt)
    resultado = format_group_message(msg)
    assert resultado == "alberto said: hola"


def test_format_group_message_sin_date():
    """Sin date (None), el resultado tampoco lleva prefijo de timestamp."""
    msg = _message(text="hola", from_user=_user(username="alberto"), date=None)
    resultado = format_group_message(msg)
    assert resultado == "alberto said: hola"


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


# ---------------------------------------------------------------------------
# Helpers — replies
# ---------------------------------------------------------------------------


def _reply_user(username: str | None, is_bot: bool) -> SimpleNamespace:
    return SimpleNamespace(username=username, is_bot=is_bot)


def _msg_with_reply(
    reply_from: SimpleNamespace | None,
    text: str = "ok",
    entities: list | None = None,
) -> SimpleNamespace:
    reply = SimpleNamespace(from_user=reply_from) if reply_from is not None else None
    return SimpleNamespace(text=text, entities=entities, reply_to_message=reply)


# ---------------------------------------------------------------------------
# es_reply_a
# ---------------------------------------------------------------------------


def test_es_reply_a_coincide():
    msg = _msg_with_reply(_reply_user(username="mi_bot", is_bot=True))
    assert es_reply_a(msg, "mi_bot") is True


def test_es_reply_a_otro_bot():
    msg = _msg_with_reply(_reply_user(username="otro_bot", is_bot=True))
    assert es_reply_a(msg, "mi_bot") is False


def test_es_reply_a_sin_reply():
    msg = SimpleNamespace(text="hola", entities=None, reply_to_message=None)
    assert es_reply_a(msg, "mi_bot") is False


def test_es_reply_a_reply_sin_from_user():
    msg = SimpleNamespace(
        text="hola", entities=None, reply_to_message=SimpleNamespace(from_user=None)
    )
    assert es_reply_a(msg, "mi_bot") is False


# ---------------------------------------------------------------------------
# es_reply_a_bot
# ---------------------------------------------------------------------------


def test_es_reply_a_bot_true():
    msg = _msg_with_reply(_reply_user(username="anacleto_bot", is_bot=True))
    assert es_reply_a_bot(msg) is True


def test_es_reply_a_bot_humano():
    msg = _msg_with_reply(_reply_user(username="juan", is_bot=False))
    assert es_reply_a_bot(msg) is False


def test_es_reply_a_bot_sin_reply():
    msg = SimpleNamespace(text="hola", entities=None, reply_to_message=None)
    assert es_reply_a_bot(msg) is False


# ---------------------------------------------------------------------------
# dirigido_a — mención OR reply
# ---------------------------------------------------------------------------


def test_dirigido_a_por_mencion():
    texto = "hola @mi_bot"
    entity = _entity("mention", offset=5, length=len("@mi_bot"))
    msg = SimpleNamespace(text=texto, entities=[entity], reply_to_message=None)
    assert dirigido_a(msg, "mi_bot") is True


def test_dirigido_a_por_reply():
    msg = _msg_with_reply(_reply_user(username="mi_bot", is_bot=True))
    assert dirigido_a(msg, "mi_bot") is True


def test_dirigido_a_otro_bot_mencion():
    texto = "hola @otro_bot"
    entity = _entity("mention", offset=5, length=len("@otro_bot"))
    msg = SimpleNamespace(text=texto, entities=[entity], reply_to_message=None)
    assert dirigido_a(msg, "mi_bot") is False


def test_dirigido_a_otro_bot_reply():
    msg = _msg_with_reply(_reply_user(username="otro_bot", is_bot=True))
    assert dirigido_a(msg, "mi_bot") is False


def test_dirigido_a_mensaje_generico():
    """Sin mención ni reply → no dirigido a nadie en particular."""
    msg = SimpleNamespace(text="hola che", entities=None, reply_to_message=None)
    assert dirigido_a(msg, "mi_bot") is False


# ---------------------------------------------------------------------------
# hay_destinatario_explicito
# ---------------------------------------------------------------------------


def test_hay_destinatario_explicito_con_mencion():
    entity = _entity("mention", offset=0, length=len("@alguien"))
    msg = SimpleNamespace(text="@alguien dale", entities=[entity], reply_to_message=None)
    assert hay_destinatario_explicito(msg) is True


def test_hay_destinatario_explicito_con_reply_a_bot():
    msg = _msg_with_reply(_reply_user(username="anacleto_bot", is_bot=True))
    assert hay_destinatario_explicito(msg) is True


def test_hay_destinatario_explicito_reply_a_humano():
    """Reply a un humano NO cuenta — el filtro solo debe aplicar a bots."""
    msg = _msg_with_reply(_reply_user(username="juan", is_bot=False))
    assert hay_destinatario_explicito(msg) is False


def test_hay_destinatario_explicito_mensaje_generico():
    """Sin mención ni reply → no hay destinatario explícito."""
    msg = SimpleNamespace(text="hola che", entities=None, reply_to_message=None)
    assert hay_destinatario_explicito(msg) is False


# ---------------------------------------------------------------------------
# telegram_update_to_input — location
# ---------------------------------------------------------------------------


def test_telegram_update_to_input_texto_normal():
    """Mensaje de texto normal devuelve el texto strippeado."""
    update = _update(_message(text="  hola mundo  "))
    assert telegram_update_to_input(update) == "hola mundo"


def test_telegram_update_to_input_location_unica():
    """Posición única (sin live_period) devuelve formato {GPS:lat,lon}."""
    msg = _message(text=None, location=_location(latitude=43.251412, longitude=2.301256))
    assert telegram_update_to_input(_update(msg)) == "{GPS:43.251412,2.301256}"


def test_telegram_update_to_input_live_location_se_ignora():
    """Posición en tiempo real (live_period seteado) devuelve None."""
    msg = _message(text=None, location=_location(live_period=900))
    assert telegram_update_to_input(_update(msg)) is None


def test_telegram_update_to_input_sin_message():
    """Update sin message devuelve None."""
    assert telegram_update_to_input(_update(None)) is None


def test_telegram_update_to_input_texto_tiene_prioridad_sobre_location():
    """Si hay texto, se usa texto (defensivo — Telegram no manda ambos)."""
    msg = _message(text="texto explicito", location=_location())
    assert telegram_update_to_input(_update(msg)) == "texto explicito"


# ---------------------------------------------------------------------------
# format_group_message — location
# ---------------------------------------------------------------------------


def test_format_group_message_con_location():
    """Mensaje de location en grupo formatea como '<user> said: {GPS:lat,lon}'."""
    msg = _message(
        text=None,
        from_user=_user(username="alberto"),
        location=_location(latitude=43.251412, longitude=2.301256),
    )
    resultado = format_group_message(msg)
    assert resultado == "alberto said: {GPS:43.251412,2.301256}"


def test_format_group_message_live_location_cae_a_texto():
    """Live location no se trata como GPS — cae al texto (vacío en este caso)."""
    msg = _message(
        text="",
        from_user=_user(username="alberto"),
        location=_location(live_period=900),
    )
    resultado = format_group_message(msg)
    assert "{GPS:" not in resultado
