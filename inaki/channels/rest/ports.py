"""Interfaces estructurales que el admin REST necesita de los containers.

Declaradas acá (el consumidor define lo que requiere) para que el admin server
no importe ``inaki.app.container``. El ``AgentContainer`` / ``AppContainer``
reales las satisfacen por duck-typing — la dirección hexagonal queda intacta
(un canal NO importa el composition root).

``agent_config`` y ``_tools`` se exponen como ``@property`` read-only a propósito:
el atributo concreto es un subtipo (``AgentConfig`` / ``ToolRegistry``) y un
miembro de Protocol mutable sería invariante, rechazando el subtipo. Read-only
los hace covariantes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from inaki.kernel.domain.services.channel_outbound_registry import ChannelOutboundRegistry
    from inaki.kernel.ports.outbound.scope_registry_port import IScopeRegistry
    from inaki.kernel.ports.outbound.tool_port import IToolExecutor
    from inaki.kernel.use_cases.run_agent import RunAgentUseCase
    from inaki.memory.use_cases.consolidate_memory import ConsolidateMemoryUseCase


class _HasChannels(Protocol):
    """Subset estructural de ``AgentConfig``.

    Los valores de ``channels`` son los modelos ya validados del schema; el
    admin no los importa (los canales son independientes entre sí y el REST no
    conoce el de Telegram), así que declara acá la forma que consume.
    """

    channels: dict[str, Any]


class AdminAgentContainer(Protocol):
    """Lo que los routers del admin acceden de un container de agente resuelto."""

    run_agent: RunAgentUseCase
    consolidate_memory: ConsolidateMemoryUseCase | None
    scope_registry: IScopeRegistry
    channel_outbound_registry: ChannelOutboundRegistry

    @property
    def agent_config(self) -> _HasChannels: ...

    @property
    def _tools(self) -> IToolExecutor: ...


class AdminAppContainer(Protocol):
    """Lo que ``create_admin_app`` recibe — el resto se accede vía ``app.state`` (Any)."""

    agents: dict[str, AdminAgentContainer]
