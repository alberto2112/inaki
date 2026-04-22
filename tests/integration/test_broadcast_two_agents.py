"""
Test de integración: dos TcpBroadcastAdapter (server + client) en localhost.

Verifica que:
- El cliente emite y el server alimenta su buffer.
- El server emite y el cliente recibe.
- El anti-loop descarta mensajes con agent_id propio (verificado via buffer).
- El callback de suscripción es invocado con el mensaje recibido.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from adapters.broadcast.tcp import TcpBroadcastAdapter
from core.domain.services.broadcast_buffer import BroadcastBuffer
from core.ports.outbound.broadcast_port import BroadcastMessage


AUTH = "secreto_compartido_tests"
CHAT_ID = "grupo_test_123"
HOST = "127.0.0.1"

# Espera mínima para que el otro lado procese (asyncio event loop, no sleep real largo)
TICK = 0.05  # 50ms — suficiente para un event loop local sin I/O real


# ---------------------------------------------------------------------------
# Fixture: par server + client en puerto efímero
# ---------------------------------------------------------------------------


@pytest.fixture
async def par_server_client():
    """Levanta un server en puerto aleatorio y un client conectado a él.

    Retorna (server_adapter, client_adapter). Hace teardown automático.
    """
    # Crear buffers independientes para cada adapter
    buf_server = BroadcastBuffer(_now=time.time)
    buf_client = BroadcastBuffer(_now=time.time)

    # Server: port=0 → el SO asigna puerto libre
    server = TcpBroadcastAdapter(
        agent_id="server_bot",
        role="server",
        host=HOST,
        port=0,
        auth=AUTH,
        buffer=buf_server,
    )
    await server.start()

    # Ceder el control al event loop para que la tarea del server ejecute asyncio.start_server
    # y asigne _server_obj (la tarea corre hasta el primer await interno).
    for _ in range(10):
        await asyncio.sleep(0)
        if server._server_obj is not None:
            break

    assert server._server_obj is not None, "server._server_obj debe estar disponible tras start()"
    sockets = server._server_obj.sockets
    assert sockets, "El server debe tener al menos un socket abierto"
    port_real = sockets[0].getsockname()[1]

    # Client: conecta al puerto real del server
    client = TcpBroadcastAdapter(
        agent_id="client_bot",
        role="client",
        host=HOST,
        port=port_real,
        auth=AUTH,
        buffer=buf_client,
        reconnect_max_backoff=1.0,  # backoff corto para tests
    )
    await client.start()

    # Dar tiempo a que el client establezca la conexión TCP
    await asyncio.sleep(TICK)

    yield server, client, buf_server, buf_client

    # Teardown
    await client.stop()
    await server.stop()


# ---------------------------------------------------------------------------
# Test: client emite → server recibe en buffer
# ---------------------------------------------------------------------------


async def test_client_emit_llega_al_buffer_del_server(par_server_client):
    """Un mensaje emitido por el client debe aparecer en el buffer del server."""
    server, client, buf_server, buf_client = par_server_client

    msg = BroadcastMessage(
        timestamp=time.time(),
        agent_id="client_bot",
        chat_id=CHAT_ID,
        message="hola desde el client",
    )

    await client.emit(msg)
    await asyncio.sleep(TICK)

    # El server tiene agent_id "server_bot" y recibe mensajes de "client_bot" → no anti-loop
    msgs_server = buf_server.recent(CHAT_ID)
    assert len(msgs_server) == 1
    assert msgs_server[0].message == "hola desde el client"
    assert msgs_server[0].agent_id == "client_bot"


# ---------------------------------------------------------------------------
# Test: server emite → client recibe en buffer
# ---------------------------------------------------------------------------


async def test_server_emit_llega_al_buffer_del_client(par_server_client):
    """Un mensaje emitido por el server debe aparecer en el buffer del client."""
    server, client, buf_server, buf_client = par_server_client

    # Dar tiempo extra para que el client esté suscrito al stream del server
    await asyncio.sleep(TICK)

    msg = BroadcastMessage(
        timestamp=time.time(),
        agent_id="server_bot",
        chat_id=CHAT_ID,
        message="hola desde el server",
    )

    await server.emit(msg)
    await asyncio.sleep(TICK)

    # El client tiene agent_id "client_bot" → recibe mensajes de "server_bot"
    msgs_client = buf_client.recent(CHAT_ID)
    assert len(msgs_client) == 1
    assert msgs_client[0].message == "hola desde el server"
    assert msgs_client[0].agent_id == "server_bot"


# ---------------------------------------------------------------------------
# Test: callback de suscripción es invocado
# ---------------------------------------------------------------------------


async def test_callback_subscribe_invocado(par_server_client):
    """subscribe() registra un callback que es llamado al recibir un mensaje."""
    server, client, buf_server, buf_client = par_server_client

    mensajes_recibidos: list[BroadcastMessage] = []

    async def mi_callback(msg: BroadcastMessage) -> None:
        mensajes_recibidos.append(msg)

    # Registrar callback en el server
    await server.subscribe(mi_callback)

    msg = BroadcastMessage(
        timestamp=time.time(),
        agent_id="client_bot",
        chat_id=CHAT_ID,
        message="mensaje para callback",
    )
    await client.emit(msg)
    await asyncio.sleep(TICK * 3)  # tiempo extra para que el callback corra

    assert len(mensajes_recibidos) == 1
    assert mensajes_recibidos[0].message == "mensaje para callback"


# ---------------------------------------------------------------------------
# Test: anti-loop — server no ve sus propios mensajes en su buffer
# ---------------------------------------------------------------------------


async def test_antiloop_server_no_ve_sus_propios_mensajes(par_server_client):
    """El server emite un mensaje; su propio buffer NO debe recibirlo (anti-loop)."""
    server, client, buf_server, buf_client = par_server_client

    # El server emite como "server_bot"
    msg = BroadcastMessage(
        timestamp=time.time(),
        agent_id="server_bot",
        chat_id=CHAT_ID,
        message="no debo verme a mí mismo",
    )

    await server.emit(msg)
    await asyncio.sleep(TICK)

    # El server tiene agent_id="server_bot" → anti-loop activo → buffer propio vacío
    msgs_server = buf_server.recent(CHAT_ID)
    assert msgs_server == []


# ---------------------------------------------------------------------------
# Test: anti-loop — client no ve mensajes con su propio agent_id en buffer
# ---------------------------------------------------------------------------


async def test_antiloop_client_no_ve_sus_propios_mensajes(par_server_client):
    """El client emite; su propio buffer no recibe el mensaje (anti-loop en el client)."""
    server, client, buf_server, buf_client = par_server_client

    # El client emite como "client_bot"
    msg = BroadcastMessage(
        timestamp=time.time(),
        agent_id="client_bot",
        chat_id=CHAT_ID,
        message="mensaje del client",
    )

    await client.emit(msg)
    await asyncio.sleep(TICK)

    # El client tiene agent_id="client_bot" — el fan-out del server envía de vuelta al client.
    # El client debe descartar el mensaje porque agent_id == self._agent_id.
    msgs_client = buf_client.recent(CHAT_ID)
    assert msgs_client == []


# ---------------------------------------------------------------------------
# Test: múltiples mensajes se acumulan en orden
# ---------------------------------------------------------------------------


async def test_multiples_mensajes_acumulados_en_orden(par_server_client):
    """Varios mensajes del client llegan al server en orden cronológico."""
    server, client, buf_server, buf_client = par_server_client

    for i in range(3):
        msg = BroadcastMessage(
            timestamp=time.time() + i * 0.001,
            agent_id="client_bot",
            chat_id=CHAT_ID,
            message=f"mensaje {i}",
        )
        await client.emit(msg)
        await asyncio.sleep(0.01)

    await asyncio.sleep(TICK)

    msgs = buf_server.recent(CHAT_ID)
    assert len(msgs) == 3
    contenidos = [m.message for m in msgs]
    assert contenidos == ["mensaje 0", "mensaje 1", "mensaje 2"]
