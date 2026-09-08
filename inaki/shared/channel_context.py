from __future__ import annotations

from contextvars import ContextVar, Token

from pydantic import BaseModel, computed_field, field_validator


class ChannelContext(BaseModel, frozen=True):
    """Value object que identifica el canal y usuario de una conversación.

    Atributos:
        channel_type: Tipo de canal, por ejemplo "telegram", "cli", "rest", "daemon".
        user_id: Identificador del usuario en ese canal, por ejemplo "123456", "local".
            En chats grupales NO es un id de usuario real (es el id del agente); la
            identidad de la conversación es ``chat_id`` — ver ``context_id``.
        chat_id: Identificador del chat real del turno (en Telegram puede ser el ID
            del grupo o el privado del usuario; en grupos NO coincide con ``user_id``).
            ``None`` cuando el canal no distingue chat de usuario (CLI/REST/daemon)
            o cuando el caller no lo informó.
        sender_name: Nombre legible compuesto del remitente para inyectar en el system
            prompt (ej: ``"Juan Pérez (@juan_dev)"``). En grupos refleja el ÚLTIMO
            emisor humano del batch (heurística de ``group_flow.py``, NO la identidad
            del grupo). ``None`` cuando no aplica: CLI, REST, scheduler triggers, o
            grupo sin mensajes humanos previos.
        username: Handle ``@username`` del remitente sin el ``@`` inicial. ``None`` cuando
            el usuario no tiene username configurado en su perfil (Telegram lo permite).
        first_name: Nombre del remitente. En Telegram es un campo requerido por la API
            (siempre presente para mensajes humanos), pero queda ``None`` para canales
            que no lo distinguen.
        last_name: Apellido del remitente. ``None`` cuando el usuario no lo configuró
            o el canal no lo provee.
        routing_key: Clave compuesta ``"{channel_type}:{user_id}"`` para enrutamiento.
        context_id: Identidad estable de la ENTIDAD de contexto (``chat_id or user_id``).
            Clave del fichero de memoria caliente per-entidad; fuente ÚNICA para la
            lectura (``_read_user_context``) y la escritura (variable ``{{CHANNEL.CONTEXTID}}``).
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

    @computed_field  # type: ignore[misc]
    @property
    def context_id(self) -> str:
        """Identidad estable de la ENTIDAD de contexto de esta conversación.

        Fuente ÚNICA de la clave del fichero de memoria caliente
        (``users/{channel_type}/{context_id}.md``): la usan por igual la LECTURA
        (``RunAgentUseCase._read_user_context``) y la ESCRITURA (el LLM, vía la
        variable ``{{CHANNEL.CONTEXTID}}`` que el operador pone en el system prompt).
        Que ambos lados deriven de acá garantiza que el agente lea del mismo
        archivo donde escribe — en privado y en grupo por igual.

        Resuelve al ``chat_id`` (la conversación: privado o grupo) con fallback a
        ``user_id`` para canales que no distinguen chat de usuario (CLI/REST/daemon).
        Nunca vacío: el validador garantiza ``user_id`` no vacío.
        """
        return self.chat_id or self.user_id


# ---------------------------------------------------------------------------
# Contexto de canal del turno en curso (task-safe via contextvars)
# ---------------------------------------------------------------------------
#
# ``execute()`` publica acá el ``ChannelContext`` del turno que está corriendo
# y lo limpia al terminar (token + reset). Como cada turno corre en su propia
# cadena de tasks de asyncio, dos turnos concurrentes del mismo agente ven cada
# uno SU contexto — a diferencia del slot mutable por-container que reemplaza,
# que se pisaba entre turnos (race con cross-user leak en ``{{CHANNEL.*}}`` y
# en las tools que enrutan por ``routing_key``).
#
# Consumidores: las tools que necesitan saber "desde qué conversación me
# llamaron" (scheduler channel_send, face tools, send/download de Telegram,
# delegate) leen esto vía ``AgentContainer.get_channel_context()`` durante el
# tool loop, que SIEMPRE ocurre dentro de un ``execute()`` en curso.

_current_channel_context: ContextVar[ChannelContext | None] = ContextVar(
    "current_channel_context", default=None
)


def current_channel_context() -> ChannelContext | None:
    """Devuelve el ``ChannelContext`` del turno en curso, o ``None`` si no hay turno."""
    return _current_channel_context.get()


def set_current_channel_context(ctx: ChannelContext | None) -> Token:
    """Publica el contexto del turno. Devuelve el token para restaurar con ``reset``."""
    return _current_channel_context.set(ctx)


def reset_current_channel_context(token: Token) -> None:
    """Restaura el valor previo al ``set`` correspondiente (fin del turno)."""
    _current_channel_context.reset(token)
