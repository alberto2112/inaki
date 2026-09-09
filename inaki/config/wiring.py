"""Wiring del módulo config: la tool ``config`` que el LLM invoca.

El snapshot se arma en el ARRANQUE porque acá vive la config ya validada que el
proceso usa: la tool no vuelve a tocar disco en ningún turno. Un reload
reconstruye el agente entero, así que el snapshot no puede quedar viejo
(convención "tool config — solo lectura, y el valor sale de MEMORIA").
"""

from __future__ import annotations

import logging

from inaki.config.adapters.yaml_repository import YamlRepository
from inaki.config.home import get_inaki_home
from inaki.config.introspection import defaults_del_schema, paths_secretos
from inaki.config.merge import deep_merge
from inaki.config.schema import AgentConfig, GlobalConfig
from inaki.config.tools.config_tool import ConfigTool
from inaki.config.use_cases.runtime_config import RuntimeConfigUseCase
from inaki.config.use_cases.show_effective import ShowEffectiveConfigUseCase

logger = logging.getLogger(__name__)


def build_config_tool(global_cfg: GlobalConfig, agent_cfg: AgentConfig) -> ConfigTool:
    """El VALOR sale de los objetos ya validados (``global_cfg`` como base, ``agent_cfg``
    encima: el mismo orden de capas del loader), no del merge de los YAML: es lo
    único que responde con qué está corriendo el proceso. El ORIGEN de cada valor
    sí sale de la vista de disco, leída en este mismo instante, cuando disco y
    memoria todavía coinciden."""
    en_memoria = deep_merge(global_cfg.model_dump(), agent_cfg.model_dump())
    return ConfigTool(
        runtime_config=RuntimeConfigUseCase(
            config_en_memoria=en_memoria,
            origenes=_origenes_de_config(agent_cfg.id),
            paths_secretos=paths_secretos(),
        )
    )


def _origenes_de_config(agent_id: str) -> dict[str, str]:
    """Mapa ``path -> capa`` leyendo los YAML. ``{}`` si no se pueden leer.

    Degradar acá es legítimo y no toca ningún valor: lo que se pierde es la
    anotación de procedencia, y el snapshot la reporta como ``desconocido`` en
    vez de inventar ``default``. Se degrada por una dependencia EXTERNA (el
    filesystem), nunca por la config en sí.
    """
    home = get_inaki_home()
    try:
        vista = ShowEffectiveConfigUseCase(
            repo=YamlRepository(config_dir=home / "config", agents_dir=home / "agents"),
            defaults=defaults_del_schema(),
            paths_secretos=paths_secretos(),
        ).execute(agent_id)
    except Exception:
        logger.warning(
            "Agente '%s': no se pudieron leer las capas de config desde %s. "
            "La tool `config` sigue sirviendo los valores en memoria, pero sin "
            "decir de qué capa sale cada uno.",
            agent_id,
            home,
            exc_info=True,
        )
        return {}
    return {campo.path: campo.origen for campo in vista.campos}
