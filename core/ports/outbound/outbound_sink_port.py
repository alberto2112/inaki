"""Puerto outbound para sinks de envío de mensajes.

Un sink representa un destino concreto al que puede enrutarse el texto
de una task del scheduler: Telegram, archivo, null (descarta), webhook,
etc. Cada implementación concreta declara un ``prefix`` que el factory
usa para parsear strings de target (``"<prefix>:<destino>"``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.domain.value_objects.dispatch_result import DispatchResult


class IOutboundSink(ABC):
    """Contrato para cualquier sink de salida del scheduler.

    Atributos de clase:
        prefix: Prefijo del target que este sink maneja (p. ej. ``"telegram"``,
            ``"file"``, ``"null"``). El ``SinkFactory`` lo usa para enrutar.
    """

    prefix: str

    @abstractmethod
    async def send(self, target: str, text: str) -> DispatchResult:
        """Envía ``text`` al destino ``target``.

        Args:
            target: Identificador completo del destino, con prefix incluido
                (p. ej. ``"telegram:12345"``, ``"file:///tmp/out.log"``).
            text: Contenido a entregar.

        Returns:
            ``DispatchResult`` con el par ``(original_target, resolved_target)``.
            Para sinks nativos directos ambos suelen coincidir; la distinción
            gana relevancia cuando el ``ChannelRouter`` resuelve un fallback.
        """
        ...
