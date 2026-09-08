"""Canal ``telegram``: token, autorización, grupos y transporte de broadcast.

Se muda entero a ``inaki/channels/telegram/`` cuando ese módulo exista (fase 4).

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from inaki.config.schema._base import _ConfigBaseModel


class BroadcastServerConfig(_ConfigBaseModel):
    """Rol **server** del broadcast: esta instancia escucha conexiones entrantes."""

    port: int = Field(ge=1024, le=65535)
    """Puerto TCP en el que escucha el servidor (1024..65535). Escucha en todas
    las interfaces de la LAN (``0.0.0.0``)."""


class BroadcastClientConfig(_ConfigBaseModel):
    """Rol **client** del broadcast: esta instancia se conecta a un server remoto."""

    host: str
    """Dirección IP o hostname del servidor (sin puerto — ese va en ``port``)."""

    port: int = Field(ge=1024, le=65535)
    """Puerto TCP del servidor remoto (1024..65535)."""


class BroadcastEmitConfig(_ConfigBaseModel):
    """Flags por agente que controlan qué tipos de eventos se emiten al broadcast.

    Cada flag corresponde a un ``event_type`` del ``BroadcastMessage``:

    - ``assistant_response`` (default ``True``): respuestas del LLM tras un turno.
      Backward-compat con el comportamiento original del broadcast.
    - ``user_input_voice`` (default ``False``): transcripciones de audio. El admin
      lo activa en UN bot del grupo con capacidad de transcripción para evitar
      duplicados.
    - ``user_input_photo`` (default ``False``): descripciones de foto. El admin
      lo activa en UN bot del grupo con capacidad de visión.

    El modelo es ``strict=True`` para rechazar coerciones implícitas (e.g.,
    string ``"yes"`` o entero ``2`` no-booleano).
    """

    model_config = {"strict": True}

    assistant_response: bool = True
    """Si ``True``, emite ``event_type="assistant_response"`` tras cada turno LLM en grupos."""

    user_input_voice: bool = False
    """Si ``True``, emite ``event_type="user_input_voice"`` tras transcribir un audio."""

    user_input_photo: bool = False
    """Si ``True``, emite ``event_type="user_input_photo"`` tras procesar una foto."""


class BroadcastConfig(_ConfigBaseModel):
    """
    Config del **transporte** de broadcast TCP entre instancias de Inaki.

    Esta clase modela SOLO la capa de red (topología + emisión de eventos). El
    **comportamiento del bot en grupos** (``behavior``, ``bot_username``,
    ``rate_limiter``, ``rate_limiter_window``) NO vive acá: vive en
    ``TelegramGroupsConfig`` (``channels.telegram.groups``), porque aplica a
    cualquier grupo — haya o no broadcast TCP activo. Mezclar ambos forzaba a
    levantar el transporte solo para configurar cómo responde el bot.

    El rol se declara con bloques nombrados: ``server`` (esta instancia escucha)
    XOR ``client`` (esta instancia se conecta). El rol y sus campos son la misma
    cosa — no existe un ``mode`` aparte que pueda desincronizarse del bloque.
    ``auth`` es el secreto HMAC compartido, único para ambos roles (el del
    client DEBE coincidir con el del server).

    Validaciones (solo con ``enabled=True``; apagado no se exige topología):
    - ``server`` y ``client`` son mutuamente excluyentes, y uno debe estar.
    - ``auth`` es obligatorio.
    - Los rangos de puerto (1024..65535) los validan los sub-modelos.
    """

    enabled: bool = True
    """Kill-switch del transporte. ``False`` = el bloque queda escrito pero no se
    levanta ningún adapter TCP (y no se exige topología ni auth)."""

    auth: str | None = Field(default=None, json_schema_extra={"secret": True})
    """Secreto HMAC-SHA256 compartido entre server y clients. Obligatorio cuando
    ``enabled=True`` (default)."""

    server: BroadcastServerConfig | None = None
    """Rol server: esta instancia escucha en ``server.port``. XOR con ``client``."""

    client: BroadcastClientConfig | None = None
    """Rol client: esta instancia se conecta a ``client.host:client.port``. XOR con ``server``."""

    emit: BroadcastEmitConfig = BroadcastEmitConfig()
    """Flags que controlan qué tipos de eventos se emiten al broadcast.
    Sin override usa los defaults: solo ``assistant_response`` activo."""

    @model_validator(mode="after")
    def _validar_topologia(self) -> "BroadcastConfig":
        """Valida server XOR client + auth obligatorio. Con ``enabled=False`` no
        se exige nada: el bloque puede quedar incompleto mientras está apagado."""
        if not self.enabled:
            return self

        tiene_server = self.server is not None
        tiene_client = self.client is not None

        if tiene_server and tiene_client:
            raise ValueError(
                "BroadcastConfig: 'server' y 'client' son mutuamente excluyentes — "
                "un nodo no puede ser servidor y cliente simultáneamente."
            )

        if not tiene_server and not tiene_client:
            raise ValueError(
                "BroadcastConfig: debe definirse el bloque 'server' (esta instancia "
                "escucha) o 'client' (esta instancia se conecta) — no pueden estar "
                "ambos ausentes. Para apagar el transporte sin borrar el bloque, "
                "usá 'enabled: false'."
            )

        if self.auth is None:
            raise ValueError("BroadcastConfig: 'auth' (secreto HMAC compartido) es obligatorio.")

        return self


class TelegramGroupsConfig(_ConfigBaseModel):
    """
    Config tipada del comportamiento del bot en chats grupales.

    Cubre dos cosas:
    - **Timing/reacciones** (``min_delay_response``, ``max_delay_response``,
      ``reactions``): opcionales, ``None`` = "heredar del padre" (``reactions``)
      o "usar default del módulo" (delays).
    - **Política de respuesta** (``behavior``, ``bot_username``, ``rate_limiter``,
      ``rate_limiter_window``): cómo decide el bot responder en un grupo. Antes
      vivían en ``BroadcastConfig``, lo que obligaba a levantar el transporte TCP
      solo para configurarlos. Ahora aplican a cualquier grupo, con o sin broadcast.
    """

    min_delay_response: float | None = None
    """Delay mínimo (segundos) antes de flushar el buffer de grupo al LLM. ``None`` → default del módulo."""

    max_delay_response: float | None = None
    """Delay máximo (segundos) antes de flushar el buffer. ``None`` → default del módulo."""

    reactions: bool | None = None
    """Override del flag ``channels.telegram.reactions`` para chats grupales. ``None`` → hereda del padre."""

    behavior: Literal["listen", "mention", "autonomous"] = "mention"
    """
    Modo de comportamiento en grupos:
    - ``listen`` → nunca invoca el LLM, solo escucha.
    - ``mention`` → invoca el LLM solo si el mensaje menciona al bot (requiere ``bot_username``).
    - ``autonomous`` → invoca el LLM ante cualquier mensaje (sujeto a rate limiter).
    """

    bot_username: str | None = None
    """Username del bot Telegram (sin ``@``) para detección de menciones en modo ``mention``."""

    rate_limiter: int = 5
    """Máximo de respuestas proactivas (modo ``autonomous``) por ventana por chat.

    El primer mensaje que SUPERA este límite (``counter > rate_limiter``) es bloqueado;
    es decir, exactamente ``rate_limiter`` mensajes pasan por ventana."""

    rate_limiter_window: int = 30
    """Duración de la ventana del rate limiter en segundos. Default 30s.

    Importante: el ciclo bot-to-bot toma típicamente 15-40s (delay de flush + LLM + red).
    Si la ventana es menor que el ciclo, el contador se resetea entre intercambios
    y el limiter es inefectivo — bots pueden hablar indefinidamente. Para grupos con
    ``behavior='autonomous'`` se recomienda 300s (5min) o más."""

    @model_validator(mode="after")
    def _validar_delays(self) -> "TelegramGroupsConfig":
        if (
            self.min_delay_response is not None
            and self.max_delay_response is not None
            and self.min_delay_response > self.max_delay_response
        ):
            raise ValueError(
                f"TelegramGroupsConfig: min_delay_response ({self.min_delay_response}) "
                f"no puede ser mayor que max_delay_response ({self.max_delay_response})."
            )
        if self.min_delay_response is not None and self.min_delay_response < 0:
            raise ValueError(
                f"TelegramGroupsConfig: min_delay_response debe ser >= 0, recibido: {self.min_delay_response}."
            )
        if self.max_delay_response is not None and self.max_delay_response < 0:
            raise ValueError(
                f"TelegramGroupsConfig: max_delay_response debe ser >= 0, recibido: {self.max_delay_response}."
            )
        return self


class TelegramChannelConfig(_ConfigBaseModel):
    """
    Config tipada del canal Telegram.

    Tuvo ``extra="allow"`` mientras el bloque no se validaba al cargar: sin
    validación, rechazar lo desconocido habría roto configs sin dar un
    diagnóstico útil. Desde que el canal se valida contra ``CHANNEL_SCHEMAS``,
    un campo que no está acá es un typo y se rechaza como en el resto del schema.
    """

    token: str = Field(default="", json_schema_extra={"secret": True})
    """Token del bot de Telegram (BotFather). Requerido para que el canal levante."""

    allowed_user_ids: list[int] = Field(default_factory=list)
    """IDs de usuarios autorizados en CHATS PRIVADOS. Lista vacía = sin restricción.
    NO aplica en grupos (los grupos se controlan solo por ``allowed_chat_ids``)."""

    allowed_chat_ids: list[int] = Field(default_factory=list)
    """IDs de grupos autorizados. Lista vacía = el bot NO responde en grupos (solo
    chats privados). En un grupo autorizado cualquier usuario puede interactuar:
    ``allowed_user_ids`` no se evalúa en grupos."""

    reactions: bool = False
    """Si True, el bot envía una reacción emoji tras procesar un mensaje."""

    voice_enabled: bool = True
    """Si True, el bot acepta mensajes de voz y los transcribe."""

    add_llm_timestamp: bool = False
    """Si True, ``RunAgentUseCase`` antepone ``[YYYY-MM-DD HH:MM:SS TZ] `` al
    ``content`` de cada mensaje USER/ASSISTANT (privados y grupos) antes de
    armar el prompt para el LLM. Default ``False`` para mantener
    compatibilidad. El timestamp sale del ``Message.timestamp`` ya persistido
    en la DB; no se duplica en el ``content`` almacenado."""

    broadcast: BroadcastConfig | None = None
    """Config del canal de broadcast entre instancias. None = broadcast inactivo."""

    groups: TelegramGroupsConfig | None = None
    """Config específica para chats grupales (delays, override de reactions). None = todos los defaults."""
