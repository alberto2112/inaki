"""Config del canal CLI (chat interactivo vía REST): sección ``channels.cli`` y su registro."""

from __future__ import annotations

from inaki.config.channels import registrar_canal
from inaki.config.schema._base import _ConfigBaseModel


class CliChannelConfig(_ConfigBaseModel):
    """
    Config tipada del canal CLI/REST.

    Es el bloque ``channels.cli`` que consume el admin server al armar el
    ``ChannelContext`` de un turno conversacional sin canal de mensajería.
    """

    user: str | None = None
    """Identidad ESTABLE del turno CLI/REST. Se usa como ``user_id`` y como
    ``context_id`` (nombra ``~/.inaki/users/cli/{user}.md``) y puebla
    ``{{CHANNEL.USERNAME}}``. ``None`` → el ``context_id`` es el ``session_id``
    (UUID efímero por proceso, sin fichero pre-escribible)."""


def registrar() -> None:
    """Registra la sección ``channels.cli`` en el módulo de config."""
    registrar_canal("cli", CliChannelConfig)
