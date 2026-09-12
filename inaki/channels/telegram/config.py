"""Config del canal Telegram: sección del schema, migraciones y validaciones propias.

El módulo ``inaki.config`` NO conoce este canal: el composition root lo registra con
``registrar()`` (modelo + migraciones + validaciones), y el loader, la introspección
y el generador de docs lo descubren por el registro. Un canal = un paquete.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field, model_validator

from inaki.config.channels import registrar_canal
from inaki.config.schema._base import _ConfigBaseModel
from inaki.shared.errors import ConfigError

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from inaki.config.schema.root import AgentConfig


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
    """Intervenciones SEGUIDAS que el agente puede hacer en un chat sin que hable un humano.

    Cuenta lo que el agente EMITE, no lo que le llega: un turno que termina en
    ``__SKIP__`` no gasta presupuesto, y varios mensajes coalescidos en un mismo
    flush son UNA intervención. Al llegar a este número, el agente entra en
    cooldown por ``rate_limiter_window`` segundos. Un mensaje humano —nativo o
    ``user_input_*`` por broadcast— pone el contador en cero y levanta el
    cooldown al instante."""

    rate_limiter_window: int = 30
    """Duración del cooldown en segundos, contado desde la última intervención. Default 30s.

    No es una ventana de pared: el reloj lo arranca el agente al agotar sus
    ``rate_limiter`` intervenciones seguidas, no el primer mensaje que le llegue.
    Es el único re-armado que no necesita un humano, así que fija el caudal
    máximo de un intercambio bot-a-bot sin nadie presente: ``rate_limiter``
    respuestas por cada ``rate_limiter_window``. Para grupos con
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
    diagnóstico útil. Desde que el canal se valida contra el registro de canales,
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


# ---------------------------------------------------------------------------
# Acceso tipado desde un AgentConfig
# ---------------------------------------------------------------------------


def telegram_config(cfg: AgentConfig) -> TelegramChannelConfig | None:
    """Bloque ``channels.telegram`` tipado del agente, o ``None`` si no lo declara."""
    return cfg.canal("telegram", TelegramChannelConfig)


# ---------------------------------------------------------------------------
# Migraciones automáticas del bloque (corren en ``ensure_user_config``)
# ---------------------------------------------------------------------------

# Campos de *comportamiento en grupos* que migraron de ``channels.telegram.broadcast``
# a ``channels.telegram.groups``. El transporte TCP (port/remote/auth/emit) NO se toca.
_GROUP_BEHAVIOR_FIELDS = ("behavior", "bot_username", "rate_limiter", "rate_limiter_window")


def migrate_telegram_group_fields(config_dir: Path, agents_dir: Path) -> None:
    """Migración one-shot: mueve ``behavior``/``bot_username``/``rate_limiter``/
    ``rate_limiter_window`` de ``channels.telegram.broadcast`` a
    ``channels.telegram.groups``.

    Esos campos describen *cómo responde el bot en un grupo* (aplica con o sin
    broadcast TCP), pero vivían en ``BroadcastConfig``, lo que obligaba a levantar
    el transporte solo para configurarlos. Esta función reubica instalaciones previas.

    Procesa ``global.yaml``, ``global.secrets.yaml`` (si sobrevive, corre antes
    del fold) y todos los YAML de ``agents_dir`` y su ``sub-agents/`` — cada
    campo puede vivir en cualquier capa.

    ``agents_dir`` llega como parámetro porque el layout REAL lo tiene como
    sibling de ``config/`` (``~/.inaki/agents/``), no como subcarpeta. La
    versión original lo derivaba como ``config_dir / "agents"`` — el layout de
    los tests — así que en instalaciones reales los ficheros de agente NUNCA se
    migraban. Con los campos viejos ignorándose en silencio nadie lo notó;
    desde que la config falla ruidoso (`config-falla-ruidoso`), un agente sin
    migrar aborta el arranque, y este bug pasó de invisible a fatal.
    Idempotente: si ``broadcast`` no tiene ninguno de los campos, no toca el archivo.
    ``groups`` gana ante conflicto (campo presente en ambos → se descarta el de
    ``broadcast``). Si ``broadcast`` queda vacío tras mover (solo tenía comportamiento,
    sin transporte) se elimina el bloque. Preserva comentarios (ruamel).
    """
    from ruamel.yaml import YAML

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True

    archivos = [config_dir / "global.yaml", config_dir / "global.secrets.yaml"]
    for directorio in (agents_dir, agents_dir / "sub-agents"):
        if directorio.is_dir():
            archivos.extend(sorted(directorio.glob("*.yaml")))

    for path in archivos:
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                doc = yaml_rt.load(f)
        except OSError as exc:
            logger.error("Migración groups: no se pudo leer %s (%s)", path, exc)
            continue
        if not isinstance(doc, dict) or not _move_group_fields_broadcast_to_groups(doc):
            continue
        try:
            with path.open("w", encoding="utf-8") as f:
                yaml_rt.dump(doc, f)
        except OSError as exc:
            logger.error("Migración groups: no se pudo escribir %s (%s)", path, exc)
            continue
        logger.info("Migración groups: comportamiento movido broadcast→groups en %s", path)


