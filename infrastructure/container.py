"""
Container de inyección de dependencias.

AgentContainer — instancia todos los adaptadores para un agente concreto.
AppContainer — container raíz, carga todos los agentes al arrancar.

Este es el ÚNICO lugar donde se instancian adaptadores concretos.
"""

from __future__ import annotations

import logging

from adapters.outbound.history.sqlite_history_store import SQLiteHistoryStore
from adapters.outbound.memory.sqlite_memory_repo import SQLiteMemoryRepository
from adapters.outbound.scheduler.dispatch_adapters import (
    ChannelSenderAdapter,
    LLMDispatcherAdapter,
    SchedulerDispatchPorts,
)
from adapters.outbound.scheduler.sqlite_scheduler_repo import SQLiteSchedulerRepo
from adapters.outbound.skills.yaml_skill_repo import YamlSkillRepository
from adapters.outbound.tools.tool_registry import ToolRegistry
from core.domain.errors import AgentNotFoundError
from core.domain.services.scheduler_service import SchedulerService
from core.use_cases.consolidate_memory import ConsolidateMemoryUseCase
from core.use_cases.run_agent import RunAgentUseCase
from core.use_cases.schedule_task import ScheduleTaskUseCase
from infrastructure.config import AgentConfig, AgentRegistry, GlobalConfig
from infrastructure.factories.embedding_factory import EmbeddingProviderFactory
from infrastructure.factories.llm_factory import LLMProviderFactory

logger = logging.getLogger(__name__)


class AgentContainer:
    """Container de dependencias para un agente concreto."""

    def __init__(self, agent_config: AgentConfig, global_config: GlobalConfig) -> None:
        cfg = agent_config

        # Factories resuelven el proveedor correcto leyendo cfg.embedding.provider y cfg.llm.provider
        self._embedder = EmbeddingProviderFactory.create(cfg)
        self._memory = SQLiteMemoryRepository(cfg.memory.db_path, self._embedder)
        self._llm = LLMProviderFactory.create(cfg)
        self._skills = YamlSkillRepository(
            skills_dir=global_config.app.skills_dir,
            embedder=self._embedder,
        )
        self._history = SQLiteHistoryStore(cfg.history)
        self._tools = ToolRegistry(embedder=self._embedder)
        self._register_tools()

        self.run_agent = RunAgentUseCase(
            llm=self._llm,
            memory=self._memory,
            embedder=self._embedder,
            skills=self._skills,
            history=self._history,
            tools=self._tools,
            agent_config=cfg,
        )

        self.consolidate_memory = ConsolidateMemoryUseCase(
            llm=self._llm,
            memory=self._memory,
            embedder=self._embedder,
            history=self._history,
            agent_id=cfg.id,
        )

    def _register_tools(self) -> None:
        """Registra las tools disponibles para este agente."""
        from adapters.outbound.tools.exchange_calendar_tool import ExchangeCalendarTool
        from adapters.outbound.tools.patch_file_tool import PatchFileTool
        from adapters.outbound.tools.read_file_tool import ReadFileTool
        from adapters.outbound.tools.shell_tool import ShellTool
        from adapters.outbound.tools.web_search_tool import WebSearchTool
        from adapters.outbound.tools.write_file_tool import WriteFileTool

        self._tools.register(ShellTool())
        self._tools.register(WebSearchTool())
        self._tools.register(ReadFileTool())
        self._tools.register(WriteFileTool())
        self._tools.register(PatchFileTool())
        self._tools.register(ExchangeCalendarTool())


class AppContainer:
    """Container raíz. Carga todos los agentes al arrancar."""

    def __init__(self, global_config: GlobalConfig, registry: AgentRegistry) -> None:
        self.global_config = global_config
        self.registry = registry
        self.agents: dict[str, AgentContainer] = {}

        for agent_cfg in registry.list_all():
            try:
                self.agents[agent_cfg.id] = AgentContainer(agent_cfg, global_config)
                logger.info("AgentContainer creado para '%s'", agent_cfg.id)
            except Exception as exc:
                logger.error(
                    "Error creando container para agente '%s': %s", agent_cfg.id, exc
                )

        # Scheduler wiring
        scheduler_cfg = global_config.scheduler
        self.scheduler_repo = SQLiteSchedulerRepo(scheduler_cfg.db_path)
        self.schedule_task_uc = ScheduleTaskUseCase(
            repo=self.scheduler_repo,
            on_mutation=self._on_scheduler_mutation,
        )
        dispatch_ports = SchedulerDispatchPorts(
            channel_sender=ChannelSenderAdapter(self),
            llm_dispatcher=LLMDispatcherAdapter(self.agents),
        )
        self.scheduler_service = SchedulerService(
            repo=self.scheduler_repo,
            dispatch=dispatch_ports,
            config=scheduler_cfg,
        )

    def _on_scheduler_mutation(self) -> None:
        self.scheduler_service.invalidate()

    async def startup(self) -> None:
        """Arranca el scheduler service. Llamar en el daemon lifecycle."""
        if self.global_config.scheduler.enabled:
            await self.scheduler_service.start()
            logger.info("SchedulerService iniciado")

    async def shutdown(self) -> None:
        """Detiene el scheduler service graciosamente."""
        await self.scheduler_service.stop()
        logger.info("SchedulerService detenido")

    def get_agent(self, agent_id: str) -> AgentContainer:
        if agent_id not in self.agents:
            raise AgentNotFoundError(
                f"Agente '{agent_id}' no encontrado o falló al inicializar. "
                f"Disponibles: {list(self.agents)}"
            )
        return self.agents[agent_id]
