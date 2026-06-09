"""Puerto genérico de envío saliente por canal.

Define la abstracción que todos los canales de salida deben implementar
(Telegram, Slack, Discord, etc.). El core usa este port sin saber nada de la
tecnología subyacente.

Contrato del adapter:
- ``channel_name`` identifica el canal (ej: ``"telegram"``, ``"slack"``).
- ``capabilities()`` declara los ``OutboundKind`` que soporta el adapter.
- ``send()`` es el único punto de entrada para envío. Valida las precondiciones
  antes de delegar al canal.
- El adapter es responsable de persistir el envío exitoso en ``IHistoryStore``
  bajo el scope ``(agent_id, channel_name, chat_id)`` con ``Role.ASSISTANT``.
  Esto NO es opcional: garantiza que el historial refleje lo enviado.

Reglas de validación en ``send()``:
- ``kind=TEXT``: requiere ``text`` no vacío.
- ``kind`` de media individual (PHOTO, AUDIO, VIDEO, FILE): requiere exactamente
  1 elemento en ``sources``.
- ``kind=ALBUM``: requiere al menos 1 elemento en ``sources`` (si es 1, el
  adapter debería delegar a PHOTO).
- Si el ``kind`` no está en ``capabilities()``, se lanza ``ValueError``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from core.domain.value_objects.outbound_kind import OutboundKind


class IChannelOutbound(ABC):
    """Puerto de envío saliente genérico por canal."""

    #: Identificador del canal que implementa este adapter.
    #: Ejemplo: ``"telegram"``, ``"slack"``.
    channel_name: str

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
    ) -> None:
        """Envía un payload al canal.

        Args:
            chat_id: Identificador del destinatario dentro del canal.
            kind: Tipo de contenido a enviar.
            text: Texto del mensaje. Requerido cuando ``kind=TEXT``.
            sources: Paths locales de los archivos a enviar. Requerido cuando
                ``kind`` es media (PHOTO, AUDIO, VIDEO, FILE, ALBUM).
            caption: Texto descriptivo adjunto a un archivo o álbum. Opcional.

        Raises:
            ValueError: Si el kind no está en ``capabilities()``, si falta
                ``text`` para TEXT, o si falta ``sources`` para media.
            FileNotFoundError: Si algún path en ``sources`` no existe.
            RuntimeError: Si el canal no está disponible (ej: bot no configurado).
        """
