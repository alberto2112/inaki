"""Contrato de CANAL — lo que el kernel exige de cualquier canal (Telegram, Slack, CLI...).

Un canal es un paquete que implementa TRES cosas, y nada más:

1. ``IChannel`` — ciclo de vida: ``start()`` / ``stop()``. El daemon itera canales
   sin saber cuál es cuál.
2. ``IChannelOutbound`` — el egress ÚNICO del canal: texto, media, álbum. TODO lo
   que sale por un canal pasa por acá (respuesta del turno, ``channel_send`` del
   scheduler, ``/admin/send``, tools, narración intermedia, resultados ``bg-N``).
   Es el BORDE del transporte: formateo, troceo, límites de caption y persistencia
   en historial se aplican UNA vez, en la implementación, nunca en los call-sites.
3. Su sección de config (registrada en ``inaki.config``) y sus tools propias
   (registradas en el registry de tools del agente).

Los sinks intermedios (``IIntermediateSink``) son una VISTA del outbound: un
intermedio es "texto al canal sin historial" (``OutboundIntermediateSink``). Se
mantienen como interfaz mínima porque es lo único que el tool loop necesita.

Antes había tres ports para "mandar algo a un canal" (``IOutboundSink`` del
scheduler, ``IChannelOutbound`` de las tools, ``IIntermediateSink`` del loop) y
cuatro caminos de egress hacia Telegram. Nota ``egress-unico`` en migraciones.md.

Dueño único del rastro
----------------------
El historial de un scope tiene UN dueño por envío. Cuando el envío ocurre dentro
de un turno con tool loop y ``chat_history.persist_tool_calls`` está activo, el
dueño es el loop: persiste el ``assistant+tool_calls`` (con los argumentos del
envío) y su ``tool result``. Que el adapter agregue ADEMÁS su propia fila no solo
duplica — la mete ENTRE el assistant y su result, partiendo el grupo protocolar.
Por eso el caller que ya es dueño pasa ``record_history=False``. Fuera de un
turno (scheduler, REST admin) o con el flag apagado, nadie más registra: el
adapter persiste y ``record_history`` queda en ``True``.

Reglas de validación en ``send()``:
- ``kind=TEXT``: requiere ``text`` no vacío.
- ``kind`` de media individual (PHOTO, AUDIO, VIDEO, FILE): requiere exactamente
  1 elemento en ``sources``.
- ``kind=ALBUM``: requiere al menos 1 elemento en ``sources`` (si es 1, el
  adapter debería delegar a PHOTO).
- Si el ``kind`` no está en ``capabilities()``, se lanza ``ValueError``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

from typing import Protocol

from inaki.kernel.domain.value_objects.dispatch_result import DispatchResult
from inaki.kernel.domain.value_objects.outbound_kind import OutboundKind

logger = logging.getLogger(__name__)


class IChannel(ABC):
    """Ciclo de vida de un canal. Lo arranca y detiene el composition root.

    ``start()`` DEBE fallar ruidosamente si el transporte no puede levantarse
    (bind de puerto, token inválido): un arranque que no puede fallar es un
    arranque que no se puede diagnosticar (``broadcast-arranque-observable``).
    ``stop()`` es idempotente.
    """

    #: Identificador del canal (``"telegram"``, ``"slack"``...). Coincide con la
    #: clave de ``channels:`` en la config y con ``IChannelOutbound.channel_name``.
    name: str

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...


class IChannelOutbound(ABC):
    """Puerto de envío saliente genérico por canal."""

    #: Identificador del canal que implementa este adapter.
    #: Ejemplo: ``"telegram"``, ``"slack"``.
    channel_name: str

    #: Capacidad: mostrar al usuario un indicador efímero mientras el modelo
    #: razona (thinking mode). El kernel solo AVISA que está pasando
    #: (``IIntermediateSink.thinking``); mostrarlo o no es del canal. Default
    #: ``False``: un outbound que no lo declare no muestra nada.
    shows_thinking: bool = False

    @abstractmethod
    def capabilities(self) -> set[OutboundKind]:
        """Retorna el conjunto de kinds que este canal soporta.

        Un adapter que no soporta cierto kind debe NO incluirlo aquí. Al
        llamar ``send()`` con un kind no soportado, se lanza ``ValueError``.
        """

    @abstractmethod
    async def send(
        self,
        *,
        chat_id: str,
        kind: OutboundKind,
        text: str | None = None,
        sources: list[Path] | None = None,
        caption: str | None = None,
        record_history: bool = True,
    ) -> None:
        """Envía un payload al canal.

        Args:
            chat_id: Identificador del destinatario dentro del canal.
            kind: Tipo de contenido a enviar.
            text: Texto del mensaje. Requerido cuando ``kind=TEXT``.
            sources: Paths locales de los archivos a enviar. Requerido cuando
                ``kind`` es media (PHOTO, AUDIO, VIDEO, FILE, ALBUM).
            caption: Texto descriptivo adjunto a un archivo o álbum. Opcional.
            record_history: Persistir el envío en el historial del scope destino.
                ``False`` solo cuando el caller YA es dueño del rastro (ver
                "Dueño único del rastro" arriba). El envío en sí no cambia.

        Raises:
            ValueError: Si el kind no está en ``capabilities()``, si falta
                ``text`` para TEXT, o si falta ``sources`` para media.
            FileNotFoundError: Si algún path en ``sources`` no existe.
            RuntimeError: Si el canal no está disponible (ej: bot no configurado).
        """


class IIntermediateSink(ABC):
    """Contrato para emitir texto intermedio del asistente durante un turno."""

    @abstractmethod
    async def emit(self, text: str) -> None:
        """Empuja ``text`` al canal del usuario.

        Debe ser no-bloqueante a efectos prácticos: el tool loop llama a
        este método entre iteraciones y una latencia alta retrasa la
        ejecución de las tools.
        """
        ...

    async def thinking(self) -> None:
        """El modelo está razonando (thinking mode): feedback EFÍMERO.

        No es narración del turno: no se persiste ni se broadcastea, por eso es
        un método aparte de ``emit``. El kernel lo llama una vez por turno cuando
        el provider activa thinking; el sink decide si lo muestra. Default: nada.
        """
        return None


class NullIntermediateSink(IIntermediateSink):
    """Sink que descarta los mensajes intermedios.

    Se usa como default para no obligar a todos los callers a pasar un
    sink cuando no les interesa (scheduler sin destino interactivo,
    tests, one-shot, etc.).
    """

    async def emit(self, text: str) -> None:  # noqa: D401 — sink no-op
        return None


class BufferingIntermediateSink(IIntermediateSink):
    """Acumula los intermedios en memoria (canales request/response: REST, CLI vía REST).

    El inbound crea el sink, lo pasa a ``run_agent.execute(..., intermediate_sink=sink)``
    y al terminar el turno lee ``messages`` para devolverlos junto a la respuesta.
    """

    def __init__(self) -> None:
        self._messages: list[str] = []

    async def emit(self, text: str) -> None:
        self._messages.append(text)

    @property
    def messages(self) -> list[str]:
        """Copia de los mensajes acumulados, en orden de emisión."""
        return list(self._messages)


class OutboundIntermediateSink(IIntermediateSink):
    """La VISTA "intermedio" del outbound: cada bloque va al canal en vivo, sin historial.

    ``record_history=False`` siempre: la narración intermedia la persiste el turno
    (``RecordingIntermediateSink`` / ``PersistingIntermediateSink`` en el kernel), y
    un fallo de red NUNCA rompe el tool loop — se loguea y se sigue.
    """

    def __init__(self, outbound: IChannelOutbound, chat_id: str) -> None:
        self._outbound = outbound
        self._chat_id = chat_id

    async def thinking(self) -> None:
        if self._outbound.shows_thinking:
            await self.emit("Thinking...")

    async def emit(self, text: str) -> None:
        try:
            await self._outbound.send(
                chat_id=self._chat_id, kind=OutboundKind.TEXT, text=text, record_history=False
            )
        except Exception as exc:  # noqa: BLE001 — un intermedio perdido no vale un turno roto
            logger.warning(
                "Intermedio no entregado a %s:%s: %s",
                self._outbound.channel_name,
                self._chat_id,
                exc,
            )


class IChannelSender(Protocol):
    """Resuelve un ``target`` (ej: ``"telegram:123"``) y entrega por el outbound del agente.

    Lo satisface ``ChannelRouter``; lo consumen el scheduler (``channel_send``,
    ``agent_send``) y la cola de delegación en background (``bg-N``). ``agent_id``
    es el DUEÑO del envío (decide bot e historial); ``record_history=False`` cuando
    el caller ya es dueño del rastro (turno de ``agent_send``, resultado ``bg-N``).
    """

    async def send_message(
        self,
        target: str,
        text: str,
        *,
        agent_id: str | None = None,
        record_history: bool = True,
    ) -> DispatchResult: ...

    def build_intermediate_sink(
        self, target: str, *, agent_id: str | None = None
    ) -> IIntermediateSink: ...

    def is_conversational(self, channel: str, agent_id: str | None) -> bool: ...
