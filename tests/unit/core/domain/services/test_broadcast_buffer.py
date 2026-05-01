"""Tests unitarios para BroadcastBuffer.

Cubre TTL, cap, aislamiento por chat_id y rendering markdown.
Usa el parámetro injectable ``_now`` para tiempo determinista — sin freezegun.
"""

from __future__ import annotations

from core.domain.services.broadcast_buffer import BroadcastBuffer
from core.ports.outbound.broadcast_port import BroadcastMessage


# ---------------------------------------------------------------------------
# Helpers de fixtures
# ---------------------------------------------------------------------------


def _msg(
    agent_id: str = "agente_a", chat_id: str = "chat_1", ts: float = 1000.0
) -> BroadcastMessage:
    """Crea un BroadcastMessage mínimo para tests."""
    return BroadcastMessage(timestamp=ts, agent_id=agent_id, chat_id=chat_id, event_type="assistant_response", content="hola")


# ---------------------------------------------------------------------------
# TTL — expiración elimina mensajes
# ---------------------------------------------------------------------------


def test_ttl_expiry_recent_retorna_vacio():
    """Mensajes cuyo timestamp supera el TTL no aparecen en recent()."""
    now = 1000.0
    buffer = BroadcastBuffer(ttl=300.0, _now=lambda: now)

    buffer.append(_msg(ts=100.0))  # 900s en el pasado — expirado
    assert buffer.recent("chat_1") == []


def test_ttl_expiry_prune_en_append():
    """El auto-prune ocurre al hacer append: los expirados se eliminan del bucket."""
    tiempo_actual = 1000.0

    def reloj():
        return tiempo_actual

    buffer = BroadcastBuffer(ttl=300.0, _now=reloj)

    # Agregar mensaje que expira
    buffer.append(_msg(ts=100.0, chat_id="c"))

    # Avanzar el tiempo más allá del TTL y agregar uno nuevo
    tiempo_actual = 1500.0
    buffer.append(_msg(ts=1500.0, chat_id="c"))

    # Solo el nuevo mensaje debe estar
    msgs = buffer.recent("c")
    assert len(msgs) == 1
    assert msgs[0].timestamp == 1500.0


def test_ttl_mensaje_fresco_visible():
    """Mensaje dentro del TTL sigue visible en recent()."""
    now = 1000.0
    buffer = BroadcastBuffer(ttl=300.0, _now=lambda: now)

    buffer.append(_msg(ts=900.0))  # 100s en el pasado — dentro del TTL
    assert len(buffer.recent("chat_1")) == 1


# ---------------------------------------------------------------------------
# Cap 50 — evicts oldest
# ---------------------------------------------------------------------------


def test_cap_evicts_oldest():
    """Con 51 mensajes, el más antiguo es descartado y quedan 50."""
    # Usamos now=10000.0, ttl=300s, threshold=9700.0.
    # Los timestamps comienzan en 9800.0 (dentro del TTL) para que no sean prunados.
    now = 10000.0
    buffer = BroadcastBuffer(max_size=50, ttl=300.0, _now=lambda: now)

    base_ts = 9800.0
    for i in range(51):
        buffer.append(_msg(ts=base_ts + float(i), chat_id="chat_cap"))

    msgs = buffer.recent("chat_cap")
    assert len(msgs) == 50
    # El más antiguo (ts=9800.0) fue expulsado; el segundo (ts=9801.0) es ahora el primero
    assert msgs[0].timestamp == base_ts + 1.0


def test_cap_exactamente_50_no_evict():
    """Exactamente 50 mensajes: no se descarta ninguno."""
    now = 10000.0
    buffer = BroadcastBuffer(max_size=50, ttl=300.0, _now=lambda: now)

    base_ts = 9800.0
    for i in range(50):
        buffer.append(_msg(ts=base_ts + float(i), chat_id="chat_cap"))

    assert len(buffer.recent("chat_cap")) == 50


# ---------------------------------------------------------------------------
# Aislamiento por chat_id
# ---------------------------------------------------------------------------


def test_isolation_chat_ids():
    """Mensajes de chat_a no aparecen en recent('chat_b') y viceversa."""
    # now=10000, ttl=300 → threshold=9700; usamos ts=9800+ para que no sean prunados
    now = 10000.0
    buffer = BroadcastBuffer(ttl=300.0, _now=lambda: now)

    buffer.append(_msg(chat_id="chat_a", ts=9800.0))
    buffer.append(_msg(chat_id="chat_b", ts=9801.0))
    buffer.append(_msg(chat_id="chat_b", ts=9802.0))

    msgs_a = buffer.recent("chat_a")
    msgs_b = buffer.recent("chat_b")

    assert len(msgs_a) == 1
    assert len(msgs_b) == 2
    for m in msgs_a:
        assert m.chat_id == "chat_a"
    for m in msgs_b:
        assert m.chat_id == "chat_b"


def test_recent_chat_inexistente_retorna_lista_vacia():
    """recent() de un chat_id sin mensajes retorna lista vacía (no KeyError)."""
    buffer = BroadcastBuffer(_now=lambda: 9999.0)
    assert buffer.recent("chat_fantasma") == []


# ---------------------------------------------------------------------------
# render — sección markdown
# ---------------------------------------------------------------------------


def test_render_vacio_retorna_none():
    """render() retorna None cuando el buffer está vacío."""
    buffer = BroadcastBuffer(_now=lambda: 9999.0)
    assert buffer.render("chat_1") is None


