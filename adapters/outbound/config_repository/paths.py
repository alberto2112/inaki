"""
Resolución de rutas para el repositorio de configuración YAML.

Layout canónico (matchea el runtime ``infrastructure/config.py``):
  ~/.inaki/config/global.yaml
  ~/.inaki/config/global.secrets.yaml
  ~/.inaki/agents/{id}.yaml            ← sibling de config/, no subcarpeta
  ~/.inaki/agents/{id}.secrets.yaml

Con home relocalizado (``INAKI_HOME=HOME``, propagado por el composition root desde
``--home``):
  HOME/config/global.yaml
  HOME/config/global.secrets.yaml
  HOME/agents/{id}.yaml
  HOME/agents/{id}.secrets.yaml

La TUI MATCHEA al runtime — no impone convención propia. Cualquier desviación
acá rompe a usuarios con installs existentes.
"""

from __future__ import annotations

import os
from pathlib import Path


def get_config_dir() -> Path:
    """
    Devuelve el directorio raíz de configuración (``<home>/config/``).

    El home se resuelve por ``INAKI_HOME`` env (que el composition root propaga desde
    ``--home``) → default ``~/.inaki``. Coincide con ``get_inaki_home()/"config"`` del runtime.
    """
    home = os.environ.get("INAKI_HOME")
    if home:
        return Path(home).expanduser().resolve() / "config"
    return Path.home() / ".inaki" / "config"


def get_agents_dir() -> Path:
    """
    Devuelve el directorio de configs de agentes (``<home>/agents/``).

    Sibling de ``<home>/config/``, sin ``config/`` intermedio. El home se resuelve por
    ``INAKI_HOME`` env (propagado desde ``--home``) → default ``~/.inaki``. Coincide con
    ``get_inaki_home()/"agents"`` del runtime.
    """
    home = os.environ.get("INAKI_HOME")
    if home:
        return Path(home).expanduser().resolve() / "agents"
    return Path.home() / ".inaki" / "agents"


def global_yaml_path() -> Path:
    """Ruta a ``~/.inaki/config/global.yaml`` (o ``$INAKI_HOME/config/global.yaml``)."""
    return get_config_dir() / "global.yaml"


def global_secrets_path() -> Path:
    """Ruta a ``~/.inaki/config/global.secrets.yaml``."""
    return get_config_dir() / "global.secrets.yaml"


def agent_yaml_path(agent_id: str) -> Path:
    """
    Ruta a ``~/.inaki/agents/{agent_id}.yaml`` (default) o ``$INAKI_HOME/agents/...``.

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
