"""
Tests del arranque del rol **server** del broadcast: el bind tiene que ser
observable.

Caso real: el operador configura la Pi como server, el daemon arranca sin un
solo ERROR, y el puerto nunca se abre — ningún cliente puede conectarse. La
causa era que ``asyncio.start_server`` vivía DENTRO de la tarea de fondo:
``start()`` retornaba OK siempre, el container logueaba "broadcast adapter
iniciado" y el ``OSError`` del bind moría en una tarea que nadie awaitea.

Se verifica que:
- Un bind exitoso deja ``_server_obj`` disponible apenas retorna ``start()``.
- Un bind fallido (puerto ocupado) PROPAGA el ``OSError`` al caller.
- Tras un bind fallido el adapter queda sin iniciar (reintentable, y ``stop()``
  no explota).
- Si la tarea principal muere después del arranque, queda un ERROR en el log.
"""

from __future__ import annotations

import asyncio
import logging
import socket

import pytest

from adapters.broadcast.tcp import TcpBroadcastAdapter
from core.domain.services.broadcast_buffer import BroadcastBuffer


AUTH = "secreto_compartido_tests"
HOST = "127.0.0.1"


def _make_server(port: int, agent_id: str = "inaki") -> TcpBroadcastAdapter:
    return TcpBroadcastAdapter(
        agent_id=agent_id,
        role="server",
        host=HOST,
        port=port,
        auth=AUTH,
        buffer=BroadcastBuffer(),
    )


async def test_start_server_bindea_antes_de_retornar():
    """Tras ``await start()`` el socket YA está escuchando — sin ceder al loop."""
    adapter = _make_server(port=0)
    try:
        await adapter.start()

        assert adapter._server_obj is not None, (
            "start() debe haber bindeado el socket antes de retornar; si el bind "
            "vuelve a la tarea de fondo, el fallo se vuelve invisible"
        )
        assert adapter._server_obj.sockets, "el server debe tener un socket escuchando"
    finally:
        await adapter.stop()


async def test_start_server_propaga_el_error_de_bind():
    """Puerto ocupado ⇒ ``start()`` LANZA. El caller no puede loguear éxito."""
    ocupador = socket.socket()
    ocupador.bind((HOST, 0))
    ocupador.listen(1)
    port = ocupador.getsockname()[1]

    adapter = _make_server(port=port)
    try:
        with pytest.raises(OSError):
            await adapter.start()
    finally:
        await adapter.stop()
        ocupador.close()


async def test_bind_fallido_deja_el_adapter_reintentable():
    """Tras el fallo el adapter no queda "iniciado": liberar el puerto y reintentar funciona."""
    ocupador = socket.socket()
    ocupador.bind((HOST, 0))
    ocupador.listen(1)
    port = ocupador.getsockname()[1]

    adapter = _make_server(port=port)
    with pytest.raises(OSError):
        await adapter.start()

    assert adapter._iniciado is False
    assert adapter._server_obj is None
    await adapter.stop()  # no-op, no debe explotar

    # Liberado el puerto, el mismo adapter arranca.
    ocupador.close()
    try:
        await adapter.start()
        assert adapter._server_obj is not None
    finally:
        await adapter.stop()


async def test_muerte_de_la_tarea_principal_queda_logueada(caplog):
    """Si el transporte muere después del arranque, hay un ERROR — no silencio."""
    caplog.set_level(logging.ERROR, logger="adapters.broadcast.tcp")

    adapter = _make_server(port=0)
    await adapter.start()

    # Simula que serve_forever() revienta: cerrar el server hace que
    # serve_forever() lance, exactamente como un fallo de red del transporte.
    assert adapter._server_obj is not None
    adapter._server_obj.close()
    # El done-callback se agenda con call_soon: hay que ceder al loop DESPUÉS de
    # que la tarea termine para que llegue a correr.
    for _ in range(20):
        await asyncio.sleep(0)

    assert adapter._tarea_principal is not None and adapter._tarea_principal.done()
    assert "murió" in caplog.text, (
        f"se esperaba un ERROR reportando la muerte del transporte; hubo: {caplog.text!r}"
    )

    await adapter.stop()
