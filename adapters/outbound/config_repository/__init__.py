"""
Adapter outbound: repositorio YAML de configuración con preservación de comentarios.
"""

from .paths import (
    agent_secrets_path,
    agent_yaml_path,
    get_agents_dir,
    get_config_dir,
    global_secrets_path,
    global_yaml_path,
)
from .yaml_repository import YamlRepository

__all__ = [
    "YamlRepository",
    "get_config_dir",
    "get_agents_dir",
    "global_yaml_path",
    "global_secrets_path",
    "agent_yaml_path",
    "agent_secrets_path",
]