def _move_group_fields_broadcast_to_groups(doc: dict) -> bool:
    """Mueve los campos de comportamiento de ``telegram.broadcast`` a
    ``telegram.groups`` dentro de un doc ruamel ya cargado. Devuelve ``True`` si
    hubo cambios (in-place sobre ``doc``)."""
    channels = doc.get("channels")
    if not isinstance(channels, dict):
        return False
    telegram = channels.get("telegram")
    if not isinstance(telegram, dict):
        return False
    broadcast = telegram.get("broadcast")
    if not isinstance(broadcast, dict):
        return False

    presentes = [campo for campo in _GROUP_BEHAVIOR_FIELDS if campo in broadcast]
    if not presentes:
        return False

    groups = telegram.get("groups")
    if not isinstance(groups, dict):
        groups = {}
        telegram["groups"] = groups

    for campo in presentes:
        valor = broadcast.pop(campo)
        # groups gana ante conflicto: solo escribimos si no estaba ya definido ahí.
        if campo not in groups:
            groups[campo] = valor

    # Un broadcast sin transporte (port/remote) ya no es broadcast: lo eliminamos
    # para no disparar el validador port-XOR-remote con un bloque vacío.
    if not broadcast:
        del telegram["broadcast"]

    return True


# Único path donde el wiring LEE el bloque de broadcast. Cualquier otro lugar
# donde el operador lo escriba se descarta sin efecto.
_BROADCAST_PATH_VALIDO = "channels.telegram.broadcast"


def avisar_broadcast_extraviado(agent_id: str, merged: dict) -> None:
    """Avisa si hay un bloque ``broadcast:`` en un nivel del YAML que nadie lee.

    ``_wire_broadcast_for_agent`` solo mira ``channels.telegram.broadcast``.
    Caso real: un `broadcast:` fuera de `channels.telegram` con la topología
    vieja (`port:` suelto). Ni el error de validación llegó a emitirse, porque
    el bloque nunca alcanzó el parser.

    Quedan dos ubicaciones equivocadas, y desde que ``AgentConfig`` valida los
    canales ya no se tratan igual:

    - **Raíz del agente**: ``assemble_agent_config`` solo copia ``channels``, así
      que el bloque se descarta sin que nada lo mire. Este warning es la ÚNICA
      señal — sigue siendo imprescindible.
    - **``channels.broadcast``**: ahora es un canal desconocido y la validación
      lo rechaza con su path. El warning corre antes y agrega lo que el error no
      sabe: cuál es el path válido.
    """
    extraviados = []
    if isinstance(merged.get("broadcast"), dict):
        extraviados.append("broadcast (raíz del agente)")
    canales = merged.get("channels")
    if isinstance(canales, dict) and isinstance(canales.get("broadcast"), dict):
        extraviados.append("channels.broadcast")

    if extraviados:
        logger.warning(
            "Agente '%s': bloque de broadcast en un nivel que NADIE lee (%s). El único "
            "path válido es '%s' — tal como está, el transporte no se levanta y el "
            "puerto queda cerrado.",
            agent_id,
            ", ".join(extraviados),
            _BROADCAST_PATH_VALIDO,
        )


