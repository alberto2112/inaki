"""Tests unitarios para ``prepend_timestamps``."""

from __future__ import annotations

from datetime import datetime, timezone

from core.domain.entities.message import Message, Role
from core.domain.services.prepend_timestamps import prepend_timestamps


def _msg(
    role: Role,
    content: str,
    ts: datetime | None = None,
    tool_calls: list[dict] | None = None,
    tool_call_id: str | None = None,
) -> Message:
    return Message(
        role=role,
        content=content,
        timestamp=ts,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
    )


def test_anteponer_a_user_con_timestamp():
    ts = datetime(2026, 5, 1, 12, 30, 0, tzinfo=timezone.utc)
    out = prepend_timestamps([_msg(Role.USER, "hola", ts=ts)])
    assert len(out) == 1
    assert out[0].content.endswith("hola")
    assert out[0].content.startswith("[")
    assert "] hola" in out[0].content


def test_anteponer_a_assistant_con_timestamp():
    ts = datetime(2026, 5, 1, 12, 30, 0, tzinfo=timezone.utc)
    out = prepend_timestamps([_msg(Role.ASSISTANT, "buenas", ts=ts)])
    assert "] buenas" in out[0].content


def test_no_muta_mensaje_original():
    """``model_copy`` evita mutar la entidad original — invariante de pureza."""
    ts = datetime(2026, 5, 1, 12, 30, 0, tzinfo=timezone.utc)
    original = _msg(Role.USER, "hola", ts=ts)
    out = prepend_timestamps([original])
    assert original.content == "hola"
    assert out[0].content != "hola"


def test_intacto_si_timestamp_es_none():
    """Working messages del tool loop sin timestamp quedan intactos."""
    msg = _msg(Role.USER, "hola", ts=None)
    out = prepend_timestamps([msg])
    assert out[0].content == "hola"


def test_intacto_si_content_vacio():
    """Assistant con tool_calls y content vacío NO se prefija — el builder
    convierte content vacío a None y meterle ``[ts] `` rompería el contrato."""
    ts = datetime(2026, 5, 1, 12, 30, 0, tzinfo=timezone.utc)
    msg = _msg(Role.ASSISTANT, "", ts=ts, tool_calls=[{"id": "x"}])
    out = prepend_timestamps([msg])
    assert out[0].content == ""


def test_tool_role_intacto():
    """Los TOOL responses no llevan timestamp — sería ruido para el LLM."""
    ts = datetime(2026, 5, 1, 12, 30, 0, tzinfo=timezone.utc)
    msg = _msg(Role.TOOL, "resultado", ts=ts, tool_call_id="abc")
    out = prepend_timestamps([msg])
    assert out[0].content == "resultado"


def test_tool_result_role_intacto():
    ts = datetime(2026, 5, 1, 12, 30, 0, tzinfo=timezone.utc)
    msg = _msg(Role.TOOL_RESULT, "x", ts=ts)
    out = prepend_timestamps([msg])
    assert out[0].content == "x"


def test_system_role_intacto():
    ts = datetime(2026, 5, 1, 12, 30, 0, tzinfo=timezone.utc)
    msg = _msg(Role.SYSTEM, "soy system", ts=ts)
    out = prepend_timestamps([msg])
    assert out[0].content == "soy system"


def test_lista_vacia_devuelve_lista_vacia():
    assert prepend_timestamps([]) == []


def test_mezcla_de_roles_y_estados():
    """Una conversación realista: history con timestamps + working msg sin ts."""
    ts1 = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 1, 12, 0, 5, tzinfo=timezone.utc)
    msgs = [
        _msg(Role.USER, "hola", ts=ts1),
        _msg(Role.ASSISTANT, "buenas", ts=ts2),
        _msg(Role.USER, "ahora", ts=None),  # working, sin timestamp
    ]
    out = prepend_timestamps(msgs)
    assert "] hola" in out[0].content
    assert "] buenas" in out[1].content
    assert out[2].content == "ahora"


def test_formato_incluye_fecha_y_hora_local():
    ts = datetime(2026, 5, 1, 12, 30, 45, tzinfo=timezone.utc)
    out = prepend_timestamps([_msg(Role.USER, "x", ts=ts)])
    # No asumimos la TZ local del runner — solo el shape.
    import re

    assert re.match(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}.*\] x$", out[0].content)
