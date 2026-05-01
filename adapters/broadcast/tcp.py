"""
TcpBroadcastAdapter — transporte TCP para el canal de broadcast multi-agente.

Implementa ``BroadcastEmitter`` y ``BroadcastReceiver`` en una única clase.
Soporta dos roles mutuamente excluyentes:

- **server**: escucha conexiones entrantes con ``asyncio.start_server``, hace fan-out
  de cada mensaje válido a todos los demás clientes conectados.
- **client**: conecta a un server remoto con ``asyncio.open_connection``; reconecta
  con backoff exponencial + jitter si la conexión se pierde.

Protocolo wire: JSON line-delimited (un objeto por línea). Cada mensaje incluye
``timestamp`` (float epoch UTC), ``agent_id``, ``chat_id``, ``message`` y ``hmac``
(HMAC-SHA256 hex sobre ``f"{timestamp}|{agent_id}|{chat_id}|{message}"``).

Freshness: se descarta cualquier mensaje con ``|now - timestamp| > 60s`` (anti-replay).
Anti-loop: se descarta silenciosamente si ``msg.agent_id == self.agent_id``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import random
import time
from typing import Literal

from core.domain.services.broadcast_buffer import BroadcastBuffer
from core.ports.outbound.broadcast_port import BroadcastCallback, BroadcastMessage

logger = logging.getLogger(__name__)

# Ventana de frescura para validación HMAC anti-replay (segundos).
_FRESHNESS_WINDOW = 60.0


_VALID_EVENT_TYPES = frozenset(
    {"assistant_response", "user_input_voice", "user_input_photo"}
)


def _firmar(
    auth: str,
    timestamp: float,
    agent_id: str,
    chat_id: str,
    event_type: str,
    sender: str,
    content: str,
) -> str:
    """Calcula HMAC-SHA256 sobre los campos canónicos del mensaje.

    El string canónico es
    ``f"{timestamp}|{agent_id}|{chat_id}|{event_type}|{sender}|{content}"``.
    El resultado es el hex digest del HMAC.

    Args:
        auth: Secreto compartido (clave HMAC).
        timestamp: Epoch UTC (float).
        agent_id: Identificador del agente emisor.
        chat_id: Identificador del chat de origen.
        event_type: Tipo de evento (assistant_response | user_input_voice | user_input_photo).
        sender: Nombre del humano emisor (vacío para assistant_response).
        content: Texto del evento.

    Returns:
        Hex digest del HMAC.
    """
    canonico = f"{timestamp}|{agent_id}|{chat_id}|{event_type}|{sender}|{content}"
    return hmac.new(auth.encode(), canonico.encode(), hashlib.sha256).hexdigest()


def _verificar_hmac(
    auth: str,
    timestamp: float,
    agent_id: str,
    chat_id: str,
    event_type: str,
    sender: str,
    content: str,
    digest_recibido: str,
) -> bool:
    """Verifica en tiempo constante el HMAC de un mensaje recibido.

    Args:
        auth: Secreto compartido.
        timestamp: Epoch UTC del mensaje.
        agent_id: Identificador del emisor.
        chat_id: Identificador del chat.
        event_type: Tipo de evento.
        sender: Nombre del humano emisor.
        content: Texto del evento.
        digest_recibido: Hex digest recibido en el wire frame.

    Returns:
        ``True`` si el HMAC es válido, ``False`` en caso contrario.
    """
    esperado = _firmar(auth, timestamp, agent_id, chat_id, event_type, sender, content)
    return hmac.compare_digest(esperado, digest_recibido)


class TcpBroadcastAdapter:
    """Adapter TCP para broadcast multi-agente. Implementa emitter + receiver.

    Un agente declara ``port`` en su config (rol server) o ``remote.host`` (rol
    client). La clase se ocupa del ciclo de vida de la conexión TCP y delega el
    almacenamiento efímero al ``BroadcastBuffer`` inyectado.
    """

    def __init__(
        self,
        agent_id: str,
        role: Literal["server", "client"],
        host: str,
        port: int,
        auth: str,
        buffer: BroadcastBuffer,
        reconnect_max_backoff: float = 30.0,
    ) -> None:
        """Inicializa el adapter sin abrir ningún socket.

        La apertura de sockets ocurre en ``start()``.

        Args:
            agent_id: Identificador único de este agente (usado para anti-loop).
            role: ``"server"`` escucha conexiones; ``"client"`` conecta a un server.
            host: Host de bind (server) o de conexión (client).
            port: Puerto de bind (server) o de conexión (client).
            auth: Secreto compartido para HMAC-SHA256.
            buffer: Buffer efímero de contexto inyectado desde el container.
            reconnect_max_backoff: Cap en segundos para el backoff exponencial en
                modo client. Por defecto 30s.
        """
        self._agent_id = agent_id
        self._role = role
        self._host = host
        self._port = port
        self._auth = auth
        self._buffer = buffer
        self._reconnect_max_backoff = reconnect_max_backoff

        # Callbacks registrados vía subscribe()
        self._callbacks: list[BroadcastCallback] = []

        # Estado de lifecycle
        self._iniciado = False
        self._tarea_principal: asyncio.Task | None = None

        # --- Modo server ---
        # Conjunto de writers de los clientes conectados. Accedido SOLO desde
        # el event loop (sin locks necesarios con asyncio single-threaded).
        self._clientes: set[asyncio.StreamWriter] = set()
        self._server_obj: asyncio.Server | None = None

        # --- Modo client ---
        # Writer de la conexión upstream activa. None cuando no hay conexión.
        self._writer_upstream: asyncio.StreamWriter | None = None

    # ------------------------------------------------------------------
    # BroadcastEmitter
    # ------------------------------------------------------------------

    async def emit(self, msg: BroadcastMessage) -> None:
        """Emite un mensaje al canal de broadcast (fire-and-forget).

        Firma el mensaje con HMAC-SHA256 y lo serializa como JSON line. Si no
        hay conexiones activas, el mensaje se descarta silenciosamente.

        Args:
            msg: Mensaje a emitir.
        """
        linea = self._serializar(msg)
        datos = (linea + "\n").encode()

        if self._role == "server":
            await self._escribir_a_todos(datos, excluir=None)
        else:
            # Modo client: escribir al upstream si existe
            if self._writer_upstream is None or self._writer_upstream.is_closing():
                return
            await self._escribir_writer(self._writer_upstream, datos)

    # ------------------------------------------------------------------
    # BroadcastReceiver
    # ------------------------------------------------------------------

    async def subscribe(self, callback: BroadcastCallback) -> None:
        """Registra un callback invocado por cada mensaje broadcast válido recibido.

        Los callbacks se ejecutan como tareas asyncio independientes (fire-and-forget)
        para no bloquear el bucle de lectura.

        Args:
            callback: Función asíncrona que recibe el ``BroadcastMessage`` validado.
        """
        self._callbacks.append(callback)

    def recent(self, chat_id: str) -> list[BroadcastMessage]:
        """Retorna los mensajes recientes del buffer para el chat dado.

        Delega directamente a ``BroadcastBuffer.recent()``.

        Args:
            chat_id: Identificador del chat a consultar.

        Returns:
            Lista de mensajes en orden cronológico (más antiguo primero).
        """
        return self._buffer.recent(chat_id)

    def render(self, chat_id: str) -> str | None:
        """Renderiza el contexto de broadcast para el chat dado como texto markdown.

        Delega directamente a ``BroadcastBuffer.render()``.

        Args:
            chat_id: Identificador del chat a renderizar.

        Returns:
            Texto markdown con el contexto, o ``None`` si no hay mensajes.
        """
        return self._buffer.render(chat_id)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Inicia el server TCP o la conexión cliente según el rol configurado.

        Idempotente: si ya está iniciado, no hace nada.
        """
        if self._iniciado:
            return
        self._iniciado = True

        if self._role == "server":
            self._tarea_principal = asyncio.create_task(
                self._ejecutar_server(), name=f"broadcast:server:{self._agent_id}"
            )
        else:
            self._tarea_principal = asyncio.create_task(
                self._ejecutar_cliente(), name=f"broadcast:client:{self._agent_id}"
            )

    async def stop(self) -> None:
        """Detiene el adapter cerrando todas las conexiones y cancelando tareas.

        Idempotente: si no está iniciado, no hace nada.
        """
        if not self._iniciado:
            return
        self._iniciado = False

        # Cerrar clientes conectados ANTES de cancelar _tarea_principal.
        # serve_forever()/wait_closed() bloquean hasta que todos los handlers
        # de conexión terminen. Cerrar los writers aquí causa que reader.readline()
        # retorne EOF en _bucle_lectura, desbloqueando esos handlers de inmediato.
        for writer in list(self._clientes):
            await self._cerrar_writer(writer)
        self._clientes.clear()

        # Cancelar tarea principal
        if self._tarea_principal and not self._tarea_principal.done():
            self._tarea_principal.cancel()
            try:
                await self._tarea_principal
            except asyncio.CancelledError:
                pass
        self._tarea_principal = None

        # Cerrar server (modo server) — puede ya estar cerrado por serve_forever()
        if self._server_obj is not None:
            self._server_obj.close()
            await self._server_obj.wait_closed()
            self._server_obj = None

        # Cerrar conexión upstream (modo client)
        if self._writer_upstream is not None:
            await self._cerrar_writer(self._writer_upstream)
            self._writer_upstream = None

        logger.info(
            "broadcast.adapter.detenido", extra={"agent_id": self._agent_id, "role": self._role}
        )

    # ------------------------------------------------------------------
    # Modo server — internos
    # ------------------------------------------------------------------

    async def _ejecutar_server(self) -> None:
        """Cuerpo principal del rol server."""
        self._server_obj = await asyncio.start_server(
            self._manejar_conexion_cliente,
            host=self._host,
            port=self._port,
        )
        logger.info(
            "broadcast.server.started",
            extra={"host": self._host, "port": self._port, "agent_id": self._agent_id},
        )
        async with self._server_obj:
            await self._server_obj.serve_forever()

    async def _manejar_conexion_cliente(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Callback invocado por asyncio para cada nueva conexión al server."""
        peer = writer.get_extra_info("peername", default="?")
        self._clientes.add(writer)
        logger.info(
            "broadcast.server.client_connected",
            extra={"peer": str(peer), "agent_id": self._agent_id},
        )
        try:
            await self._bucle_lectura(reader, writer_origen=writer)
        finally:
            self._clientes.discard(writer)
            await self._cerrar_writer(writer)
            logger.info(
                "broadcast.server.client_disconnected",
                extra={"peer": str(peer), "agent_id": self._agent_id},
            )

    # ------------------------------------------------------------------
    # Modo client — internos
    # ------------------------------------------------------------------

    async def _ejecutar_cliente(self) -> None:
        """Cuerpo principal del rol client con reconexión y backoff exponencial."""
        backoff = 1.0
        while True:
            logger.info(
                "broadcast.client.connecting",
                extra={"host": self._host, "port": self._port, "agent_id": self._agent_id},
            )
            try:
                reader, writer = await asyncio.open_connection(self._host, self._port)
            except (OSError, ConnectionRefusedError) as exc:
                jitter = random.uniform(0, backoff * 0.2)
                espera = min(backoff + jitter, self._reconnect_max_backoff)
                logger.info(
                    "broadcast.client.reconnecting",
                    extra={
                        "agent_id": self._agent_id,
                        "backoff_seconds": round(espera, 2),
                        "reason": str(exc),
                    },
                )
                await asyncio.sleep(espera)
                backoff = min(backoff * 2, self._reconnect_max_backoff)
                continue

            # Conexión exitosa — resetear backoff
            backoff = 1.0
            self._writer_upstream = writer
            logger.info(
                "broadcast.client.connected",
                extra={"host": self._host, "port": self._port, "agent_id": self._agent_id},
            )
            try:
                await self._bucle_lectura(reader, writer_origen=None)
            except asyncio.CancelledError:
                break
            finally:
                self._writer_upstream = None
                await self._cerrar_writer(writer)

            # Si llega aquí sin CancelledError, la conexión se cerró — reconectar
            jitter = random.uniform(0, 0.5)
            espera = min(1.0 + jitter, self._reconnect_max_backoff)
            logger.info(
                "broadcast.client.reconnecting",
                extra={
                    "agent_id": self._agent_id,
                    "backoff_seconds": round(espera, 2),
                    "reason": "conexión cerrada por el server",
                },
            )
            await asyncio.sleep(espera)

    # ------------------------------------------------------------------
    # Bucle de lectura compartido (server por cliente / client upstream)
    # ------------------------------------------------------------------

    async def _bucle_lectura(
        self,
        reader: asyncio.StreamReader,
        writer_origen: asyncio.StreamWriter | None,
    ) -> None:
        """Lee líneas JSON del reader y procesa cada mensaje recibido.

        Si ``writer_origen`` no es None, el adapter está en modo server y
        hace fan-out del mensaje RAW a todos los demás clientes conectados.

        Args:
            reader: StreamReader de la conexión.
            writer_origen: Writer del cliente emisor (para excluirlo del fan-out).
                           ``None`` en modo client.
        """
        while True:
            try:
                linea_bytes = await reader.readline()
            except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
                break

            if not linea_bytes:
                # EOF — conexión cerrada limpiamente
                break

            linea = linea_bytes.decode(errors="replace").rstrip("\n")
            if not linea:
                continue

            msg = self._parsear_y_validar(linea)
            if msg is None:
                continue

            # Fan-out en modo server: retransmitir la línea raw a los demás clientes
            if writer_origen is not None:
                datos_raw = linea_bytes if linea_bytes.endswith(b"\n") else linea_bytes + b"\n"
                await self._escribir_a_todos(datos_raw, excluir=writer_origen)

            # Anti-loop: ignorar mensajes propios (silencioso — es comportamiento esperado)
            if msg.agent_id == self._agent_id:
                logger.debug(
                    "broadcast.message.dropped.own_agent_id",
                    extra={"agent_id": self._agent_id},
                )
                continue

            # Alimentar buffer y disparar callbacks
            self._buffer.append(msg)
            logger.info(
                "broadcast.message.received",
                extra={"from_agent_id": msg.agent_id, "chat_id": msg.chat_id},
            )
            for cb in self._callbacks:
                asyncio.ensure_future(cb(msg))

    # ------------------------------------------------------------------
    # Serialización / deserialización / validación
    # ------------------------------------------------------------------

    def _serializar(self, msg: BroadcastMessage) -> str:
        """Serializa un BroadcastMessage a JSON line sin el ``\\n`` final.

        Incluye el campo ``hmac`` calculado con el secreto compartido.

        Args:
            msg: Mensaje a serializar.

        Returns:
            JSON string (sin newline).
        """
        digest = _firmar(
            self._auth,
            msg.timestamp,
            msg.agent_id,
            msg.chat_id,
            msg.event_type,
            msg.sender,
            msg.content,
        )
        payload = {
            "timestamp": msg.timestamp,
            "agent_id": msg.agent_id,
            "chat_id": msg.chat_id,
            "event_type": msg.event_type,
            "sender": msg.sender,
            "content": msg.content,
            "hmac": digest,
        }
        return json.dumps(payload, ensure_ascii=False)

    def _parsear_y_validar(self, linea: str) -> BroadcastMessage | None:
        """Deserializa, verifica HMAC y frescura de una línea JSON recibida.

        Descarta el mensaje (retorna ``None``) si:
        - La línea no es JSON válido.
        - Faltan campos obligatorios.
        - El HMAC no coincide.
        - El timestamp está fuera de la ventana de frescura (60s).

        Args:
            linea: Línea JSON recibida del wire (sin ``\\n``).

        Returns:
            ``BroadcastMessage`` si es válido, ``None`` si debe descartarse.
        """
        try:
            datos = json.loads(linea)
        except json.JSONDecodeError:
            logger.warning(
                "broadcast.message.dropped.malformed",
                extra={"agent_id": self._agent_id, "linea": linea[:120]},
            )
            return None

        # Verificar campos obligatorios
        try:
            timestamp: float = float(datos["timestamp"])
            agent_id: str = str(datos["agent_id"])
            chat_id: str = str(datos["chat_id"])
            event_type: str = str(datos["event_type"])
            sender: str = str(datos.get("sender", ""))
            content: str = str(datos["content"])
            digest_recibido: str = str(datos["hmac"])
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "broadcast.message.dropped.malformed",
                extra={"agent_id": self._agent_id, "razon": "campo faltante o tipo inválido"},
            )
            return None

        # Validar event_type contra el conjunto cerrado
        if event_type not in _VALID_EVENT_TYPES:
            logger.warning(
                "broadcast.message.dropped.invalid_event_type",
                extra={"agent_id": self._agent_id, "event_type": event_type},
            )
            return None

        # Verificar HMAC
        if not _verificar_hmac(
            self._auth, timestamp, agent_id, chat_id, event_type, sender, content, digest_recibido
        ):
            logger.warning(
                "broadcast.message.dropped.hmac_mismatch",
                extra={"agent_id": self._agent_id, "from_agent_id": agent_id},
            )
            return None

        # Verificar frescura (anti-replay)
        ahora = time.time()
        if abs(ahora - timestamp) > _FRESHNESS_WINDOW:
            logger.warning(
                "broadcast.message.dropped.stale_timestamp",
                extra={
                    "agent_id": self._agent_id,
                    "from_agent_id": agent_id,
                    "desfase_segundos": round(ahora - timestamp, 1),
                },
            )
            return None

        return BroadcastMessage(
            timestamp=timestamp,
            agent_id=agent_id,
            chat_id=chat_id,
            event_type=event_type,  # type: ignore[arg-type]
            content=content,
            sender=sender,
        )

    # ------------------------------------------------------------------
    # Helpers de escritura
    # ------------------------------------------------------------------

    async def _escribir_a_todos(
        self,
        datos: bytes,
        excluir: asyncio.StreamWriter | None,
    ) -> None:
        """Escribe datos a todos los clientes conectados, excepto ``excluir``.

        Las escrituras fallidas se loggean y el cliente se elimina del conjunto.

        Args:
            datos: Bytes a enviar.
            excluir: Writer a excluir del fan-out. ``None`` para enviar a todos.
        """
        clientes_fallidos: list[asyncio.StreamWriter] = []
        for writer in list(self._clientes):
            if writer is excluir:
                continue
            exito = await self._escribir_writer(writer, datos)
            if not exito:
                clientes_fallidos.append(writer)

        for writer in clientes_fallidos:
            self._clientes.discard(writer)
            await self._cerrar_writer(writer)

    async def _escribir_writer(self, writer: asyncio.StreamWriter, datos: bytes) -> bool:
        """Escribe datos a un writer concreto.

        Args:
            writer: StreamWriter destino.
            datos: Bytes a enviar.

        Returns:
            ``True`` si la escritura fue exitosa, ``False`` si falló.
        """
        try:
            writer.write(datos)
            await writer.drain()
            return True
        except (ConnectionResetError, BrokenPipeError, OSError) as exc:
            logger.warning(
                "broadcast.emit.write_failure",
                extra={"agent_id": self._agent_id, "error": str(exc)},
            )
            return False

    @staticmethod
    async def _cerrar_writer(writer: asyncio.StreamWriter) -> None:
        """Cierra un StreamWriter ignorando errores de conexión ya cerrada.

        Args:
            writer: Writer a cerrar.
        """
        try:
            if not writer.is_closing():
                writer.close()
                await writer.wait_closed()
        except (OSError, ConnectionResetError):
            pass
