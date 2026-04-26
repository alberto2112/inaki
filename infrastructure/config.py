"""
Sistema de configuración de Iñaki.

Layout por defecto en el home del usuario:
  ~/.inaki/config/global.yaml          — config base del sistema
  ~/.inaki/config/global.secrets.yaml  — secrets globales (api keys compartidas)
  ~/.inaki/agents/{id}.yaml            — config y canales del agente
  ~/.inaki/agents/{id}.secrets.yaml    — secrets del agente (opcional)

El primer arranque crea los archivos faltantes vía `ensure_user_config()`.
Se puede override con `--config DIR` (usa el layout legacy `DIR/agents/`).

4 capas de merge en orden (cada capa sobreescribe solo los campos que define):
  1. global.yaml → 2. global.secrets.yaml → 3. agents/{id}.yaml → 4. agents/{id}.secrets.yaml

Regla de secrets: si el agente no define un secret, hereda del global.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


def _expand_user_str(v: Any) -> Any:
    """Expand `~` in a string path. Non-strings pass through untouched."""
    if isinstance(v, str):
        return str(Path(v).expanduser())
    return v


def _expand_user_list(v: Any) -> Any:
    """Expand `~` in every string element of a list. Non-lists pass through."""
    if isinstance(v, list):
        return [str(Path(x).expanduser()) if isinstance(x, str) else x for x in v]
    return v


ExpandedPath = Annotated[str, BeforeValidator(_expand_user_str)]
ExpandedPathList = Annotated[list[str], BeforeValidator(_expand_user_list)]


# Raíz hardcoded para datos de runtime del usuario (DBs, models, digest markdown).
# Convive con `~/.inaki/config/` y `~/.inaki/agents/` (bootstrap del sistema).
_INAKI_HOME = Path.home() / ".inaki"

# Valores SQLite especiales que NO deben interpretarse como paths.
_SQLITE_SPECIAL = {":memory:"}


def _resolve_runtime_path(v: Any) -> Any:
    """
    Resuelve un path de runtime contra `~/.inaki/`.

    - Valores no-str pasan sin tocar (ya vienen normalizados).
    - Valores especiales de SQLite (`:memory:`) pasan tal cual.
    - Paths absolutos (incluyendo `~/...` tras expansión) se usan tal cual.
    - Paths relativos se anclan bajo `_INAKI_HOME`.
    """
    if not isinstance(v, str):
        return v
    if v in _SQLITE_SPECIAL:
        return v
    p = Path(v).expanduser()
    if p.is_absolute():
        return str(p)
    return str(_INAKI_HOME / p)


RuntimePath = Annotated[str, BeforeValidator(_resolve_runtime_path)]


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------


class AppConfig(BaseModel):
    name: str = "Iñaki"
    log_level: str = "INFO"
    ext_dirs: ExpandedPathList = ["ext", "~/.inaki/ext"]
    default_agent: str = "general"


class ProviderConfig(BaseModel):
    """
    Entrada del registry top-level de proveedores.

    Cada entrada representa UN vendor (groq, openai, openrouter, ollama, etc.)
    con sus credenciales y endpoint. Las features (`llm`, `embedding`,
    `transcription`, `memory.llm`) referencian entradas por nombre vía su
    campo ``provider: <key>``, eliminando duplicación de ``api_key``/``base_url``.

    Campos:
      - ``type``: nombre del adapter. Si es ``None``, se resuelve a la key del
        dict (``providers.groq`` → ``type == "groq"``). Solo se explicita cuando
        se quieren múltiples entradas del mismo adapter con creds distintas
        (p. ej. ``providers.groq-work: {type: groq, api_key: K2}``).
      - ``api_key``: credencial. Opcional para providers locales que no la
        requieren (ollama, e5_onnx).
      - ``base_url``: override del default del adapter. Opcional.

    ``extra="forbid"`` atrapa typos temprano (``api_ky``).
    """

    model_config = ConfigDict(extra="forbid")

    type: str | None = None
    api_key: str | None = None
    base_url: str | None = None


class LLMConfig(BaseModel):
    provider: str = "openrouter"
    model: str = "anthropic/claude-3-5-haiku"
    temperature: float = 0.7
    max_tokens: int = 2048
    reasoning_effort: str | None = None


class EmbeddingConfig(BaseModel):
    model_config = ConfigDict(validate_default=True)  # RuntimePath en los defaults

    provider: str = "e5_onnx"
    model_dirname: RuntimePath = "models/e5-small"  # solo e5_onnx — relativo a ~/.inaki/
    model: str = "text-embedding-3-small"  # solo openai
    dimension: int = 384
    cache_filename: RuntimePath = "data/embedding_cache.db"  # relativo a ~/.inaki/


class TranscriptionConfig(BaseModel):
    """Config del provider de transcripción de audio (opcional)."""

    provider: str = "groq"
    model: str = "whisper-large-v3-turbo"
    language: str | None = None  # None → auto-detect por el modelo
    timeout_seconds: int = 60  # segundos para el request HTTP
    max_audio_mb: int = 25  # límite de tamaño de audio (MB) — Groq Whisper: 25


# ---------------------------------------------------------------------------
# ResolvedXConfig — valor compuesto (feature + provider) que recibe el adapter
# ---------------------------------------------------------------------------


class ResolvedLLMConfig(BaseModel):
    """LLMConfig + credenciales del registry resueltas. Lo recibe el adapter."""

    provider: str
    model: str
    temperature: float
    max_tokens: int
    reasoning_effort: str | None = None
    api_key: str | None = None
    base_url: str | None = None


class ResolvedEmbeddingConfig(BaseModel):
    """EmbeddingConfig + credenciales resueltas del registry."""

    provider: str
    model_dirname: str
    model: str
    dimension: int
    cache_filename: str
    api_key: str | None = None
    base_url: str | None = None


class ResolvedTranscriptionConfig(BaseModel):
    """TranscriptionConfig + credenciales resueltas del registry."""

    provider: str
    model: str
    language: str | None = None
    timeout_seconds: int = 60
    max_audio_mb: int = 25
    api_key: str | None = None
    base_url: str | None = None


_KEEP_LAST_MESSAGES_FALLBACK = 84


class MemoryLLMOverride(BaseModel):
    """
    Override parcial de ``LLMConfig`` para el LLM de consolidación de memoria.

    Todos los campos son opcionales. Solo los campos EXPLÍCITAMENTE presentes
    en el YAML pisan al ``llm.*`` del agente; los ausentes se heredan.

    Semántica ``null`` vs ausente (relevante para distinguir override de herencia):
      - Clave ausente en YAML → no está en ``model_fields_set`` → hereda del base.
      - Clave presente con valor ``null`` → está en ``model_fields_set`` con valor
        ``None`` → pisa al base con ``None`` (útil para, p. ej., apagar
        ``reasoning_effort`` en consolidación sin tocar el LLM del agente).

    Las credenciales NO viven acá — si el override cambia ``provider``, las creds
    se resuelven automáticamente desde el registry ``providers`` del nivel
    superior. Ver ``MemoryConfig.resolved_llm_config``.
    """

    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None


class MemoryConfig(BaseModel):
    model_config = ConfigDict(validate_default=True)  # RuntimePath en los defaults

    db_filename: RuntimePath = "data/inaki.db"  # relativo a ~/.inaki/
    default_top_k: int = 5
    digest_size: int = 14
    digest_filename: RuntimePath = "mem/last_memories.md"  # relativo a ~/.inaki/
    min_relevance_score: float = 0.5
    schedule: str = "0 3 * * *"
    delay_seconds: int = 2
    keep_last_messages: int = 0
    enabled: bool = True
    channels_infused: list[str] | None = None
    """
    Canales cuyo historial se incluye en la consolidación de memoria.

    ``None`` o lista vacía → se procesan mensajes de todos los canales.
    Si se especifica, solo se consolidan mensajes donde ``channel`` está en la lista.
    Ejemplo: ``["telegram"]`` — no consolida mensajes de CLI ni daemon.
    """
    llm: MemoryLLMOverride | None = None
    """
    Override opcional del LLM usado SOLO para la consolidación de memoria.
    Si es ``None``, consolidación reusa el LLM del agente.
    """

    def resolved_keep_last_messages(self) -> int:
        """
        Devuelve cuántos mensajes preservar por agente tras la consolidación.
        0 (default) es un sentinel que significa 'usar el fallback del sistema'
        ({fallback}). Cualquier valor > 0 se respeta tal cual.
        """.format(fallback=_KEEP_LAST_MESSAGES_FALLBACK)
        if self.keep_last_messages <= 0:
            return _KEEP_LAST_MESSAGES_FALLBACK
        return self.keep_last_messages

    def merged_llm_config(self, base: LLMConfig) -> LLMConfig:
        """
        Devuelve la ``LLMConfig`` efectiva (sin creds) tras aplicar el override.

        Merge field-by-field: los campos que el usuario seteó EXPLÍCITAMENTE
        en ``memory.llm.*`` (incluso ``null``) pisan al ``base``; el resto hereda.
        Si no hay override, devuelve el ``base`` tal cual.

        Las credenciales se resuelven aparte contra el registry ``providers``
        — ver ``resolved_llm_config``.
        """
        if self.llm is None:
            return base

        fields_set = self.llm.model_fields_set
        overrides = {f: getattr(self.llm, f) for f in fields_set}
        return base.model_copy(update=overrides)

    def resolved_llm_config(
        self,
        base: LLMConfig,
        providers: "dict[str, ProviderConfig]",
    ) -> ResolvedLLMConfig:
        """
        Resuelve la ``ResolvedLLMConfig`` efectiva para la consolidación de memoria.

        1. Mergea el override (``memory.llm.*``) sobre ``base`` (``llm`` del agente).
        2. Resuelve credenciales desde el registry ``providers`` según el
           ``provider`` efectivo tras el merge.

        El check de ``REQUIRES_CREDENTIALS`` (fail-fast si el provider exige
        creds y no hay entrada en el registry) queda delegado a la factory
        — ver ``LLMProviderFactory.create_from_resolved``.
        """
        merged = self.merged_llm_config(base)
        provider_cfg = providers.get(merged.provider, ProviderConfig())
        return ResolvedLLMConfig(
            provider=merged.provider,
            model=merged.model,
            temperature=merged.temperature,
            max_tokens=merged.max_tokens,
            reasoning_effort=merged.reasoning_effort,
            api_key=provider_cfg.api_key,
            base_url=provider_cfg.base_url,
        )


class ChatHistoryConfig(BaseModel):
    model_config = ConfigDict(validate_default=True)  # RuntimePath en los defaults

    db_filename: RuntimePath = "data/history.db"  # relativo a ~/.inaki/
    max_messages: int = 0  # 0 = sin límite; N = últimos N mensajes al LLM
    merge_chats: bool = False  # False = aislar historial por (channel, chat_id);
                               # True = compartir todo el historial del agente entre canales/chats


class ChannelFallbackConfig(BaseModel):
    """Config de fallbacks para el routing de canales del scheduler.

    Cuando una task dispara un envío a un canal que no tiene sink nativo
    (p. ej. ``cli``, ``rest``, ``daemon``), el ``ChannelRouter`` resuelve
    el destino efectivo aplicando esta cascada:

      1. Sink nativo registrado para el prefix del target.
      2. Entry en ``overrides`` para el ``channel_type`` del target.
      3. ``default`` global (si está configurado).
      4. Fallback hardcoded: ``file:///tmp/inaki-schedule-output.log``.

    Atributos:
        default: Target string (p. ej. ``"file:///var/log/x.log"``,
            ``"telegram:12345"``, ``"null:"``) usado cuando no hay override
            específico. ``None`` delega al fallback hardcoded.
        overrides: Mapa ``channel_type → target string`` para redirigir
            canales concretos. Ejemplo: ``{"cli": "telegram:123"}`` envía
            los mensajes que nacieron desde CLI hacia ese chat de Telegram.
    """

    default: str | None = None
    overrides: dict[str, str] = {}


class SchedulerConfig(BaseModel):
    model_config = ConfigDict(validate_default=True)  # RuntimePath en los defaults

    enabled: bool = True
    db_filename: RuntimePath = "data/scheduler.db"  # relativo a ~/.inaki/
    max_retries: int = 3
    output_truncation_size: int = 65536
    channel_fallback: ChannelFallbackConfig = ChannelFallbackConfig()


class SkillsConfig(BaseModel):
    semantic_routing_min_skills: int = 10
    semantic_routing_top_k: int = 3
    semantic_routing_min_score: float = 0.0
    sticky_ttl: int = 3  # Turnos que una skill seleccionada sobrevive; 0 = disabled


class ToolsConfig(BaseModel):
    semantic_routing_min_tools: int = 10
    semantic_routing_top_k: int = 5
    semantic_routing_min_score: float = 0.0
    tool_call_max_iterations: int = 5
    circuit_breaker_threshold: int = 2
    sticky_ttl: int = 3  # Turnos que una tool seleccionada sobrevive; 0 = disabled


class SemanticRoutingConfig(BaseModel):
    """Políticas transversales al pipeline de semantic routing (skills + tools).

    ``min_words_threshold``: si el user_input tiene MENOS palabras que este
    umbral Y existe una selección sticky previa (skills o tools), el turno
    saltea el cálculo del embedding y hereda la selección del turno anterior
    intacta (no decrementa TTL, no persiste estado). ``0`` desactiva la
    feature y mantiene el comportamiento histórico (routing corre siempre).
    """

    min_words_threshold: int = 0


ContainmentMode = Literal["strict", "warn", "off"]


class WorkspaceConfig(BaseModel):
    """
    Workspace sobre el que operan las tools de filesystem.

    `path` — directorio raíz donde se resuelven los paths relativos.
    `containment` — guard de contención para paths absolutos y escapes via `..`:
      - "strict"  → bloquea cualquier path fuera del workspace (recomendado en prod)
      - "warn"    → loggea warning pero permite el acceso
      - "off"     → sin check (útil en desarrollo)
    """

    path: ExpandedPath = "~/inaki-workspace"
    containment: ContainmentMode = "strict"

    def model_post_init(self, __context: object) -> None:
        # Expand ~ in the default value (BeforeValidator no corre en defaults de clase).
        object.__setattr__(self, "path", str(Path(self.path).expanduser()))


class RemoteBroadcastConfig(BaseModel):
    """Config de conexión al servidor broadcast remoto (modo client)."""

    host: str
    """Dirección del servidor en formato ``ip:port`` (ej: ``"192.168.1.10:9000"``)."""

    auth: str
    """Secreto compartido con el servidor para autenticación HMAC-SHA256."""


class BroadcastConfig(BaseModel):
    """
    Config del canal de broadcast TCP entre instancias de Iñaki.

    Un nodo opera como **servidor** si declara ``port`` (sin ``remote``).
    Un nodo opera como **cliente** si declara ``remote`` (sin ``port``).
    Ambos ausentes → broadcast inactivo para ese canal.

    Validaciones:
    - ``port`` y ``remote`` son mutuamente excluyentes (``port XOR remote``).
    - Si ``port`` está seteado → ``auth`` es obligatorio.
    - ``port`` debe estar en el rango 1024..65535.
    """

    port: int | None = None
    """Puerto TCP en el que escucha el servidor. ``None`` → modo cliente."""

    remote: RemoteBroadcastConfig | None = None
    """Config del servidor remoto al que conectar como cliente. ``None`` → modo servidor."""

    behavior: Literal["listen", "mention", "autonomous"] = "mention"
    """
    Modo de comportamiento en grupos:
    - ``listen`` → nunca invoca el LLM, solo escucha.
    - ``mention`` → invoca el LLM solo si el mensaje menciona al bot.
    - ``autonomous`` → invoca el LLM ante cualquier mensaje (sujeto a rate limiter).
    """

    rate_limiter: int = 5
    """Máximo de respuestas proactivas (modo ``autonomous``) por ventana de 30s por chat."""

    auth: str | None = None
    """Secreto HMAC-SHA256 del servidor. Obligatorio cuando ``port`` está seteado."""

    bot_username: str | None = None
    """Username del bot Telegram (sin ``@``) para detección de menciones en modo ``mention``."""

    @model_validator(mode="after")
    def _validar_topologia(self) -> "BroadcastConfig":
        """Valida que el nodo sea server XOR client, y que server tenga auth."""
        tiene_port = self.port is not None
        tiene_remote = self.remote is not None

        if tiene_port and tiene_remote:
            raise ValueError(
                "BroadcastConfig: 'port' y 'remote' son mutuamente excluyentes — "
                "un nodo no puede ser servidor y cliente simultáneamente."
            )

        if not tiene_port and not tiene_remote:
            raise ValueError(
                "BroadcastConfig: debe definirse 'port' (modo servidor) o "
                "'remote' (modo cliente) — no pueden estar ambos ausentes."
            )

        if tiene_port:
            if self.auth is None:
                raise ValueError(
                    "BroadcastConfig: 'auth' es obligatorio cuando 'port' está definido."
                )
            if not (1024 <= self.port <= 65535):  # type: ignore[operator]
                raise ValueError(
                    f"BroadcastConfig: 'port' debe estar en el rango 1024..65535, "
                    f"recibido: {self.port}."
                )

        return self


class TelegramGroupsConfig(BaseModel):
    """
    Config tipada del comportamiento del bot en chats grupales.

    Todos los campos son opcionales y se resuelven contra defaults definidos en
    el adaptador. Ausencia explícita (``None``) significa "heredar del nivel
    padre" cuando aplica (caso ``reactions``) o "usar default del módulo".
    """

    model_config = ConfigDict(extra="allow")

    min_delay_response: float | None = None
    """Delay mínimo (segundos) antes de flushar el buffer de grupo al LLM. ``None`` → default del módulo."""

    max_delay_response: float | None = None
    """Delay máximo (segundos) antes de flushar el buffer. ``None`` → default del módulo."""

    reactions: bool | None = None
    """Override del flag ``channels.telegram.reactions`` para chats grupales. ``None`` → hereda del padre."""

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


class TelegramChannelConfig(BaseModel):
    """
    Config tipada del canal Telegram.

    Soporta ``extra="allow"`` para no romper campos desconocidos que puedan
    existir en configs de usuario hasta que sean adoptados formalmente.
    """

    model_config = ConfigDict(extra="allow")

    token: str = ""
    """Token del bot de Telegram (BotFather). Requerido para que el canal levante."""

    allowed_user_ids: list[int] = Field(default_factory=list)
    """IDs de usuarios autorizados a interactuar directamente. Lista vacía = sin restricción."""

    allowed_chat_ids: list[int] = Field(default_factory=list)
    """IDs de grupos autorizados. Lista vacía = solo chats privados de usuarios en allowed_user_ids."""

    reactions: bool = False
    """Si True, el bot envía una reacción emoji tras procesar un mensaje."""

    voice_enabled: bool = True
    """Si True, el bot acepta mensajes de voz y los transcribe."""

    broadcast: BroadcastConfig | None = None
    """Config del canal de broadcast entre instancias. None = broadcast inactivo."""

    groups: TelegramGroupsConfig | None = None
    """Config específica para chats grupales (delays, override de reactions). None = todos los defaults."""


class KnowledgeSourceConfig(BaseModel):
    """Configuración de una fuente de conocimiento externa."""

    id: str
    """Identificador único de la fuente (usado para rutas de DB y CLI)."""

    type: str
    """Tipo de fuente: 'document' | 'sqlite'."""

    enabled: bool = True
    """Si False, la fuente se ignora al construir el KnowledgeOrchestrator."""

    description: str = ""
    """Descripción de la fuente (inyectada en el system prompt)."""

    path: ExpandedPath | None = None
    """Ruta al directorio de documentos (solo para type='document')."""

    glob: str = "**/*.md"
    """Glob pattern para seleccionar archivos (solo para type='document')."""

    chunk_size: int = 500
    """Tamaño de cada chunk en palabras (solo para type='document')."""

    chunk_overlap: int = 80
    """Solapamiento entre chunks en palabras (solo para type='document')."""

    top_k: int = 3
    """Resultados máximos a recuperar de esta fuente por turno."""

    min_score: float = 0.5
    """Score mínimo de coseno para incluir un chunk."""


class KnowledgeConfig(BaseModel):
    """Configuración global del pipeline de knowledge pre-fetch."""

    model_config = ConfigDict(validate_default=True)

    enabled: bool = True
    """Si False, el pre-fetch se saltea completamente en cada turno."""

    include_memory: bool = True
    """Si True, la memoria SQLite del agente se registra como fuente automáticamente."""

    top_k_per_source: int = 3
    """top_k global por fuente cuando no se override por fuente individual."""

    min_score: float = 0.5
    """min_score global cuando no se override por fuente individual."""

    max_total_chunks: int = 10
    """Límite duro de chunks totales tras el fan-out (ordenados por score desc)."""

    token_budget_warn_threshold: int = 4000
    """Umbral estimado de tokens totales (chunks + digest + skills). Si se supera,
    se emite un WARNING con el desglose. 0 = deshabilita la advertencia."""

    sources: list[KnowledgeSourceConfig] = []
    """Lista de fuentes de conocimiento externas configuradas."""


class DelegationConfig(BaseModel):
    """Config global de delegación (aplica a todos los agentes como valores por defecto)."""

    max_iterations_per_sub: int = 10
    timeout_seconds: int = 60


class AgentDelegationConfig(BaseModel):
    """Config de delegación por agente."""

    enabled: bool = False
    allowed_targets: list[str] = []


class AdminConfig(BaseModel):
    """Configuración del admin server del daemon."""

    port: int = 6497
    host: str = "127.0.0.1"
    auth_key: str | None = None
    chat_timeout: float = 300.0
    """Timeout en segundos para turnos de chat vía REST (POST /admin/chat/turn)."""


class UserConfig(BaseModel):
    """Preferencias del usuario."""

    timezone: str = ""
    """
    Timezone IANA (ej: "America/Argentina/Buenos_Aires").

    Si queda vacío, se autodetecta desde el host vía `tzlocal` con fallback a
    "UTC". Si el valor no es una zona IANA válida, se loggea un warning y se
    autodetecta igual.
    """

    @field_validator("timezone", mode="after")
    @classmethod
    def _resolve_timezone(cls, v: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        if v:
            try:
                ZoneInfo(v)
                return v
            except (ZoneInfoNotFoundError, ValueError):
                logger.warning(
                    "user.timezone='%s' no es una zona IANA válida — autodetectando",
                    v,
                )

        try:
            import tzlocal

            detected = tzlocal.get_localzone_name()
            if detected:
                logger.info("user.timezone autodetectado desde el host: %s", detected)
                return detected
        except Exception as exc:
            logger.warning("No se pudo autodetectar timezone del host: %s", exc)

        logger.info("user.timezone fallback a UTC")
        return "UTC"


# ---------------------------------------------------------------------------
# AgentConfig — config completa y resuelta para un agente
# ---------------------------------------------------------------------------


class AgentConfig(BaseModel):
    id: str
    name: str
    description: str
    system_prompt: str
    llm: LLMConfig
    embedding: EmbeddingConfig
    memory: MemoryConfig
    chat_history: ChatHistoryConfig
    skills: SkillsConfig = SkillsConfig()
    tools: ToolsConfig = ToolsConfig()
    semantic_routing: SemanticRoutingConfig = SemanticRoutingConfig()
    workspace: WorkspaceConfig = WorkspaceConfig()
    delegation: AgentDelegationConfig = AgentDelegationConfig()
    transcription: TranscriptionConfig | None = None
    channels: dict[str, dict[str, Any]] = {}
    providers: dict[str, ProviderConfig] = {}
    """Registry de proveedores post-merge. Heredado del global + overrides del agente."""


# ---------------------------------------------------------------------------
# GlobalConfig — config del sistema (sin agentes)
# ---------------------------------------------------------------------------


class GlobalConfig(BaseModel):
    app: AppConfig
    llm: LLMConfig
    embedding: EmbeddingConfig
    memory: MemoryConfig
    chat_history: ChatHistoryConfig
    skills: SkillsConfig = SkillsConfig()
    tools: ToolsConfig = ToolsConfig()
    semantic_routing: SemanticRoutingConfig = SemanticRoutingConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    workspace: WorkspaceConfig = WorkspaceConfig()
    delegation: DelegationConfig = DelegationConfig()
    admin: AdminConfig = AdminConfig()
    user: UserConfig = UserConfig()
    transcription: TranscriptionConfig | None = None
    knowledge: KnowledgeConfig = KnowledgeConfig()
    providers: dict[str, ProviderConfig] = {}
    """Registry top-level de proveedores — credenciales compartidas por vendor."""


# ---------------------------------------------------------------------------
# Utilidades de merge
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, override: dict) -> dict:
    """
    Merge recursivo campo a campo. Los campos ausentes en override se heredan de base.
    Nunca elimina campos. override tiene prioridad sobre base.
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_yaml_safe(path: Path) -> dict:
    """Carga un YAML. Retorna dict vacío si el archivo no existe."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


# ---------------------------------------------------------------------------
# Bootstrap del directorio del usuario (~/.inaki)
# ---------------------------------------------------------------------------

_GLOBAL_YAML_HEADER = """\
# =============================================================================
# Iñaki — Configuración global
# =============================================================================
#
# Este archivo fue generado automáticamente en el primer arranque con los
# valores por defecto del sistema. Podés editarlo a mano.
#
# Referencia completa de todos los parámetros disponibles:
#   config.example.yaml (en el repo de Iñaki)
#
# Layout:
#   ~/.inaki/config/global.yaml          ← este archivo (config base)
#   ~/.inaki/config/global.secrets.yaml  ← secrets (api keys)
#   ~/.inaki/agents/{id}.yaml            ← config de cada agente
#   ~/.inaki/agents/{id}.secrets.yaml    ← secrets por agente (opcional)
# =============================================================================

