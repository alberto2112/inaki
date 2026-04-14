from __future__ import annotations

from pydantic import BaseModel, computed_field, field_validator


class ChannelContext(BaseModel, frozen=True):
    """Value object que identifica el canal y usuario de una conversación.

    Atributos:
        channel_type: Tipo de canal, por ejemplo "telegram", "cli", "rest", "daemon".
        user_id: Identificador del usuario en ese canal, por ejemplo "123456", "local".
        routing_key: Clave compuesta ``"{channel_type}:{user_id}"`` para enrutamiento.
    """

    channel_type: str
    user_id: str

    @field_validator("channel_type", "user_id", mode="before")
    @classmethod
    def _no_vacio(cls, v: str) -> str:
        """Rechaza cadenas vacías o que contienen solo espacios en blanco."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("no puede ser vacío ni contener solo espacios en blanco")
        return v

    @computed_field  # type: ignore[misc]
    @property
    def routing_key(self) -> str:
        """Clave de enrutamiento en formato ``channel_type:user_id``."""
        return f"{self.channel_type}:{self.user_id}"
