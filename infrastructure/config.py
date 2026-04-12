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
from pydantic import BaseModel, BeforeValidator, field_validator

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


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------

class AppConfig(BaseModel):
    name: str = "Iñaki"
    log_level: str = "INFO"
    data_dir: ExpandedPath = "data"
    models_dir: ExpandedPath = "models"
    ext_dirs: ExpandedPathList = ["ext", "~/.inaki/ext"]
    default_agent: str = "general"


class LLMConfig(BaseModel):
    provider: str = "openrouter"
    base_url: str | None = None   # None → cada provider usa su propio default
    model: str = "anthropic/claude-3-5-haiku"
    temperature: float = 0.7
    max_tokens: int = 2048
    api_key: str | None = None


class EmbeddingConfig(BaseModel):
    provider: str = "e5_onnx"
    model_path: ExpandedPath = "models/e5-small"   # solo e5_onnx
    model: str = "text-embedding-3-small"  # solo openai
    dimension: int = 384
    base_url: str = "https://api.openai.com/v1"  # solo openai
    api_key: str | None = None             # solo openai — en secrets


_KEEP_LAST_MESSAGES_FALLBACK = 84


class MemoryConfig(BaseModel):
    db_path: ExpandedPath = "data/inaki.db"
    default_top_k: int = 5
    digest_size: int = 14
    digest_path: Path = Path("~/.inaki/mem/last_memories.md")
    min_relevance_score: float = 0.5
    schedule: str = "0 3 * * *"
    delay_seconds: int = 2
    keep_last_messages: int = 0
    enabled: bool = True

    @field_validator("digest_path", mode="before")
    @classmethod
    def _expand_digest_path(cls, v) -> Path:
        return Path(v).expanduser()

    def model_post_init(self, __context: object) -> None:
        # Expand ~ in the default value (field_validator does not run on class defaults)
        object.__setattr__(self, "digest_path", self.digest_path.expanduser())

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
    db_path: ExpandedPath = "data/history.db"
    max_messages: int = 0  # 0 = sin límite; N = últimos N mensajes al LLM


class SchedulerConfig(BaseModel):
    enabled: bool = True
    db_path: ExpandedPath = "data/scheduler.db"
    max_retries: int = 3
    output_truncation_size: int = 65536


class SkillsConfig(BaseModel):
    rag_min_skills: int = 10
    rag_top_k: int = 3


class ToolsConfig(BaseModel):
    rag_min_tools: int = 10
    rag_top_k: int = 5
    tool_call_max_iterations: int = 5
    circuit_breaker_threshold: int = 2


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


class DelegationConfig(BaseModel):
    """Config global de delegación (aplica a todos los agentes como valores por defecto)."""

    max_iterations_per_sub: int = 10
    timeout_seconds: int = 60


class AgentDelegationConfig(BaseModel):
    """Config de delegación por agente."""

    enabled: bool = False
    allowed_targets: list[str] = []


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
    workspace: WorkspaceConfig = WorkspaceConfig()
    delegation: AgentDelegationConfig = AgentDelegationConfig()
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
    scheduler: SchedulerConfig = SchedulerConfig()
    workspace: WorkspaceConfig = WorkspaceConfig()
    delegation: DelegationConfig = DelegationConfig()


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
    mem = MemoryConfig().model_dump()
    # Path no es serializable por yaml.safe_dump — convertir a str
    mem["digest_path"] = str(mem["digest_path"])
    defaults = {
        "app": AppConfig().model_dump(),
        "llm": LLMConfig().model_dump(exclude={"api_key"}),
        "embedding": EmbeddingConfig().model_dump(exclude={"api_key"}),
        "memory": mem,
        "chat_history": ChatHistoryConfig().model_dump(),
        "skills": SkillsConfig().model_dump(),
        "tools": ToolsConfig().model_dump(),
        "scheduler": SchedulerConfig().model_dump(),
        "workspace": WorkspaceConfig().model_dump(),
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

    if not secrets and (config_dir / "global.secrets.yaml").exists() is False:
        logger.debug("global.secrets.yaml no encontrado — usando solo global.yaml")

    merged = _deep_merge(base, secrets)

    app = AppConfig(**merged.get("app", {}))
    llm = LLMConfig(**merged.get("llm", {}))
    embedding = EmbeddingConfig(**merged.get("embedding", {}))
    memory = MemoryConfig(**merged.get("memory", {}))
    chat_history = ChatHistoryConfig(**merged.get("chat_history", {}))

    skills = SkillsConfig(**merged.get("skills", {}))
    tools = ToolsConfig(**merged.get("tools", {}))
    scheduler = SchedulerConfig(**merged.get("scheduler", {}))
    workspace = WorkspaceConfig(**merged.get("workspace", {}))
    delegation = DelegationConfig(**merged.get("delegation", {}))

    global_cfg = GlobalConfig(
        app=app,
        llm=llm,
        embedding=embedding,
        memory=memory,
        chat_history=chat_history,
        skills=skills,
        tools=tools,
        scheduler=scheduler,
        workspace=workspace,
        delegation=delegation,
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
            workspace=WorkspaceConfig(**merged.get("workspace", {})),
            delegation=AgentDelegationConfig(**merged.get("delegation", {})),
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

        logger.info("AgentRegistry: %d agente(s) cargado(s): %s", len(self._agents), list(self._agents))

    def get(self, agent_id: str) -> AgentConfig:
        if agent_id not in self._agents:
            from core.domain.errors import AgentNotFoundError
            raise AgentNotFoundError(f"Agente '{agent_id}' no encontrado. Disponibles: {list(self._agents)}")
        return self._agents[agent_id]

    def list_all(self) -> list[AgentConfig]:
        return list(self._agents.values())

    def agents_with_channel(self, channel_type: str) -> list[AgentConfig]:
        return [a for a in self._agents.values() if channel_type in a.channels]
