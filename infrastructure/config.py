"""
Sistema de configuración de Iñaki.

4 capas de merge en orden (cada capa sobreescribe solo los campos que define):
  1. config/global.yaml          — config base del sistema
  2. config/global.secrets.yaml  — secrets globales (api keys compartidas)
  3. config/agents/{id}.yaml     — config y canales del agente
  4. config/agents/{id}.secrets.yaml — secrets del agente (opcional)

Regla de secrets: si el agente no define un secret, hereda del global.
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------

class AppConfig(BaseModel):
    name: str = "Iñaki"
    log_level: str = "INFO"
    data_dir: str = "data"
    models_dir: str = "models"
    skills_dir: str = "skills"
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
    model_path: str = "models/e5-small"   # solo e5_onnx
    model: str = "text-embedding-3-small"  # solo openai
    dimension: int = 384
    base_url: str = "https://api.openai.com/v1"  # solo openai
    api_key: str | None = None             # solo openai — en secrets


class MemoryConfig(BaseModel):
    db_path: str = "data/inaki.db"
    default_top_k: int = 5


class HistoryConfig(BaseModel):
    active_dir: str = "data/history/active"
    archive_dir: str = "data/history/archive"
    max_messages_in_prompt: int = 0  # 0 = sin límite; N = últimos N mensajes por participante


class SkillsConfig(BaseModel):
    rag_min_skills: int = 10
    rag_top_k: int = 3


class ToolsConfig(BaseModel):
    rag_min_tools: int = 10
    rag_top_k: int = 5
    tool_call_max_iterations: int = 5


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
    history: HistoryConfig
    skills: SkillsConfig = SkillsConfig()
    tools: ToolsConfig = ToolsConfig()
    channels: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# GlobalConfig — config del sistema (sin agentes)
# ---------------------------------------------------------------------------

class GlobalConfig(BaseModel):
    app: AppConfig
    llm: LLMConfig
    embedding: EmbeddingConfig
    memory: MemoryConfig
    history: HistoryConfig
    skills: SkillsConfig = SkillsConfig()
    tools: ToolsConfig = ToolsConfig()


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
    history = HistoryConfig(**merged.get("history", {}))

    skills = SkillsConfig(**merged.get("skills", {}))
    tools = ToolsConfig(**merged.get("tools", {}))

    global_cfg = GlobalConfig(
        app=app,
        llm=llm,
        embedding=embedding,
        memory=memory,
        history=history,
        skills=skills,
        tools=tools,
    )
    return global_cfg, merged


def load_agent_config(
    agent_id: str,
    config_dir: Path,
    global_raw: dict,
) -> AgentConfig | None:
    """
    Carga y mergea la config de un agente:
      global_raw → agents/{id}.yaml → agents/{id}.secrets.yaml

    Retorna None si el agente tiene config inválida (loggea WARNING).
    """
    agents_dir = config_dir / "agents"
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
            history=HistoryConfig(**merged.get("history", {})),
            skills=SkillsConfig(**merged.get("skills", {})),
            tools=ToolsConfig(**merged.get("tools", {})),
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
    Escanea config/agents/ al arrancar y construye el registro de agentes.
    Los agentes con config inválida se omiten con WARNING.
    """

    def __init__(self, config_dir: Path, global_raw: dict) -> None:
        self._agents: dict[str, AgentConfig] = {}
        agents_dir = config_dir / "agents"

        if not agents_dir.exists():
            logger.warning("Directorio de agentes no encontrado: %s", agents_dir)
            return

        for yaml_file in sorted(agents_dir.glob("*.yaml")):
            # Ignorar .example y .secrets
            if ".secrets" in yaml_file.name or ".example" in yaml_file.name:
                continue
            agent_id = yaml_file.stem
            cfg = load_agent_config(agent_id, config_dir, global_raw)
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
