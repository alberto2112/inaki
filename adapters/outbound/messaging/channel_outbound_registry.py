"""Registro de adapters de envío saliente por canal.

Mantiene una tabla ``channel_name → IChannelOutbound`` para que el resto del
sistema pueda resolver el adapter correcto dado un nombre de canal, sin
necesidad de imports directos ni factory global.

Sin estado global: cada instancia es independiente. En producción, una única
instancia vive en ``AppContainer`` o ``AgentContainer`` y se comparte entre
todos los componentes que necesiten enviar mensajes.
"""

from __future__ import annotations

import logging

from core.domain.value_objects.outbound_kind import OutboundKind
from core.ports.outbound.channel_outbound_port import IChannelOutbound

logger = logging.getLogger(__name__)


class ChannelOutboundRegistry:
    """Registry de adapters de canal saliente.

    Permite registrar y recuperar adapters por nombre de canal. No tiene
    estado global: instanciar para cada ``AppContainer``.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, IChannelOutbound] = {}

    def register(self, adapter: IChannelOutbound) -> None:
        """Registra un adapter por su ``channel_name``.

        Si ya existía un adapter para ese canal, lo sobreescribe con warning.
        """
        nombre = adapter.channel_name
        if nombre in self._adapters:
            logger.warning(
                "ChannelOutboundRegistry: sobreescribiendo adapter existente para canal '%s'",
                nombre,
            )
        self._adapters[nombre] = adapter
        logger.debug(
            "ChannelOutboundRegistry: adapter '%s' registrado (capabilities=%s)",
            nombre,
            {k.value for k in adapter.capabilities()},
        )

    def get(self, channel: str) -> IChannelOutbound:
        """Retorna el adapter registrado para el canal dado.

        Raises:
            KeyError: Si el canal no tiene adapter registrado.
        """
        if channel not in self._adapters:
            canales_disponibles = ", ".join(sorted(self._adapters.keys())) or "(ninguno)"
            raise KeyError(
                f"No hay adapter registrado para el canal '{channel}'. "
                f"Canales disponibles: {canales_disponibles}"
            )
        return self._adapters[channel]

    def supports(self, channel: str, kind: OutboundKind) -> bool:
        """Retorna ``True`` si el canal tiene adapter registrado y soporta el kind."""
        if channel not in self._adapters:
            return False
        return kind in self._adapters[channel].capabilities()

    def list_channels(self) -> list[str]:
        """Retorna la lista de canales registrados (orden de inserción)."""
        return list(self._adapters.keys())
