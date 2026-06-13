"""Interfaces estructurales que el admin REST necesita de los containers.

Declaradas acá (el consumidor define lo que requiere) para que el admin server
no importe ``infrastructure.container``. El ``AgentContainer`` / ``AppContainer``
reales las satisfacen por duck-typing — la dirección hexagonal queda intacta
(``adapters`` NO importa ``infrastructure``).

``agent_config`` y ``_tools`` se exponen como ``@property`` read-only a propósito:
el atributo concreto es un subtipo (``AgentConfig`` / ``ToolRegistry``) y un
miembro de Protocol mutable sería invariante, rechazando el subtipo. Read-only
los hace covariantes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from adapters.outbound.messaging.channel_outbound_registry import ChannelOutboundRegistry
    from core.ports.outbound.scope_registry_port import IScopeRegistry
    from core.ports.outbound.tool_port import IToolExecutor
    from core.use_cases.consolidate_memory import ConsolidateMemoryUseCase
    from core.use_cases.run_agent import RunAgentUseCase


class _HasChannels(Protocol):
    channels: dict[str, dict[str, Any]]


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
