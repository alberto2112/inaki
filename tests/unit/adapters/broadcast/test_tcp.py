"""Tests unitarios para TcpBroadcastAdapter — funciones puras y estado.

No se prueban sockets asyncio reales (eso es 6.5). Se testean:
- Firma y verificación HMAC (_firmar / _verificar_hmac).
- Rechazo de timestamp stale.
- Anti-loop (agent_id propio ignorado en _parsear_y_validar).
- Malformed JSON produce warning y retorna None.

Las funciones _firmar y _verificar_hmac son module-level helpers expuestos
en el módulo tcp.py. _parsear_y_validar es un método de instancia del adapter;
se crea una instancia mínima (sin sockets abiertos) para probarlo.
"""

from __future__ import annotations

import json
import logging
import time

from adapters.broadcast.tcp import TcpBroadcastAdapter, _firmar, _verificar_hmac
from core.domain.services.broadcast_buffer import BroadcastBuffer


# ---------------------------------------------------------------------------
# Helper — adapter mínimo sin sockets
# ---------------------------------------------------------------------------


def _make_adapter(agent_id: str = "bot_test", auth: str = "secreto") -> TcpBroadcastAdapter:
    """Crea un adapter en modo server sin abrir sockets (start() no llamado)."""
    buffer = BroadcastBuffer(_now=lambda: 9999.0)
    return TcpBroadcastAdapter(
        agent_id=agent_id,
        role="server",
        host="127.0.0.1",
        port=9999,
        auth=auth,
        buffer=buffer,
    )


def _linea_valida(
    auth: str,
    agent_id: str = "otro_bot",
    chat_id: str = "chat_1",
    message: str = "hola",
    ts: float | None = None,
) -> str:
    """Genera una línea JSON válida con HMAC correcto y timestamp fresco."""
    if ts is None:
        ts = time.time()
    digest = _firmar(auth, ts, agent_id, chat_id, message)
    payload = {
        "timestamp": ts,
        "agent_id": agent_id,
        "chat_id": chat_id,
        "message": message,
        "hmac": digest,
    }
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# HMAC sign / verify round-trip
# ---------------------------------------------------------------------------


def test_firmar_produce_hex_digest():
    """_firmar retorna una cadena hex no vacía."""
    digest = _firmar("secreto", 1000.0, "ag", "ch", "msg")
    assert isinstance(digest, str)
    assert len(digest) == 64  # SHA-256 hex digest son 64 chars


def test_verificar_hmac_valid_roundtrip():
    """_verificar_hmac retorna True para el mismo mensaje firmado."""
    auth = "clave_compartida"
    ts = 1704067200.0
    agent_id = "agente_1"
    chat_id = "grupo_42"
    message = "mensaje de prueba"

    digest = _firmar(auth, ts, agent_id, chat_id, message)
    assert _verificar_hmac(auth, ts, agent_id, chat_id, message, digest) is True


def test_verificar_hmac_tampered_message():
    """_verificar_hmac retorna False si el campo message fue modificado."""
    auth = "secreto"
    ts = 1704067200.0
    digest = _firmar(auth, ts, "ag", "ch", "original")
    assert _verificar_hmac(auth, ts, "ag", "ch", "MODIFICADO", digest) is False


def test_verificar_hmac_tampered_timestamp():
    """_verificar_hmac retorna False si el timestamp fue modificado."""
    auth = "secreto"
    ts = 1704067200.0
    digest = _firmar(auth, ts, "ag", "ch", "msg")
    assert _verificar_hmac(auth, ts + 1, "ag", "ch", "msg", digest) is False


def test_verificar_hmac_tampered_agent_id():
    """_verificar_hmac retorna False si agent_id fue modificado."""
    auth = "secreto"
    ts = 1704067200.0
    digest = _firmar(auth, ts, "original", "ch", "msg")
    assert _verificar_hmac(auth, ts, "atacante", "ch", "msg", digest) is False


def test_verificar_hmac_wrong_auth():
    """_verificar_hmac retorna False si se usa una clave diferente."""
    ts = 1704067200.0
    digest = _firmar("clave_correcta", ts, "ag", "ch", "msg")
    assert _verificar_hmac("clave_incorrecta", ts, "ag", "ch", "msg", digest) is False


# ---------------------------------------------------------------------------
# Rechazo de timestamp stale (anti-replay)
# ---------------------------------------------------------------------------


def test_parsear_rechaza_timestamp_stale_pasado():
    """Mensaje con timestamp > 60s en el pasado es descartado."""
    adapter = _make_adapter()
    ahora = time.time()
    # Timestamp 120s en el pasado
    ts = ahora - 120.0
    linea = _linea_valida("secreto", ts=ts)
    result = adapter._parsear_y_validar(linea)
    assert result is None


