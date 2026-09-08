"""Bloque ``channels`` global, fallback de canal y canal ``cli``.

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

from inaki.config.schema._base import _ConfigBaseModel


class ChannelsGlobalConfig(_ConfigBaseModel):
    """Flags transversales de presentación al usuario en cualquier canal.

    Se configura SOLO a nivel global (``global.yaml`` → ``channels:``). No hay
    override per-agent: ``AgentConfig.channels`` (dict de adapters telegram/cli/…)
    es una estructura distinta y mantiene su rol. Si el usuario pone estos
    flags en ``agents/{id}.yaml`` por error, el merge los filtra en
    ``load_agent_config`` para no contaminar el dict de adapters.
    """

    thinking_indicator: bool = False
    """Mostrar "Thinking..." en el canal cuando el modelo está razonando.

    Solo aplica si el provider activa thinking mode (``reasoning_effort``).
    ``False`` (default) → el bot permanece silencioso durante el razonamiento.
    """


class ChannelFallbackConfig(_ConfigBaseModel):
    """Config de fallbacks para el routing de canales del scheduler.

    Cuando una task dispara un envío a un canal que no tiene sink nativo
    (p. ej. ``cli``, ``rest``, ``daemon``), el ``ChannelRouter`` resuelve
    el destino efectivo aplicando esta cascada:

      1. Sink nativo registrado para el prefix del target.
      2. Entry en ``overrides`` para el ``channel_type`` del target.
      3. ``default`` global (si está configurado).
      4. Fallback hardcoded: ``file://~/.inaki/data/scheduler-fallback.log``.
    """

    default: str | None = None
    """Target al que van los canales sin sink nativo ni override. ``null`` → log de fallback.

    Es un target string con prefijo: ``"telegram:12345"``, ``"file:///var/log/x.log"``
    o ``"null:"`` para descartar. Se aplica DESPUÉS de ``overrides``, así que
    funciona como red general; con ``null`` el envío cae al
    ``scheduler.fallback_log_filename``."""

    overrides: dict[str, str] = {}
    """Redirecciones ``channel_type → target string``, evaluadas antes que ``default``.

    Solo se consultan para prefijos SIN sink nativo registrado — un target de un
    canal vivo nunca se redirige. Ejemplo: ``{"cli": "telegram:123"}`` manda a ese
    chat las salidas de tareas que nacieron en la CLI, que si no nadie leería."""


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
