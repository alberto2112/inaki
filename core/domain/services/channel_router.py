"""Router de canales del kernel: resuelve un ``target`` y entrega por el outbound correcto.

Un ``target`` es ``"<canal>:<chat_id>"`` (``"telegram:-100123"``, ``"file:///var/x.log"``,
``"null:"``). El router lo resuelve contra DOS fuentes:

1. Los **outbounds del kernel** — destinos que no son canales conversacionales:
   ``file`` (append a un fichero) y ``null`` (descarta). Son el suelo del fallback.
2. El **registro de outbounds del agente** dueño del envío (``resolve_outbounds(agent_id)``):
   ahí viven los canales vivos (Telegram hoy; Slack mañana), cada uno con SU bot y
   SU historial. Resolver por dueño es lo que hace que un ``channel_send`` agendado
   en nombre del agente A salga por el bot de A y quede en el historial de A — con
   el viejo ``TelegramSink`` salía por "el primer bot que hubiera".

Cascada cuando el canal del target no está en ninguna de las dos: ``overrides[canal]``
→ ``default`` → ``hardcoded_fallback`` (un ``file://`` bajo ``<home>/data/``). El
``DispatchResult`` preserva siempre el ``original_target`` y reporta a dónde fue de
verdad; ``TaskLog.metadata`` lo persiste para auditoría.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.domain.services.channel_outbound_registry import ChannelOutboundRegistry
from core.domain.value_objects.dispatch_result import DispatchResult
from core.domain.value_objects.outbound_kind import OutboundKind
from core.ports.outbound.channel_port import (
    IChannelOutbound,
    IIntermediateSink,
    OutboundIntermediateSink,
)


@dataclass(frozen=True)
class ChannelFallbackSettings:
    """Settings VO del router — el composition root lo mapea desde ``ChannelFallbackConfig``.

    Atributos:
        default: target usado cuando un canal no resuelve y no tiene override.
            ``None`` delega al fallback hardcoded.
        overrides: ``canal → target`` para redirigir canales concretos.
    """

    default: str | None = None
    overrides: dict[str, str] = field(default_factory=dict)


def parse_target(target: str) -> tuple[str, str]:
    """``"telegram:123"`` → ``("telegram", "123")``; ``"file:///x"`` → ``("file", "/x")``.

    El ``chat_id`` de ``file`` es el path (se recorta el ``//`` del esquema URL).

    Raises:
        ValueError: si el target no tiene prefijo ``canal:``.
    """
    canal, sep, resto = target.partition(":")
    if not sep or not canal:
        raise ValueError(f"Target sin prefix de canal: '{target}'")
    if canal == "file":
        resto = resto.removeprefix("//")
    return canal, resto


class FileOutbound(IChannelOutbound):
    """Destino ``file``: append de ``<ISO8601> | <texto>`` al path que hace de ``chat_id``.

    Sin sandbox: el path se respeta literal. Crea el directorio padre. Un ``OSError``
    (permisos, disco lleno) propaga — el scheduler lo registra como fallo del trigger.
    Nunca persiste historial: un fichero no es una conversación.
    """

    channel_name = "file"

    def capabilities(self) -> set[OutboundKind]:
        return {OutboundKind.TEXT}

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
        if kind is not OutboundKind.TEXT:
            raise ValueError(f"El destino 'file' solo acepta kind=text (recibió {kind.value!r})")
        ruta = Path(chat_id)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        marca = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with ruta.open("a", encoding="utf-8") as fh:
            fh.write(f"{marca} | {text or ''}\n")


class NullOutbound(IChannelOutbound):
    """Destino ``null``: descarta. Para agendar sin notificar a nadie, y para tests."""

    channel_name = "null"

    def capabilities(self) -> set[OutboundKind]:
        return {OutboundKind.TEXT}

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
        return None


OutboundResolver = Callable[[str | None], ChannelOutboundRegistry | None]
"""Devuelve el registro de outbounds del agente, o ``None`` si no existe."""


class ChannelRouter:
    """Resuelve targets contra outbounds del kernel + outbounds del agente, con cascada."""

    def __init__(
        self,
        *,
        resolve_outbounds: OutboundResolver,
        fallback: ChannelFallbackSettings | None = None,
        hardcoded_fallback: str = "file:///tmp/inaki-schedule-output.log",
        kernel_outbounds: dict[str, IChannelOutbound] | None = None,
    ) -> None:
        self._resolve_outbounds = resolve_outbounds
        self._fallback = fallback or ChannelFallbackSettings()
        self._hardcoded = hardcoded_fallback
        self._kernel: dict[str, IChannelOutbound] = kernel_outbounds or {
            "file": FileOutbound(),
            "null": NullOutbound(),
        }

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def is_conversational(self, channel: str, agent_id: str | None) -> bool:
        """``True`` si ``agent_id`` tiene un outbound vivo para ``channel``.

        Un envío que resuelve a un canal conversacional llegó a una conversación
        real; uno que cae al fallback (fichero) no — y esa diferencia decide si el
        resultado de una delegación en background se entrega o se queda en el
        historial.
        """
        registro = self._resolve_outbounds(agent_id)
        return registro is not None and channel in registro.list_channels()

    # ------------------------------------------------------------------
    # Egress
    # ------------------------------------------------------------------

    async def send_message(
        self,
        target: str,
        text: str,
        *,
        agent_id: str | None = None,
        record_history: bool = True,
    ) -> DispatchResult:
        """Entrega ``text`` al target (o a su fallback) por el outbound del agente.

        Args:
            target: ``"<canal>:<chat_id>"`` original.
            text: contenido.
            agent_id: agente DUEÑO del envío — decide qué outbound (bot, historial)
                se usa. ``None``/vacío: solo resuelven los outbounds del kernel y el
                fallback; nada se persiste.
            record_history: si el outbound debe dejar el envío en el historial del
                agente. ``False`` cuando el caller YA es dueño del rastro (el turno
                de un ``agent_send``, un resultado ``bg-N``, un intermedio).
        """
        resolved, outbound, chat_id = self._resolve(target, agent_id)
        await outbound.send(
            chat_id=chat_id,
            kind=OutboundKind.TEXT,
            text=text,
            record_history=record_history and bool(agent_id),
        )
        return DispatchResult(original_target=target, resolved_target=resolved)

    def build_intermediate_sink(
        self, target: str, *, agent_id: str | None = None
    ) -> IIntermediateSink:
        """Sink de intermedios hacia el target resuelto (misma cascada que ``send_message``)."""
        _resolved, outbound, chat_id = self._resolve(target, agent_id)
        return OutboundIntermediateSink(outbound, chat_id)

    # ------------------------------------------------------------------
    # Resolución
    # ------------------------------------------------------------------

    def _resolve(self, target: str, agent_id: str | None) -> tuple[str, IChannelOutbound, str]:
        """Devuelve ``(resolved_target, outbound, chat_id)`` aplicando la cascada."""
        canal, _chat = parse_target(target)
        directo = self._lookup(target, agent_id)
        if directo is not None:
            return directo
        for candidato in (
            self._fallback.overrides.get(canal),
            self._fallback.default,
            self._hardcoded,
        ):
            if candidato is None:
                continue
            resuelto = self._lookup(candidato, agent_id)
            if resuelto is not None:
                return resuelto
        raise ValueError(
            f"Ningún destino resuelve '{target}' (canal '{canal}' sin outbound, "
            f"sin override ni default utilizables, y el fallback '{self._hardcoded}' tampoco)."
        )

    def _lookup(
        self, target: str, agent_id: str | None
    ) -> tuple[str, IChannelOutbound, str] | None:
        canal, chat_id = parse_target(target)
        if canal in self._kernel:
            return target, self._kernel[canal], chat_id
        registro = self._resolve_outbounds(agent_id)
        if registro is not None and canal in registro.list_channels():
            return target, registro.get(canal), chat_id
        return None