def test_render_expirados_retorna_none():
    """render() retorna None cuando todos los mensajes expiraron."""
    now = 1000.0
    buffer = BroadcastBuffer(ttl=300.0, _now=lambda: now)
    buffer.append(_msg(ts=100.0))  # expirado
    assert buffer.render("chat_1") is None


def test_render_formato_seccion_markdown():
    """render() produce encabezado y líneas con formato [HH:MM:SS] agent_id: message."""
    # Usamos un timestamp UTC conocido: 2024-01-01 00:00:00 UTC = 1704067200.0
    ts = 1704067200.0
    buffer = BroadcastBuffer(ttl=3600.0, _now=lambda: ts + 1.0)

    buffer.append(
        BroadcastMessage(timestamp=ts, agent_id="agente_x", chat_id="c", event_type="assistant_response", content="hola mundo")
    )

    result = buffer.render("c")
    assert result is not None
    assert result.startswith("## Contexto del grupo (otros agentes)")
    assert "agente_x" in result
    assert "hola mundo" in result
    # Verificar formato de hora: la marca de tiempo es 00:00:00 UTC
    assert "00:00:00" in result


def test_render_multiple_mensajes_orden_cronologico():
    """render() lista mensajes en orden de antiguo a reciente."""
    ts_base = 1704067200.0
    buffer = BroadcastBuffer(ttl=3600.0, _now=lambda: ts_base + 100.0)

    buffer.append(
        BroadcastMessage(timestamp=ts_base + 10, agent_id="a1", chat_id="c", event_type="assistant_response", content="primero")
    )
    buffer.append(
        BroadcastMessage(timestamp=ts_base + 20, agent_id="a2", chat_id="c", event_type="assistant_response", content="segundo")
    )

    result = buffer.render("c")
    assert result is not None
    idx_primero = result.index("primero")
    idx_segundo = result.index("segundo")
    assert idx_primero < idx_segundo


def test_render_formato_linea_completa():
    """Cada línea sigue el patrón '- [HH:MM:SS] agent_id: message'."""
    # 1704067260.0 = 2024-01-01 00:01:00 UTC
    ts = 1704067260.0
    buffer = BroadcastBuffer(ttl=3600.0, _now=lambda: ts + 1.0)

    buffer.append(
        BroadcastMessage(timestamp=ts, agent_id="bot_dev", chat_id="grp", event_type="assistant_response", content="mensaje de test")
    )

    result = buffer.render("grp")
    assert result is not None
    assert "- [00:01:00] bot_dev: mensaje de test" in result


# ---------------------------------------------------------------------------
# render — formato por event_type
# ---------------------------------------------------------------------------


def test_render_user_input_voice_formato():
    """user_input_voice se renderiza con prefijo '{sender} (audio):'."""
    ts = 1704067260.0  # 2024-01-01 00:01:00 UTC
    buffer = BroadcastBuffer(ttl=3600.0, _now=lambda: ts + 1.0)

    buffer.append(
        BroadcastMessage(
            timestamp=ts,
            agent_id="bot_a",
            chat_id="grp",
            event_type="user_input_voice",
            content="cuánto es 5+5",
            sender="alberto",
        )
    )

    result = buffer.render("grp")
    assert result is not None
    assert "- [00:01:00] alberto (audio): cuánto es 5+5" in result
    # No debe aparecer el agent_id del emisor — el sender humano lo reemplaza
    assert "bot_a" not in result


def test_render_user_input_photo_formato():
    """user_input_photo se renderiza con prefijo '{sender} (foto):'."""
    ts = 1704067320.0  # 2024-01-01 00:02:00 UTC
    buffer = BroadcastBuffer(ttl=3600.0, _now=lambda: ts + 1.0)

    buffer.append(
        BroadcastMessage(
            timestamp=ts,
            agent_id="bot_a",
            chat_id="grp",
            event_type="user_input_photo",
            content="persona caminando hacia la cámara",
            sender="alberto",
        )
    )

    result = buffer.render("grp")
    assert result is not None
    assert "- [00:02:00] alberto (foto): persona caminando hacia la cámara" in result


def test_render_mixto_cronologico_3_event_types():
    """Mensajes de los 3 event_types aparecen en orden cronológico, cada uno con su formato."""
    ts_base = 1704067200.0  # 2024-01-01 00:00:00 UTC
    buffer = BroadcastBuffer(ttl=3600.0, _now=lambda: ts_base + 100.0)

    buffer.append(
        BroadcastMessage(
            timestamp=ts_base + 10,
            agent_id="bot_a",
            chat_id="grp",
            event_type="user_input_voice",
            content="hola, ¿cómo va?",
            sender="alberto",
        )
    )
    buffer.append(
        BroadcastMessage(
            timestamp=ts_base + 20,
            agent_id="bot_a",
            chat_id="grp",
            event_type="assistant_response",
            content="todo bien",
        )
    )
    buffer.append(
        BroadcastMessage(
            timestamp=ts_base + 30,
            agent_id="bot_a",
            chat_id="grp",
            event_type="user_input_photo",
            content="gato durmiendo",
            sender="alberto",
        )
    )

    result = buffer.render("grp")
    assert result is not None

    idx_voice = result.index("alberto (audio): hola")
    idx_assistant = result.index("bot_a: todo bien")
    idx_photo = result.index("alberto (foto): gato durmiendo")

    assert idx_voice < idx_assistant < idx_photo
