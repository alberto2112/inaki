"""
Resolución de rutas para el repositorio de configuración YAML.

Layout canónico en ``~/.inaki/config/``:
  global.yaml
  global.secrets.yaml
  agents/{id}.yaml
  agents/{id}.secrets.yaml

Se puede sobreescribir la raíz vía la variable de entorno ``INAKI_CONFIG_DIR``,
igual que el mecanismo ``--config DIR`` de ``infrastructure/config.py``.
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
    Devuelve el directorio de configs de agentes (``~/.inaki/config/agents/``).

    Históricamente el código de ``infrastructure/config.py`` usa
    ``~/.inaki/agents/`` (sin ``config/`` intermedio), pero el nuevo adapter
    TUI unifica todo bajo ``~/.inaki/config/agents/`` para mantener la config
    agrupada. Si necesitás leer agentes del layout legacy, usá el
    ``infrastructure/config.py`` directamente.
    """
    return get_config_dir() / "agents"


def global_yaml_path() -> Path:
    """Ruta a ``~/.inaki/config/global.yaml``."""
    return get_config_dir() / "global.yaml"


def global_secrets_path() -> Path:
    """Ruta a ``~/.inaki/config/global.secrets.yaml``."""
    return get_config_dir() / "global.secrets.yaml"


def agent_yaml_path(agent_id: str) -> Path:
    """
    Ruta a ``~/.inaki/config/agents/{agent_id}.yaml``.

    Args:
        agent_id: Identificador del agente (sin extensión).
    """
    if not agent_id:
        raise ValueError("agent_id no puede ser vacío")
    return get_agents_dir() / f"{agent_id}.yaml"


def agent_secrets_path(agent_id: str) -> Path:
    """
    Ruta a ``~/.inaki/config/agents/{agent_id}.secrets.yaml``.

    Args:
        agent_id: Identificador del agente (sin extensión).
    """
    if not agent_id:
        raise ValueError("agent_id no puede ser vacío")
    return get_agents_dir() / f"{agent_id}.secrets.yaml"
