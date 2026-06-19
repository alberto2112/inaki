"""
Contenedor DI liviano para la TUI de setup.

INVARIANTE OFFLINE-ONLY: Este módulo NO instancia ni importa nada relacionado
con LLM, embedding, memoria vectorial, daemon TCP ni schedulers.
Su única función es cablear los use cases de config contra YamlRepository.

Usá este módulo en lugar de ``infrastructure/container.py`` para la TUI —
AppContainer levanta LLM y embedding, lo que es innecesario y lento aquí.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from adapters.outbound.config_repository.yaml_repository import YamlRepository
from core.use_cases.config.create_agent import CreateAgentUseCase
from core.use_cases.config.delete_agent import DeleteAgentUseCase
from core.use_cases.config.delete_provider import DeleteProviderUseCase
from core.use_cases.config.get_effective_config import GetEffectiveConfigUseCase
from core.use_cases.config.list_agents import ListAgentsUseCase
from core.use_cases.config.list_providers import ListProvidersUseCase
from core.use_cases.config.update_agent_layer import UpdateAgentLayerUseCase
from core.use_cases.config.update_global_layer import UpdateGlobalLayerUseCase
from core.use_cases.config.upsert_provider import UpsertProviderUseCase


@dataclass(frozen=True)
class SetupContainer:
    """
    Contenedor offline con todos los use cases de config.

    Todos los use cases comparten la misma instancia de ``YamlRepository``.
    No hay LLM, embedding ni daemon — la TUI puede lanzarse sin conexión
    y sin que el daemon esté corriendo.
    """

    repo: YamlRepository
    get_effective_config: GetEffectiveConfigUseCase
    list_agents: ListAgentsUseCase
    create_agent: CreateAgentUseCase
    delete_agent: DeleteAgentUseCase
    update_global_layer: UpdateGlobalLayerUseCase
    update_agent_layer: UpdateAgentLayerUseCase
    list_providers: ListProvidersUseCase
    upsert_provider: UpsertProviderUseCase
    delete_provider: DeleteProviderUseCase
    # Clases de schema Pydantic inyectadas por el composition root (inaki/).
    # Los screens las usan para introspección (``build_schema_tree``) y
    # validación — así el setup_tui NO importa ``infrastructure.config``.
    global_schema: type[BaseModel]
    agent_schema: type[BaseModel]
    # Registry ``nombre_canal → modelo`` para resolver el dict ``channels`` del
    # agente (``AgentConfig.channels`` es ``dict[str, dict]``, no introspectable
    # solo). Lo inyecta el composition root; el árbol de schema lo usa para tratar
    # cada canal como sub-sección tipada. Vacío = sin tratamiento especial.
    channel_schemas: dict[str, type[BaseModel]]
    # Choices dinámicos por NOMBRE de campo, para campos cuyo conjunto de valores
    # válidos se conoce en runtime pero no está en el schema como ``Literal``
    # (ej. ``provider`` → adaptadores autodescubiertos por las factories). El
    # composition root los computa e inyecta; el árbol marca esos campos como enum.
    dynamic_enums: dict[str, tuple[str, ...]]


def build_setup_container(
    config_dir: Path | None,
    global_schema: type[BaseModel],
    agent_schema: type[BaseModel],
    channel_schemas: dict[str, type[BaseModel]] | None = None,
    dynamic_enums: dict[str, tuple[str, ...]] | None = None,
) -> SetupContainer:
    """
    Construye el contenedor offline para la TUI de setup.

    Args:
        config_dir: Directorio raíz de config. ``None`` → usa el default
                    (``~/.inaki/config/`` o ``$INAKI_HOME/config/`` por env var).
        global_schema: clase Pydantic ``GlobalConfig`` — la inyecta el
                       composition root (el setup_tui no importa infrastructure).
        agent_schema: clase Pydantic ``AgentConfig`` — ídem.
        channel_schemas: Registry ``nombre_canal → modelo`` (ej.
                       ``{"telegram": TelegramChannelConfig}``) para resolver el
                       dict ``channels`` del agente. ``None`` → ``{}``.
        dynamic_enums: Choices por nombre de campo (ej. ``{"provider": (...)}``)
                       computados en runtime. ``None`` → ``{}``.

    Returns:
        ``SetupContainer`` con todos los use cases cableados.
    """
    repo = YamlRepository(config_dir=config_dir)

    return SetupContainer(
        repo=repo,
        get_effective_config=GetEffectiveConfigUseCase(repo),
        list_agents=ListAgentsUseCase(repo),
        create_agent=CreateAgentUseCase(repo),
        delete_agent=DeleteAgentUseCase(repo),
        update_global_layer=UpdateGlobalLayerUseCase(repo),
        update_agent_layer=UpdateAgentLayerUseCase(repo),
        list_providers=ListProvidersUseCase(repo),
        upsert_provider=UpsertProviderUseCase(repo),
        delete_provider=DeleteProviderUseCase(repo),
        global_schema=global_schema,
        agent_schema=agent_schema,
        channel_schemas=channel_schemas or {},
        dynamic_enums=dynamic_enums or {},
    )
