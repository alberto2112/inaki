"""Wiring del módulo config: la tool ``config`` que el LLM invoca y la UI web de config.

El snapshot se arma en el ARRANQUE porque acá vive la config ya validada que el
proceso usa: la tool no vuelve a tocar disco en ningún turno. Un reload
reconstruye el agente entero, así que el snapshot no puede quedar viejo
(convención "tool config — solo lectura, y el valor sale de MEMORIA").
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from inaki.config.adapters.yaml_repository import YamlRepository
from inaki.config.boundary import errores_del_operador
from inaki.config.home import get_inaki_home
from inaki.config.introspection import (
    CampoDelSchema,
    campos_del_schema,
    defaults_del_schema,
    paths_fijos_en_runtime,
    paths_secretos,
)
from inaki.config.merge import deep_merge
from inaki.config.ports import ISolicitanteDeReload
from inaki.config.runtime_overrides import OverridesDeRuntime
from inaki.config.schema import AgentConfig, GlobalConfig
from inaki.config.tools.config_set_tool import ConfigSetTool
from inaki.config.tools.config_tool import ConfigTool
from inaki.config.use_cases.apply_changes import ApplyConfigChangesUseCase
from inaki.config.use_cases.manage_agents import ManageAgentsUseCase
from inaki.config.use_cases.manage_providers import ManageProvidersUseCase
from inaki.config.use_cases.runtime_config import RuntimeConfigUseCase
from inaki.config.use_cases.runtime_overrides import (
    ConstruccionRechazadaError,
    GestionarOverridesUseCase,
)
from inaki.config.use_cases.show_effective import ShowEffectiveConfigUseCase
from inaki.shared.channel_context import ChannelContext
from inaki.shared.errors import ConfigError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConfigWeb:
    """Lo que la UI web de config consume: la vista efectiva, el carril de edición
    validado, la ayuda del schema y la lista de agentes. Quien la sirve (el admin
    server del daemon o ``inaki config web``) no conoce loader ni repo."""

    show: ShowEffectiveConfigUseCase
    apply: ApplyConfigChangesUseCase
    schema: list[CampoDelSchema]
    agentes: Callable[[], tuple[list[str], list[str]]]
    """``() -> (agentes regulares, sub-agentes)``, leídos de disco en cada llamada."""
    manage_agents: ManageAgentsUseCase
    manage_providers: ManageProvidersUseCase
    daemon: bool
    """``True`` en el admin server: la UI ofrece recargar el daemon tras guardar."""


def build_config_web(*, daemon: bool) -> ConfigWeb:
    """La UI edita SOLO lo que arranca: ``validar`` es el loader del arranque entero
    (global + todos los agentes + unicidad de canal), y el use case deshace la
    escritura si lo rechaza (``borde-de-config``: una vista que no valida con el
    mismo loader puede decir "todo bien" sobre lo que no arranca)."""
    from inaki.config.loader import AgentRegistry, load_global_config

    home = get_inaki_home()
    config_dir, agents_dir = home / "config", home / "agents"
    repo = YamlRepository(config_dir=config_dir, agents_dir=agents_dir)

    def validar() -> None:
        _, global_raw = load_global_config(config_dir)
        AgentRegistry(agents_dir, global_raw)

    return ConfigWeb(
        show=ShowEffectiveConfigUseCase(
            repo=repo, defaults=defaults_del_schema(), paths_secretos=paths_secretos()
        ),
        apply=ApplyConfigChangesUseCase(repo, validar),
        schema=campos_del_schema(),
        agentes=lambda: (repo.list_agents(), repo.list_sub_agents()),
        manage_agents=ManageAgentsUseCase(repo, validar),
        manage_providers=ManageProvidersUseCase(repo, validar),
        daemon=daemon,
    )


def build_config_tool(
    global_cfg: GlobalConfig,
    agent_cfg: AgentConfig,
    overrides: OverridesDeRuntime | None = None,
) -> ConfigTool:
    """El VALOR sale de los objetos ya validados (``global_cfg`` como base, ``agent_cfg``
    encima: el mismo orden de capas del loader), no del merge de los YAML: es lo
    único que responde con qué está corriendo el proceso. El ORIGEN de cada valor
    sí sale de la vista de disco, leída en este mismo instante, cuando disco y
    memoria todavía coinciden.

    ``overrides`` es la capa de ``config_set``: los objetos validados YA la traen
    aplicada (el loader la apila), así que acá solo aporta QUÉ hojas están pisadas
    para marcarlas con el origen ``runtime``. Se lee en el mismo instante que el
    disco. ``None`` → sin overrides (tests, arranques sueltos)."""
    en_memoria = deep_merge(global_cfg.model_dump(), agent_cfg.model_dump())
    return ConfigTool(
        runtime_config=RuntimeConfigUseCase(
            config_en_memoria=en_memoria,
            origenes=_origenes_de_config(agent_cfg.id),
            paths_secretos=paths_secretos(),
            paths_runtime=overrides.paths_runtime(agent_cfg.id) if overrides else frozenset(),
        )
    )


def build_config_set_tool(
    agent_id: str,
    overrides: OverridesDeRuntime,
    reloader: ISolicitanteDeReload,
    verificar_construccion: Callable[[GlobalConfig, list[AgentConfig]], None],
    get_channel_context: Callable[[], ChannelContext | None],
    tiene_outbound: Callable[[str], bool],
    *,
    config_dir: Path | None = None,
    agents_dir: Path | None = None,
) -> ConfigSetTool:
    """La tool ``config_set`` de UN agente, con el validador que encadena el loader y los adapters.

    ``validar`` es el carril del arranque entero: ``load_global_config`` + el
    ``AgentRegistry`` de TODOS los agentes (un override global que rompa a otro agente
    se rechaza) con la capa candidata aplicada, y después ``verificar_construccion``.
    Esa segunda etapa la arma el composition root con las factories de LLM y embedding
    (``config`` no puede importarlas) y la recibe inyectada.

    Un fallo de la etapa 2 sale como ``ConstruccionRechazadaError`` para que el rechazo
    diga que fue el adapter y no el schema. Lo que ni el loader ni la verificación ven
    (transcripción, fuentes de knowledge, arranque de un canal) falla recién al
    reconstruir el daemon: ese fallo sale con exit 1 y pierde los overrides.

    ``config_dir``/``agents_dir`` salen del home de la instancia salvo que se pasen.
    """
    from inaki.config.loader import AgentRegistry, load_global_config

    home = get_inaki_home()
    config_dir = config_dir or home / "config"
    agents_dir = agents_dir or home / "agents"

    def validar(candidato: OverridesDeRuntime) -> dict[str, Any]:
        global_cfg, global_raw = load_global_config(config_dir, runtime=candidato.capa_global())
        registry = AgentRegistry(
            agents_dir, global_raw, runtime_por_agente=candidato.capas_por_agente()
        )
        try:
            verificar_construccion(global_cfg, registry.list_all())
        except ConstruccionRechazadaError:
            raise
        except errores_del_operador() as exc:
            raise ConstruccionRechazadaError(str(exc)) from exc
        agent_cfg = registry.get(agent_id)
        if agent_cfg is None:
            raise ConfigError(f"El agente '{agent_id}' ya no existe en {agents_dir}.")
        return deep_merge(global_cfg.model_dump(), agent_cfg.model_dump())

    return ConfigSetTool(
        GestionarOverridesUseCase(
            agent_id=agent_id,
            overrides=overrides,
            reloader=reloader,
            validar=validar,
            paths_fijos=paths_fijos_en_runtime(),
        ),
        get_channel_context=get_channel_context,
        tiene_outbound=tiene_outbound,
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
