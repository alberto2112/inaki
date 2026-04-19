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
from pydantic import BaseModel, BeforeValidator, ConfigDict, field_validator

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


class LLMConfig(BaseModel):
    provider: str = "openrouter"
    base_url: str | None = None  # None → cada provider usa su propio default
    model: str = "anthropic/claude-3-5-haiku"
    temperature: float = 0.7
    max_tokens: int = 2048
    api_key: str | None = None


class EmbeddingConfig(BaseModel):
    model_config = ConfigDict(validate_default=True)  # RuntimePath en los defaults

    provider: str = "e5_onnx"
    model_dirname: RuntimePath = "models/e5-small"  # solo e5_onnx — relativo a ~/.inaki/
    model: str = "text-embedding-3-small"  # solo openai
    dimension: int = 384
    base_url: str = "https://api.openai.com/v1"  # solo openai
    api_key: str | None = None  # solo openai — en secrets
    cache_filename: RuntimePath = "data/embedding_cache.db"  # relativo a ~/.inaki/


class TranscriptionConfig(BaseModel):
    """Config del provider de transcripción de audio (opcional)."""

    provider: str = "groq"
    model: str = "whisper-large-v3-turbo"
    base_url: str | None = None  # None → el adapter usa su default (p. ej. Groq)
    language: str | None = None  # None → auto-detect por el modelo
    api_key: str | None = None  # en secrets
    timeout_seconds: int = 60  # segundos para el request HTTP
    max_audio_mb: int = 25  # límite de tamaño de audio (MB) — Groq Whisper: 25


_KEEP_LAST_MESSAGES_FALLBACK = 84


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

    def resolved_keep_last_messages(self) -> int:
        """
        Devuelve cuántos mensajes preservar por agente tras la consolidación.
        0 (default) es un sentinel que significa 'usar el fallback del sistema'
        ({fallback}). Cualquier valor > 0 se respeta tal cual.
        """.format(fallback=_KEEP_LAST_MESSAGES_FALLBACK)
        if self.keep_last_messages <= 0:
            return _KEEP_LAST_MESSAGES_FALLBACK
        return self.keep_last_messages


class ChatHistoryConfig(BaseModel):
    model_config = ConfigDict(validate_default=True)  # RuntimePath en los defaults

    db_filename: RuntimePath = "data/history.db"  # relativo a ~/.inaki/
    max_messages: int = 0  # 0 = sin límite; N = últimos N mensajes al LLM


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
# Ejemplo:
#
#   llm:
#     api_key: "sk-or-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
#
#   embedding:
#     api_key: "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
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
        "llm": LLMConfig().model_dump(exclude={"api_key"}),
        "embedding": EmbeddingConfig().model_dump(exclude={"api_key"}),
        "memory": MemoryConfig().model_dump(),
        "chat_history": ChatHistoryConfig().model_dump(),
        "skills": SkillsConfig().model_dump(),
        "tools": ToolsConfig().model_dump(),
        "scheduler": SchedulerConfig().model_dump(),
        "workspace": WorkspaceConfig().model_dump(),
        "transcription": TranscriptionConfig().model_dump(exclude={"api_key"}),
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

    try:
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
