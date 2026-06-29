"""Settings per-use-case — el vocabulario de configuración que el dominio posee.

Cada use case de ``core/`` declara acá EXACTAMENTE los parámetros que consume,
en lugar de recibir el ``AgentConfig`` completo de ``infrastructure/``. La
dirección de dependencia queda legal: ``infrastructure/container.py`` es el
ÚNICO lugar que mapea config (YAML mergeado) → estos VOs.

Beneficio colateral: los tests unitarios construyen el VO con solo los campos
que les importan — sin armar un ``AgentConfig`` completo con providers,
embedding y credenciales irrelevantes para pasar la validación.

Los defaults espejan los de ``infrastructure/config.py`` (fuente user-facing).
En runtime no hay riesgo de drift: el container siempre pasa valores explícitos
leídos de la config — los defaults de acá solo alivianan la construcción en tests.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

# Fallback del sistema para ``keep_last_messages`` cuando la config trae el
# sentinel 0. Vivía en infrastructure/config.py; se muda con la lógica que
# lo consume (``MemorySettings.resolved_keep_last_messages``).
KEEP_LAST_MESSAGES_FALLBACK = 84

_DIGEST_SCOPE_SANITIZER = re.compile(r"[^a-zA-Z0-9_-]")


def sanitize_digest_scope(value: str | None) -> str:
    """
    Normaliza un valor de ``channel`` o ``chat_id`` para usarlo como segmento
    de un nombre de archivo del digest.

    - ``None`` o cadena vacía → ``"default"``.
    - Cualquier carácter fuera de ``[a-zA-Z0-9_-]`` se reemplaza por ``_``
      (cubre IDs negativos de Telegram, dos puntos, espacios, etc.).
    """
    if not value:
        return "default"
    sanitized = _DIGEST_SCOPE_SANITIZER.sub("_", value)
    return sanitized or "default"


class ConsolidationSettings(BaseModel, frozen=True):
    """Parámetros que ``ConsolidateMemoryUseCase`` consume del job de consolidación."""

    min_relevance_score: float = 0.5
    keep_last_messages: int = 0
    channels_infused: tuple[str, ...] | None = None

    def resolved_keep_last_messages(self) -> int:
        """
        Devuelve cuántos mensajes preservar por agente tras la consolidación.
        0 (default) es un sentinel que significa 'usar el fallback del sistema'.
        Cualquier valor > 0 se respeta tal cual.
        """
        if self.keep_last_messages <= 0:
            return KEEP_LAST_MESSAGES_FALLBACK
        return self.keep_last_messages


class ReconciliationSettings(BaseModel, frozen=True):
    """Parámetros que ``ReconcileMemoryUseCase`` consume del job de reconciliación."""

    similarity_threshold: float = 0.80
    top_k: int = 10


class MemorySettings(BaseModel, frozen=True):
    """Parámetros de memoria que consumen ``ConsolidateMemoryUseCase``,
    ``ReconcileMemoryUseCase`` y ``RunAgentUseCase``.

    Estructura espejo del YAML ``memories``: campos de digest COMPARTIDOS en la
    raíz, y dos sub-VOs hermanos (``consolidation`` / ``reconciliation``) con los
    parámetros propios de cada job.

    ``digest_template`` llega ya resuelto a ruta absoluta por el container
    (la expansión de ``~/.inaki/`` es responsabilidad del loader de config).
    Admite los placeholders ``{channel}`` y ``{chat_id}``.
    """

    digest_template: str = "mem/digest_{channel}_{chat_id}.md"
    digest_size: int = 14
    consolidation: ConsolidationSettings = ConsolidationSettings()
    reconciliation: ReconciliationSettings = ReconciliationSettings()

    def resolved_digest_path(self, channel: str | None, chat_id: str | None) -> Path:
        """
        Devuelve la ruta del digest markdown para el scope ``(channel, chat_id)``.

        Aplica ``sanitize_digest_scope`` a ambos componentes y formatea el
        template. Si el template no contiene los placeholders ``{channel}`` y
        ``{chat_id}`` (config legacy), se devuelve la misma ruta para todos los
        scopes — comportamiento de compatibilidad temporal.
        """
        ch = sanitize_digest_scope(channel)
        cid = sanitize_digest_scope(chat_id)
        formatted = self.digest_template.format(channel=ch, chat_id=cid)
        return Path(formatted)


class RunAgentSettings(BaseModel, frozen=True):
    """Parámetros que ``RunAgentUseCase`` consume — y nada más.

    ``workspace_root`` y ``users_dir`` llegan pre-resueltos a ruta absoluta por el
    container (``users_dir`` = ``<home>/users``, reanclado por ``--home``/``INAKI_HOME``).
    ``timestamp_channels`` generaliza el flag ``channels.telegram.add_llm_timestamp``:
    el use case antepone timestamps cuando el canal del turno está en el set.
    """

    agent_id: str
    name: str = ""
    description: str = ""
    system_prompt: str = ""
    workspace_root: str = ""
    users_dir: str = ""
    # Base para resolver ``@include(<archivo>)`` relativos del prompt: el home de
    # instancia (``~/.inaki/``), reanclado por ``--home``/``INAKI_HOME``. Llega
    # pre-resuelto por el container. Vacío → los includes relativos resuelven contra cwd.
    include_base_dir: str = ""
    merge_chats: bool = False
    min_words_threshold: int = 0
    skills_min_skills: int = 10
    skills_top_k: int = 3
    skills_min_score: float = 0.0
    skills_sticky_ttl: int = 3
    tools_min_tools: int = 10
    tools_top_k: int = 5
    tools_min_score: float = 0.0
    tools_sticky_ttl: int = 3
    tool_call_max_iterations: int = 5
    circuit_breaker_threshold: int = 2
    request_delay_seconds: float = 2.0
    timestamp_channels: frozenset[str] = frozenset()
    memory: MemorySettings = MemorySettings()


class OneShotSettings(BaseModel, frozen=True):
    """Parámetros que ``RunAgentOneShotUseCase`` consume."""

    agent_id: str
    system_prompt: str = ""
    circuit_breaker_threshold: int = 2
    request_delay_seconds: float = 2.0
    allowed_tools: frozenset[str] | None = None
    """Allow-list de nombres de tools. ``None`` = sin restricción (toolkit completo, menos
    ``delegate``). Si trae nombres, el OneShot expone SOLO esas (intersección con el registry
    recibido). La pobla el builder efímero del flujo delegate desde ``tools.allowed`` del
    sub-agente; un nombre que no exista en el registry se ignora."""


class PhotosSettings(BaseModel, frozen=True):
    """Parámetros que ``ProcessPhotoUseCase`` consume.

    Aplana ``photos.faces.*`` de la config: el use case solo necesita los dos
    umbrales, no el sub-modelo completo (provider/model son del adapter de visión).
    """

    enabled: bool = True
    debug: bool = False
    enrollment_chats: str = "private"
    match_threshold: float = 0.55
    ambiguous_threshold: float = 0.40