"""

_SECRETS_YAML_HEADER = """\
# =============================================================================
# Iñaki — Secrets globales
# =============================================================================
#
# Poné acá las API keys compartidas entre todos los agentes.
# Este archivo NUNCA debe commitearse a un repositorio.
#
# Las credenciales viven en el bloque top-level `providers:` y se referencian
# desde cada feature (`llm`, `embedding`, `transcription`, `memory.llm`) por
# el campo `provider: <key>`. Esto evita duplicar api_key cuando varias
# features comparten vendor.
#
# Ejemplo:
#
#   providers:
#     openrouter:
#       api_key: "sk-or-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
#     groq:
#       api_key: "gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
#       base_url: "https://api.groq.com/openai/v1"
#     openai:
#       api_key: "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
#
# Los secrets por agente (tokens de Telegram, auth_keys REST) van en
# ~/.inaki/agents/{id}.secrets.yaml
# =============================================================================
"""


_DELEGATION_SECTION_COMMENT = """\

# -----------------------------------------------------------------------------
# [delegation] — Delegación agente-a-agente (defaults globales)
# -----------------------------------------------------------------------------
#
# Controla los valores por defecto para la ejecución de sub-agentes delegados.
# Per-agent `delegation.enabled: true` y `allowed_targets: [...]` siguen siendo
# necesarios en cada agents/{id}.yaml para habilitar la delegación en ese agente.
#
# Nota: NO existe campo `max_depth` — la prevención de recursión es estructural
# (el tool `delegate` se filtra automáticamente de los schemas del sub-agente).
#
# delegation:
#   max_iterations_per_sub: 10   # máx. iteraciones del tool-loop por llamada delegada
#   timeout_seconds: 60          # presupuesto de reloj por llamada delegada (asyncio.wait_for)
"""


def _render_default_global_yaml() -> str:
    """Serializa los defaults de las clases Pydantic como YAML con header."""
    defaults = {
        "app": AppConfig().model_dump(),
        "llm": LLMConfig().model_dump(),
        "embedding": EmbeddingConfig().model_dump(),
        "memory": MemoryConfig().model_dump(),
        "chat_history": ChatHistoryConfig().model_dump(),
        "skills": SkillsConfig().model_dump(),
        "tools": ToolsConfig().model_dump(),
        "scheduler": SchedulerConfig().model_dump(),
        "workspace": WorkspaceConfig().model_dump(),
        "transcription": TranscriptionConfig().model_dump(),
        "user": UserConfig().model_dump(),
    }
    body = yaml.safe_dump(defaults, sort_keys=False, default_flow_style=False)
    return _GLOBAL_YAML_HEADER + body + _DELEGATION_SECTION_COMMENT


def ensure_user_config(config_dir: Path, agents_dir: Path) -> None:
    """
    Bootstrap idempotente del layout ~/.inaki/.

    Crea `config_dir`, `agents_dir`, `global.yaml` y `global.secrets.yaml`
    si no existen. No toca archivos ya presentes.
    """
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        agents_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("No se pudo crear el directorio de configuración: %s", exc)
        raise

    global_yaml = config_dir / "global.yaml"
    if not global_yaml.exists():
        try:
            global_yaml.write_text(_render_default_global_yaml(), encoding="utf-8")
        except OSError as exc:
            logger.error("No se pudo escribir %s: %s", global_yaml, exc)
            raise
        logger.info("Config creada: %s", global_yaml)

    secrets_yaml = config_dir / "global.secrets.yaml"
    if not secrets_yaml.exists():
        try:
            secrets_yaml.write_text(_SECRETS_YAML_HEADER, encoding="utf-8")
        except OSError as exc:
            logger.error("No se pudo escribir %s: %s", secrets_yaml, exc)
            raise
        logger.info("Secrets file creado: %s", secrets_yaml)


# ---------------------------------------------------------------------------
# Legacy shape detection
# ---------------------------------------------------------------------------


_LEGACY_FIELDS: tuple[tuple[str, str], ...] = (
    ("llm", "api_key"),
    ("llm", "base_url"),
    ("embedding", "api_key"),
    ("embedding", "base_url"),
    ("transcription", "api_key"),
    ("transcription", "base_url"),
)


_LEGACY_ERROR_TEMPLATE = """\
Formato legacy detectado en config: '{field}' ya no existe. \
Las credenciales ahora viven en el bloque top-level 'providers:'. Ejemplo:

  providers:
    groq: {{ api_key: TU_API_KEY, base_url: https://api.groq.com/openai/v1 }}
  llm:
    provider: groq
    model: gpt-oss-120b

Ver docs/configuracion.md#providers.\
"""


def _check_legacy_shape(merged: dict) -> None:
    """
    Inspecciona el dict crudo mergeado y rechaza el shape viejo.

    Busca ``llm.api_key``, ``llm.base_url``, ``embedding.{api_key,base_url}``,
    ``transcription.{api_key,base_url}``, ``memory.llm.{api_key,base_url}``.
    Si alguno existe levanta ``ConfigError`` con mensaje accionable en español
    que incluye un ejemplo del shape nuevo.

    DEBE correr ANTES de ``model_validate`` porque pydantic strict rechazaría
    el field desconocido con un mensaje genérico, perdiendo el ejemplo.
    """
    from core.domain.errors import ConfigError

    for section, key in _LEGACY_FIELDS:
        node = merged.get(section)
        if isinstance(node, dict) and key in node:
            raise ConfigError(_LEGACY_ERROR_TEMPLATE.format(field=f"{section}.{key}"))

    memory = merged.get("memory")
    if isinstance(memory, dict):
        memory_llm = memory.get("llm")
        if isinstance(memory_llm, dict):
            for key in ("api_key", "base_url"):
                if key in memory_llm:
                    raise ConfigError(_LEGACY_ERROR_TEMPLATE.format(field=f"memory.llm.{key}"))


def _parse_providers(merged: dict) -> dict[str, ProviderConfig]:
    """Construye el dict ``{key: ProviderConfig}`` desde el merged raw."""
    providers_raw = merged.get("providers") or {}
    if not isinstance(providers_raw, dict):
        from core.domain.errors import ConfigError

        raise ConfigError("El bloque 'providers:' debe ser un diccionario de entradas por vendor.")
    return {key: ProviderConfig(**(entry or {})) for key, entry in providers_raw.items()}


# ---------------------------------------------------------------------------
# Carga de configuración
# ---------------------------------------------------------------------------


def load_global_config(config_dir: Path) -> tuple[GlobalConfig, dict]:
    """
    Carga y mergea global.yaml + global.secrets.yaml.
    Retorna (GlobalConfig, raw_dict) — el dict raw se usa para merge con agentes.
    """
    base = _load_yaml_safe(config_dir / "global.yaml")
    secrets = _load_yaml_safe(config_dir / "global.secrets.yaml")

    if not secrets and not (config_dir / "global.secrets.yaml").exists():
        logger.debug("global.secrets.yaml no encontrado — usando solo global.yaml")

    merged = _deep_merge(base, secrets)

    _check_legacy_shape(merged)
    providers = _parse_providers(merged)

    app = AppConfig(**merged.get("app", {}))
    llm = LLMConfig(**merged.get("llm", {}))
    embedding = EmbeddingConfig(**merged.get("embedding", {}))
    memory = MemoryConfig(**merged.get("memory", {}))
    chat_history = ChatHistoryConfig(**merged.get("chat_history", {}))

    skills = SkillsConfig(**merged.get("skills", {}))
    tools = ToolsConfig(**merged.get("tools", {}))
    semantic_routing = SemanticRoutingConfig(**merged.get("semantic_routing", {}))
    scheduler = SchedulerConfig(**merged.get("scheduler", {}))
    workspace = WorkspaceConfig(**merged.get("workspace", {}))
    delegation = DelegationConfig(**merged.get("delegation", {}))
    admin = AdminConfig(**merged.get("admin", {}))
    user = UserConfig(**merged.get("user", {}))
    transcription = (
        TranscriptionConfig(**merged["transcription"])
        if merged.get("transcription") is not None
        else None
    )

    knowledge_raw = merged.get("knowledge")
    if knowledge_raw is not None:
        sources_raw = knowledge_raw.pop("sources", []) or []
        sources = [KnowledgeSourceConfig(**s) for s in sources_raw]
        knowledge = KnowledgeConfig(**knowledge_raw, sources=sources)
    else:
        knowledge = KnowledgeConfig()

    global_cfg = GlobalConfig(
        app=app,
        llm=llm,
        embedding=embedding,
        memory=memory,
        chat_history=chat_history,
        skills=skills,
        tools=tools,
        semantic_routing=semantic_routing,
        scheduler=scheduler,
        workspace=workspace,
        delegation=delegation,
        admin=admin,
        user=user,
        transcription=transcription,
        knowledge=knowledge,
        providers=providers,
    )
    return global_cfg, merged


def load_agent_config(
    agent_id: str,
    agents_dir: Path,
    global_raw: dict,
) -> AgentConfig | None:
    """
    Carga y mergea la config de un agente:
      global_raw → agents/{id}.yaml → agents/{id}.secrets.yaml

    Retorna None si el agente tiene config inválida (loggea WARNING).
    """
    agent_yaml = agents_dir / f"{agent_id}.yaml"
    agent_secrets = agents_dir / f"{agent_id}.secrets.yaml"

    if not agent_yaml.exists():
        logger.warning("Config del agente '%s' no encontrada: %s", agent_id, agent_yaml)
        return None

    agent_raw = _load_yaml_safe(agent_yaml)

    if agent_secrets.exists():
        secrets_raw = _load_yaml_safe(agent_secrets)
        agent_raw = _deep_merge(agent_raw, secrets_raw)
    else:
        logger.warning(
            "Agente '%s': %s no encontrado — canales con secrets no levantarán.",
            agent_id,
            agent_secrets.name,
        )

    # Merge: global como base, agente como override
    merged = _deep_merge(global_raw, agent_raw)

    _check_legacy_shape(merged)

    try:
        providers = _parse_providers(merged)
        transcription_raw = merged.get("transcription")
        transcription = (
            TranscriptionConfig(**transcription_raw) if transcription_raw is not None else None
        )
        return AgentConfig(
            id=merged["id"],
            name=merged["name"],
            description=merged["description"],
            system_prompt=merged["system_prompt"],
            llm=LLMConfig(**merged.get("llm", {})),
            embedding=EmbeddingConfig(**merged.get("embedding", {})),
            memory=MemoryConfig(**merged.get("memory", {})),
            chat_history=ChatHistoryConfig(**merged.get("chat_history", {})),
            skills=SkillsConfig(**merged.get("skills", {})),
            tools=ToolsConfig(**merged.get("tools", {})),
            semantic_routing=SemanticRoutingConfig(**merged.get("semantic_routing", {})),
            workspace=WorkspaceConfig(**merged.get("workspace", {})),
            delegation=AgentDelegationConfig(**merged.get("delegation", {})),
            transcription=transcription,
            channels=merged.get("channels", {}),
            providers=providers,
        )
    except (KeyError, ValueError) as exc:
        logger.warning("Config inválida para agente '%s': %s", agent_id, exc)
        return None


# ---------------------------------------------------------------------------
# AgentRegistry
# ---------------------------------------------------------------------------


class AgentRegistry:
    """
    Escanea el directorio de agentes al arrancar y construye el registro.
    Los agentes con config inválida se omiten con WARNING.
    """

    def __init__(self, agents_dir: Path, global_raw: dict) -> None:
        self._agents: dict[str, AgentConfig] = {}

        if not agents_dir.exists():
            logger.warning("Directorio de agentes no encontrado: %s", agents_dir)
            return

        for yaml_file in sorted(agents_dir.glob("*.yaml")):
            if ".secrets" in yaml_file.name or ".example" in yaml_file.name:
                continue
            agent_id = yaml_file.stem
            cfg = load_agent_config(agent_id, agents_dir, global_raw)
            if cfg is not None:
                self._agents[agent_id] = cfg
                logger.debug("Agente '%s' cargado: %s", agent_id, cfg.name)

        logger.info(
            "AgentRegistry: %d agente(s) cargado(s): %s", len(self._agents), list(self._agents)
        )

        _validate_channel_uniqueness(self._agents)

    def get(self, agent_id: str) -> AgentConfig:
        if agent_id not in self._agents:
            from core.domain.errors import AgentNotFoundError

            raise AgentNotFoundError(
                f"Agente '{agent_id}' no encontrado. Disponibles: {list(self._agents)}"
            )
        return self._agents[agent_id]

    def list_all(self) -> list[AgentConfig]:
        return list(self._agents.values())

    def agents_with_channel(self, channel_type: str) -> list[AgentConfig]:
        return [a for a in self._agents.values() if channel_type in a.channels]


def _validate_channel_uniqueness(agents: dict[str, AgentConfig]) -> None:
    """
    Rechaza configs donde varios agentes comparten la misma identidad de canal,
    o donde un mismo agente tiene dos canales con el mismo ``broadcast.port``.

    Motivo: un bot de Telegram solo admite UN ``getUpdates`` activo por token
    (Telegram API), y un socket TCP solo acepta UN bind por ``host:port`` (SO).
    Si dos agentes declaran el mismo token o el mismo host:port, el daemon
    levanta pollings/sockets que se pisan → errores ``Conflict`` en loop o
    ``address already in use``.

    El modelo canónico: un solo agente expone el canal (entry point) y delega
    a los subagentes vía la tool ``delegate``. Los subagentes NO deben
    declarar ``channels.telegram`` ni ``channels.rest`` apuntando al mismo
    token/puerto que el principal.

    Broadcast port uniqueness: dentro de un mismo agente, dos canales no pueden
    declarar el mismo ``broadcast.port`` — ambos intentarían hacer ``bind()``
    en el mismo puerto del host.
    """
    from core.domain.errors import ConfigError

    telegram_tokens: dict[str, list[str]] = {}
    rest_addrs: dict[tuple[str, int], list[str]] = {}

    for agent_id, cfg in agents.items():
        tg_cfg = cfg.channels.get("telegram") or {}
        token = tg_cfg.get("token")
        if token:
            telegram_tokens.setdefault(token, []).append(agent_id)

        rest_cfg = cfg.channels.get("rest") or {}
        if rest_cfg:
            host = rest_cfg.get("host", "0.0.0.0")
            port = rest_cfg.get("port")
            if port is not None:
                rest_addrs.setdefault((host, int(port)), []).append(agent_id)

        # Unicidad de broadcast.port dentro del mismo agente.
        broadcast_ports: dict[int, list[str]] = {}
        for channel_name, channel_raw in cfg.channels.items():
            if not isinstance(channel_raw, dict):
                continue
            bc_raw = channel_raw.get("broadcast")
            if not isinstance(bc_raw, dict):
                continue
            bc_port = bc_raw.get("port")
            if bc_port is not None:
                broadcast_ports.setdefault(int(bc_port), []).append(channel_name)

        duplicated_bc_ports = {p: chs for p, chs in broadcast_ports.items() if len(chs) > 1}
        if duplicated_bc_ports:
            conflicts = "; ".join(
                f"port {p} declarado en [{', '.join(chs)}]"
                for p, chs in duplicated_bc_ports.items()
            )
            raise ConfigError(
                f"Agente '{agent_id}': broadcast.port duplicado — {conflicts}. "
                "Cada canal del agente debe usar un puerto de broadcast distinto."
            )

    duplicated_tokens = {tok: ids for tok, ids in telegram_tokens.items() if len(ids) > 1}
    duplicated_addrs = {addr: ids for addr, ids in rest_addrs.items() if len(ids) > 1}

    if duplicated_tokens:
        agent_lists = "; ".join(f"agentes [{', '.join(ids)}]" for ids in duplicated_tokens.values())
        raise ConfigError(
            f"Token de Telegram duplicado entre {agent_lists}. "
            "Un token solo admite un polling activo: dejá 'channels.telegram' únicamente "
            "en el agente que actúa como entry point; los subagentes reciben mensajes "
            "vía la tool 'delegate'."
        )

    if duplicated_addrs:
        addr_lists = "; ".join(
            f"{host}:{port} usada por [{', '.join(ids)}]"
            for (host, port), ids in duplicated_addrs.items()
        )
        raise ConfigError(
            f"Dirección REST duplicada entre agentes: {addr_lists}. "
            "Asigná un 'channels.rest.port' distinto a cada agente o quitá el canal "
            "REST de los subagentes."
        )
