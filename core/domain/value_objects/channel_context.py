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
        sender_name: Nombre legible compuesto del remitente para inyectar en el system
            prompt (ej: ``"Juan Pérez (@juan_dev)"``). ``None`` cuando no aplica: chats
            grupales (la identidad va embebida en el contenido del mensaje vía
            ``format_group_message``), CLI, REST, scheduler triggers, etc.
        username: Handle ``@username`` del remitente sin el ``@`` inicial. ``None`` cuando
            el usuario no tiene username configurado en su perfil (Telegram lo permite).
        first_name: Nombre del remitente. En Telegram es un campo requerido por la API
            (siempre presente para mensajes humanos), pero queda ``None`` para canales
            que no lo distinguen.
        last_name: Apellido del remitente. ``None`` cuando el usuario no lo configuró
            o el canal no lo provee.
        routing_key: Clave compuesta ``"{channel_type}:{user_id}"`` para enrutamiento.
    """

    channel_type: str
    user_id: str
    chat_id: str | None = None
    sender_name: str | None = None
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None

    @field_validator("channel_type", "user_id", mode="before")
    @classmethod
    def _no_vacio(cls, v: str) -> str:
        """Rechaza cadenas vacías o que contienen solo espacios en blanco."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("no puede ser vacío ni contener solo espacios en blanco")
        return v

    @field_validator("chat_id", "sender_name", "username", "first_name", "last_name", mode="before")
    @classmethod
    def _opcional_normalizar(cls, v: str | None) -> str | None:
        """Permite ``None`` pero rechaza strings vacíos/blancos para evitar ambigüedad.

        Patrón compartido por todos los campos opcionales: o el caller informa un
        valor real, o pasa ``None`` explícito. Un string vacío sería ambiguo (¿es
        "no aplica" o "el usuario tiene nombre = ''"?) y rompería el contrato del
        resolver de variables, que distingue ``None`` (deja literal) de un valor real.
        """
        if v is None:
            return None
        if not isinstance(v, str) or not v.strip():
            raise ValueError("no puede ser vacío; usá None si no aplica")
        return v

    @computed_field  # type: ignore[misc]
    @property
    def routing_key(self) -> str:
        """Clave de enrutamiento en formato ``channel_type:user_id``."""
        return f"{self.channel_type}:{self.user_id}"