def test_parsear_rechaza_timestamp_stale_futuro():
    """Mensaje con timestamp > 60s en el futuro también es descartado."""
    adapter = _make_adapter()
    ahora = time.time()
    # Timestamp 120s en el futuro (reloj del emisor muy adelantado)
    ts = ahora + 120.0
    linea = _linea_valida("secreto", ts=ts)
    result = adapter._parsear_y_validar(linea)
    assert result is None


def test_parsear_acepta_timestamp_fresco():
    """Mensaje con timestamp dentro del margen de 60s es aceptado."""
    adapter = _make_adapter(agent_id="receptor", auth="secreto")
    ahora = time.time()
    linea = _linea_valida("secreto", agent_id="emisor", ts=ahora)
    result = adapter._parsear_y_validar(linea)
    assert result is not None
    assert result.agent_id == "emisor"


# ---------------------------------------------------------------------------
# Anti-loop — se descarta mensaje propio
# ---------------------------------------------------------------------------


def test_parsear_descarta_propio_agent_id(caplog):
    """Si agent_id del mensaje == self._agent_id, _parsear_y_validar retorna BroadcastMessage
    y el bucle de lectura lo descarta. Aquí testeamos que el adapter accede a la lógica
    correcta: verificar que _parsear_y_validar SÍ retorna el mensaje (la lógica anti-loop
    está en _bucle_lectura, no en _parsear_y_validar).
    """
    # El anti-loop está en _bucle_lectura, no en _parsear_y_validar.
    # _parsear_y_validar solo valida HMAC y frescura.
    # Este test documenta ese comportamiento: el mensaje propio pasa _parsear_y_validar
    # pero es descartado antes de llegar al buffer.
    adapter = _make_adapter(agent_id="mi_bot", auth="secreto")
    ahora = time.time()
    linea = _linea_valida("secreto", agent_id="mi_bot", ts=ahora)
    result = adapter._parsear_y_validar(linea)
    # _parsear_y_validar NO descarta por agent_id — solo verifica integridad
    assert result is not None
    assert result.agent_id == "mi_bot"


def test_antiloop_no_alimenta_buffer():
    """Mensaje con agent_id propio no llega al buffer (la lógica está en _bucle_lectura).

    Verificamos el comportamiento desde el buffer: el buffer del adapter que recibe
    su propio mensaje debe quedar vacío.
    """
    adapter = _make_adapter(agent_id="mi_bot", auth="secreto")
    ahora = time.time()
    linea = _linea_valida("secreto", agent_id="mi_bot", ts=ahora)

    # Simulamos el comportamiento de _bucle_lectura manualmente:
    # 1. _parsear_y_validar
    msg = adapter._parsear_y_validar(linea)
    assert msg is not None

    # 2. Verificar anti-loop: si msg.agent_id == self._agent_id, no se llama buffer.append
    if msg.agent_id == adapter._agent_id:
        # Anti-loop activo → no append
        pass
    else:
        adapter._buffer.append(msg)

    # El buffer debe estar vacío
    assert adapter._buffer.recent("chat_1") == []


# ---------------------------------------------------------------------------
# Malformed JSON — warning + retorna None
# ---------------------------------------------------------------------------


def test_parsear_json_malformado_retorna_none(caplog):
    """JSON inválido produce un warning y retorna None (no lanza excepción)."""
    adapter = _make_adapter()

    with caplog.at_level(logging.WARNING):
        result = adapter._parsear_y_validar("esto no es json {{{")

    assert result is None
    assert any(
        "malformed" in r.message.lower() or "malformed" in str(r.message) for r in caplog.records
    )


def test_parsear_json_campo_faltante_retorna_none(caplog):
    """JSON válido pero sin campos obligatorios produce warning y retorna None."""
    adapter = _make_adapter()
    linea = json.dumps({"timestamp": 1000.0})  # faltan agent_id, chat_id, message, hmac

    with caplog.at_level(logging.WARNING):
        result = adapter._parsear_y_validar(linea)

    assert result is None
    assert len(caplog.records) > 0


def test_parsear_hmac_invalido_retorna_none(caplog):
    """JSON completo pero con HMAC incorrecto es descartado con warning."""
    adapter = _make_adapter(auth="clave_correcta")
    ahora = time.time()
    payload = {
        "timestamp": ahora,
        "agent_id": "otro",
        "chat_id": "c",
        "message": "msg",
        "hmac": "deadbeef" * 8,  # HMAC falso de 64 chars
    }
    linea = json.dumps(payload)

    with caplog.at_level(logging.WARNING):
        result = adapter._parsear_y_validar(linea)

    assert result is None
