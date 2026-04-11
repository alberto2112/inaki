"""
Container de inyección de dependencias.

AgentContainer — instancia todos los adaptadores para un agente concreto.
AppContainer — container raíz, carga todos los agentes al arrancar.

Este es el ÚNICO lugar donde se instancian adaptadores concretos.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from croniter import croniter
from pydantic import ValidationError

from adapters.outbound.history.sqlite_history_store import SQLiteHistoryStore
from adapters.outbound.memory.sqlite_memory_repo import SQLiteMemoryRepository
from adapters.outbound.scheduler.builtin_tasks import (
    CONSOLIDATE_MEMORY_TASK_ID,
    build_consolidate_memory_task,
)
from adapters.outbound.scheduler.dispatch_adapters import (
    ChannelSenderAdapter,
    ConsolidationDispatchAdapter,
    LLMDispatcherAdapter,
    SchedulerDispatchPorts,
)
from adapters.outbound.scheduler.sqlite_scheduler_repo import SQLiteSchedulerRepo
from adapters.outbound.skills.yaml_skill_repo import YamlSkillRepository
from adapters.outbound.tools.tool_registry import ToolRegistry
from core.domain.entities.task import TaskStatus
from core.domain.errors import AgentNotFoundError
from core.domain.services.scheduler_service import SchedulerService
from core.use_cases.consolidate_all_agents import ConsolidateAllAgentsUseCase
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
        self.agent_config = agent_config

        # Factories resuelven el proveedor correcto leyendo cfg.embedding.provider y cfg.llm.provider
        self._embedder = EmbeddingProviderFactory.create(cfg)
        self._memory = SQLiteMemoryRepository(cfg.memory.db_path, self._embedder)
        self._llm = LLMProviderFactory.create(cfg)
        self._skills = YamlSkillRepository(embedder=self._embedder)
        self._history = SQLiteHistoryStore(cfg.chat_history)
        self._tools = ToolRegistry(embedder=self._embedder)
        self._register_tools()
        self._register_extensions(global_config.app.ext_dirs)

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
            memory_config=cfg.memory,
        )

    def _register_tools(self) -> None:
        """Registra tools built-in del núcleo. Las extensiones se cargan aparte."""
        from pathlib import Path

        from adapters.outbound.tools.patch_file_tool import PatchFileTool
        from adapters.outbound.tools.read_file_tool import ReadFileTool
        from adapters.outbound.tools.web_search_tool import WebSearchTool
        from adapters.outbound.tools.write_file_tool import WriteFileTool

        ws_cfg = self.agent_config.workspace
        workspace_path = Path(ws_cfg.path).expanduser().resolve()
        try:
            workspace_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error(
                "No se pudo crear el workspace '%s' para el agente '%s': %s",
                workspace_path, self.agent_config.id, exc,
            )
            raise
        logger.info(
            "Agente '%s': workspace='%s' containment='%s'",
            self.agent_config.id, workspace_path, ws_cfg.containment,
        )

        self._tools.register(WebSearchTool())
        self._tools.register(ReadFileTool(workspace=workspace_path, containment=ws_cfg.containment))
        self._tools.register(WriteFileTool(workspace=workspace_path, containment=ws_cfg.containment))
        self._tools.register(PatchFileTool(workspace=workspace_path, containment=ws_cfg.containment))

    def _register_extensions(self, ext_dirs: list[str]) -> None:
        """
        Auto-discovery de extensiones de usuario.

        Itera sobre cada directorio en ext_dirs en orden, escanea */manifest.py,
        y registra TOOLS + SKILLS declarados. Usa spec_from_file_location para
        cargar por path absoluta sin dependencia de sys.path para el manifest.
        Añade el parent de cada ext_dir a sys.path para que los imports internos
        del engine de cada extensión resuelvan.
        """
        import importlib.util
        import sys
        from pathlib import Path

        for ext_dir_str in ext_dirs:
            ext_dir = Path(ext_dir_str).expanduser().resolve()

            if not ext_dir.exists() or not ext_dir.is_dir():
                logger.debug("Directorio de extensiones no encontrado: %s", ext_dir)
                continue

            # Añadir parent al sys.path para que los imports del engine resuelvan
            parent_str = str(ext_dir.parent)
            if parent_str not in sys.path:
                sys.path.insert(0, parent_str)
                logger.debug("sys.path += %s (extensiones en %s)", parent_str, ext_dir.name)

            for manifest_path in sorted(ext_dir.glob("*/manifest.py")):
                ext_name = manifest_path.parent.name
                # ID único para evitar colisión entre extensiones de mismo nombre en dirs distintos
                module_id = f"_inaki_ext_{ext_dir.name}_{ext_name}_manifest"

                try:
                    spec = importlib.util.spec_from_file_location(module_id, manifest_path)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                except Exception as exc:
                    logger.warning(
                        "Extensión '%s': falló al cargar manifest (%s) — skipping",
                        ext_name, exc,
                    )
                    continue

                # Registrar tools
                for tool_cls in getattr(module, "TOOLS", []) or []:
                    try:
                        tool_instance = tool_cls()
                        # Verificar colisión de nombres antes de registrar
                        if tool_instance.name in self._tools._tools:
                            logger.warning(
                                "Extensión '%s': tool '%s' ya registrada — skipping (colisión)",
                                ext_name, tool_instance.name,
                            )
                            continue
                        self._tools.register(tool_instance)
                        logger.info(
                            "Extensión '%s': tool '%s' registrada",
                            ext_name, tool_instance.name,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Extensión '%s': falló al instanciar %r (%s) — skipping tool",
                            ext_name, tool_cls, exc,
                        )

                # Registrar skills
                for skill_rel in getattr(module, "SKILLS", []) or []:
                    skill_path = (manifest_path.parent / skill_rel).resolve()
                    if not skill_path.exists():
                        logger.warning(
                            "Extensión '%s': skill file no encontrado: %s",
                            ext_name, skill_path,
                        )
                        continue
                    self._skills.add_file(skill_path)
                    logger.info(
                        "Extensión '%s': skill '%s' añadida",
                        ext_name, skill_path.name,
                    )


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

        # Global consolidation use case — itera agentes habilitados con delay
        enabled_consolidators: dict[str, ConsolidateMemoryUseCase] = {
            agent_id: container.consolidate_memory
            for agent_id, container in self.agents.items()
            if container.agent_config.memory.enabled
        }
        self.consolidate_all_agents = ConsolidateAllAgentsUseCase(
            enabled_agents=enabled_consolidators,
            delay_seconds=global_config.memory.delay_seconds,
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
            consolidator=ConsolidationDispatchAdapter(self.consolidate_all_agents),
        )
        self.scheduler_service = SchedulerService(
            repo=self.scheduler_repo,
            dispatch=dispatch_ports,
            config=scheduler_cfg,
        )

    def _on_scheduler_mutation(self) -> None:
        self.scheduler_service.invalidate()

    async def _reconcile_consolidate_memory_task(self) -> None:
        """
        Garantiza que la tarea builtin `consolidate_memory` en la DB refleja
        la config actual:
          - no existe → seed con schedule de config + next_run computado
          - schedule cambió en config → update + recompute next_run
          - status = FAILED (arrastre de corridas viejas rotas) → reset a pending
          - next_run NULL → recompute
        """
        target_schedule = self.global_config.memory.schedule
        target = build_consolidate_memory_task(target_schedule)

        await self.scheduler_repo.ensure_schema()

        # get_task puede fallar con ValidationError si en la DB quedó un payload
        # de un schema viejo (p.ej. 'cli_command' de la era previa al refactor).
        # En ese caso tratamos la fila como stale: la borramos y seedamos limpio.
        try:
            existing = await self.scheduler_repo.get_task(CONSOLIDATE_MEMORY_TASK_ID)
        except ValidationError as exc:
            logger.warning(
                "Tarea builtin consolidate_memory tiene payload stale en DB "
                "(esquema viejo) — borrando y reseedando: %s",
                exc,
            )
            await self.scheduler_repo.delete_task(CONSOLIDATE_MEMORY_TASK_ID)
            existing = None

        if existing is None:
            # seed_builtin computa next_run si es recurrente y viene None
            await self.scheduler_repo.seed_builtin(target)
            logger.info(
                "Tarea builtin consolidate_memory sembrada con schedule '%s'",
                target_schedule,
            )
            return

        now = datetime.now(timezone.utc)
        needs_save = False
        new_schedule = existing.schedule
        new_next_run = existing.next_run
        new_status = existing.status
        new_retry = existing.retry_count

        if existing.schedule != target_schedule:
            new_schedule = target_schedule
            new_next_run = datetime.fromtimestamp(
                croniter(target_schedule, now).get_next(), tz=timezone.utc
            )
            logger.info(
                "consolidate_memory: schedule actualizado '%s' → '%s'",
                existing.schedule,
                target_schedule,
            )
            needs_save = True

        if new_status == TaskStatus.FAILED:
            new_status = TaskStatus.PENDING
            new_retry = 0
            if new_next_run is None or new_next_run <= now:
                new_next_run = datetime.fromtimestamp(
                    croniter(new_schedule, now).get_next(), tz=timezone.utc
                )
            logger.info(
                "consolidate_memory: estado FAILED reseteado a PENDING (next_run=%s)",
                new_next_run,
            )
            needs_save = True

        if new_next_run is None:
            new_next_run = datetime.fromtimestamp(
                croniter(new_schedule, now).get_next(), tz=timezone.utc
            )
            logger.info("consolidate_memory: next_run era NULL → recomputado a %s", new_next_run)
            needs_save = True

        if needs_save:
            updated = existing.model_copy(update={
                "schedule": new_schedule,
                "next_run": new_next_run,
                "status": new_status,
                "retry_count": new_retry,
            })
            await self.scheduler_repo.save_task(updated)

    async def startup(self) -> None:
        """Arranca el scheduler service. Llamar en el daemon lifecycle."""
        if self.global_config.scheduler.enabled:
            await self._reconcile_consolidate_memory_task()
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
