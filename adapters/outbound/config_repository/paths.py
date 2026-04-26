"""
Resolución de rutas para el repositorio de configuración YAML.

Layout canónico (matchea el runtime ``infrastructure/config.py``):
  ~/.inaki/config/global.yaml
  ~/.inaki/config/global.secrets.yaml
  ~/.inaki/agents/{id}.yaml            ← sibling de config/, no subcarpeta
  ~/.inaki/agents/{id}.secrets.yaml

Layout legacy unificado (cuando se setea ``INAKI_CONFIG_DIR=DIR``):
  DIR/global.yaml
  DIR/global.secrets.yaml
  DIR/agents/{id}.yaml
  DIR/agents/{id}.secrets.yaml

La TUI MATCHEA al runtime — no impone convención propia. Cualquier desviación
acá rompe a usuarios con installs existentes.
"""

from __future__ import annotations

import os
from pathlib import Path


def get_config_dir() -> Path:
    """
    Devuelve el directorio raíz de configuración (``~/.inaki/config/``).

    Si la variable de entorno ``INAKI_CONFIG_DIR`` está definida, se usa ese
    valor como override (útil en tests y desarrollo).
    """
    env_override = os.environ.get("INAKI_CONFIG_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve()
    return Path.home() / ".inaki" / "config"


def get_agents_dir() -> Path:
    """
    Devuelve el directorio de configs de agentes.

    Default (sin env override): ``~/.inaki/agents/`` — sibling de
    ``~/.inaki/config/``, sin ``config/`` intermedio. Coincide exactamente
    con la convención que usa ``infrastructure/config.py`` en runtime.

    Con ``INAKI_CONFIG_DIR=DIR`` (modo legacy unificado): ``<DIR>/agents/``,
    también consistente con el override del runtime.
    """
    env_override = os.environ.get("INAKI_CONFIG_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve() / "agents"
    return Path.home() / ".inaki" / "agents"


def global_yaml_path() -> Path:
    """Ruta a ``~/.inaki/config/global.yaml`` (o ``$INAKI_CONFIG_DIR/global.yaml``)."""
    return get_config_dir() / "global.yaml"


def global_secrets_path() -> Path:
    """Ruta a ``~/.inaki/config/global.secrets.yaml``."""
    return get_config_dir() / "global.secrets.yaml"


def agent_yaml_path(agent_id: str) -> Path:
    """
    Ruta a ``~/.inaki/agents/{agent_id}.yaml`` (default) o ``$INAKI_CONFIG_DIR/agents/...`` (legacy).

    Args:
        agent_id: Identificador del agente (sin extensión).
    """
    if not agent_id:
        raise ValueError("agent_id no puede ser vacío")
    return get_agents_dir() / f"{agent_id}.yaml"


def agent_secrets_path(agent_id: str) -> Path:
    """
    Ruta a ``~/.inaki/agents/{agent_id}.secrets.yaml`` (default).

    Args:
        agent_id: Identificador del agente (sin extensión).
    """
    if not agent_id:
        raise ValueError("agent_id no puede ser vacío")
    return get_agents_dir() / f"{agent_id}.secrets.yaml"
