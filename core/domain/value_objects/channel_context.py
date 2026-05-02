from __future__ import annotations

from pydantic import BaseModel, computed_field, field_validator


class ChannelContext(BaseModel, frozen=True):
    """Value object que identifica el canal y usuario de una conversación.

    Atributos:
        channel_type: Tipo de canal, por ejemplo "telegram", "cli", "rest", "daemon".
        user_id: Identificador del usuario en ese canal, por ejemplo "123456", "local".
        chat_id: Identificador del chat real del turno (en Telegram puede ser el ID
            del grupo o el privado del usuario; en grupos NO coincide con ``user_id``).
            ``None`` cuando el canal no distingue chat de usuario (CLI/REST/daemon)
            o cuando el caller no lo informó.
        routing_key: Clave compuesta ``"{channel_type}:{user_id}"`` para enrutamiento.
    """

    channel_type: str
    user_id: str
    chat_id: str | None = None

    @field_validator("channel_type", "user_id", mode="before")
    @classmethod
    def _no_vacio(cls, v: str) -> str:
        """Rechaza cadenas vacías o que contienen solo espacios en blanco."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("no puede ser vacío ni contener solo espacios en blanco")
        return v

    @field_validator("chat_id", mode="before")
    @classmethod
    def _chat_id_normalizar(cls, v: str | None) -> str | None:
        """Permite ``None`` pero rechaza strings vacíos/blancos para evitar ambigüedad."""
        if v is None:
            return None
        if not isinstance(v, str) or not v.strip():
            raise ValueError("chat_id no puede ser vacío; usá None si no aplica")
        return v

    @computed_field  # type: ignore[misc]
    @property
    def routing_key(self) -> str:
        """Clave de enrutamiento en formato ``channel_type:user_id``."""
        return f"{self.channel_type}:{self.user_id}"