# ---------------------------------------------------------------------------
# Validación cruzada entre agentes (corre al construir el AgentRegistry)
# ---------------------------------------------------------------------------


def validar_unicidad(agents: dict[str, AgentConfig]) -> None:
    """
    Rechaza configs donde varios agentes comparten la misma identidad de canal,
    o donde un mismo agente tiene dos canales con el mismo ``broadcast.server.port``.

    Motivo: un bot de Telegram solo admite UN ``getUpdates`` activo por token
    (Telegram API). Si dos agentes declaran el mismo token, el daemon levanta
    pollings que se pisan → errores ``Conflict`` en loop.

    El modelo canónico: un solo agente expone el canal (entry point) y delega
    a los subagentes vía la tool ``delegate``. Los subagentes NO deben
    declarar ``channels.telegram`` apuntando al mismo token que el principal.

    Broadcast port uniqueness: dentro de un mismo agente, dos canales no pueden
    declarar el mismo ``broadcast.server.port`` — ambos intentarían hacer
    ``bind()`` en el mismo puerto del host.
    """
    telegram_tokens: dict[str, list[str]] = {}

    for agent_id, cfg in agents.items():
        tg_cfg = telegram_config(cfg)
        if tg_cfg is not None and tg_cfg.token:
            telegram_tokens.setdefault(tg_cfg.token, []).append(agent_id)

        # Unicidad de broadcast.server.port dentro del mismo agente. Solo los
        # servers hacen bind(); un bloque con enabled=false no levanta transporte.
        broadcast_ports: dict[int, list[str]] = {}
        for channel_name, channel_cfg in cfg.channels.items():
            bc = getattr(channel_cfg, "broadcast", None)
            if bc is None or bc.enabled is False or bc.server is None:
                continue
            broadcast_ports.setdefault(bc.server.port, []).append(channel_name)

        duplicated_bc_ports = {p: chs for p, chs in broadcast_ports.items() if len(chs) > 1}
        if duplicated_bc_ports:
            conflicts = "; ".join(
                f"port {p} declarado en [{', '.join(chs)}]"
                for p, chs in duplicated_bc_ports.items()
            )
            raise ConfigError(
                f"Agente '{agent_id}': broadcast.server.port duplicado — {conflicts}. "
                "Cada canal del agente debe usar un puerto de broadcast distinto."
            )

    duplicated_tokens = {tok: ids for tok, ids in telegram_tokens.items() if len(ids) > 1}

    if duplicated_tokens:
        agent_lists = "; ".join(f"agentes [{', '.join(ids)}]" for ids in duplicated_tokens.values())
        raise ConfigError(
            f"Token de Telegram duplicado entre {agent_lists}. "
            "Un token solo admite un polling activo: dejá 'channels.telegram' únicamente "
            "en el agente que actúa como entry point; los subagentes reciben mensajes "
            "vía la tool 'delegate'."
        )


# ---------------------------------------------------------------------------
# Registro del canal
# ---------------------------------------------------------------------------


def registrar() -> None:
    """Registra la sección ``channels.telegram`` con sus migraciones y validaciones."""
    registrar_canal(
        "telegram",
        TelegramChannelConfig,
        migraciones=(migrate_telegram_group_fields,),
        validar_raw=avisar_broadcast_extraviado,
        validar_agentes=validar_unicidad,
    )
