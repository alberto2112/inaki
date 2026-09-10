"""Los runtimes: lo que el composition root entrega, ya ensamblado, tipado e inmutable.

``AgentRuntime`` es un agente listo (tier per-agente); ``HarnessRuntime`` es el
proceso entero (tier harness-global) con sus agentes, canales y servicios.
Los construye ``inaki.app.assembly.ensamblar`` UNA vez, al final, cuando todo
existe: ningún consumidor vuelve a preguntar ``getattr(x, "y", None)`` para
saber si algo "ya se wireó". Un campo ``X | None`` significa que la capacidad
no está configurada para ese agente (sub-agente, sin token, fotos apagadas),
nunca que falta una pasada.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from inaki.agents.delegation.background_queue import BackgroundDelegationQueueAdapter
from inaki.agents.dispatcher import LLMDispatcherAdapter
from inaki.app.reloader import DaemonReloader
from inaki.channels.telegram.bot import TelegramBot
from inaki.channels.telegram.broadcast.egress import BroadcastEgress
from inaki.channels.telegram.broadcast.rate_limiter import FixedWindowRateLimiter
from inaki.channels.telegram.broadcast.tcp import TcpBroadcastAdapter
from inaki.channels.telegram.files.ports import IFileDownloader, IFileRecordRepo
from inaki.config import AgentConfig, AgentRegistry, GlobalConfig
from inaki.kernel.domain.channel_outbound_registry import ChannelOutboundRegistry
from inaki.kernel.domain.channel_router import ChannelRouter
from inaki.kernel.ports.channel_port import IChannel
from inaki.kernel.ports.embedding_port import IEmbeddingProvider
from inaki.kernel.ports.llm_port import ILLMProvider
from inaki.kernel.ports.memory_port import IMemoryRepository
from inaki.kernel.ports.scope_registry_port import IScopeRegistry
from inaki.kernel.ports.tool_config_port import IToolConfigStore
from inaki.kernel.ports.turn_tracer_port import ITurnTracer
from inaki.kernel.conversation_history import ConversationHistory
from inaki.kernel.run_agent import RunAgentUseCase
from inaki.kernel.run_agent_one_shot import RunAgentOneShotUseCase
from inaki.knowledge.wiring import KnowledgeBundle
from inaki.memory.adapters.sqlite_history_store import SQLiteHistoryStore
from inaki.memory.use_cases.consolidate_all_agents import ConsolidateAllAgentsUseCase
from inaki.memory.use_cases.consolidate_memory import ConsolidateMemoryUseCase
from inaki.memory.use_cases.reconcile_memory import ReconcileMemoryUseCase
from inaki.perception.use_cases.process_photo import ProcessPhotoUseCase
from inaki.perception.use_cases.transcribe_audio import TranscribeAudioUseCase
from inaki.scheduler.ports.use_case import IManualTaskRunner
from inaki.scheduler.use_cases.schedule_task import ScheduleTaskUseCase
from inaki.scheduler.wiring import SchedulerBundle, reconciliar_builtins
from inaki.shared.errors import AgentNotFoundError
from inaki.skills.yaml_skill_repo import YamlSkillRepository
from inaki.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentRuntime:
    """Un agente ensamblado. Los nombres son los que ya consumen el bot, REST y el
    scheduler (``FuentesDelBot``, ``AdminAgentRuntime``, ``LLMDispatcherAdapter``)."""

    # Identidad y kernel
    agent_config: AgentConfig
    run_agent: RunAgentUseCase
    run_agent_one_shot: RunAgentOneShotUseCase
    scope_registry: IScopeRegistry
    tracer: ITurnTracer

    # Módulos (pasada 1: se construyen con la config del agente)
    llm: ILLMProvider
    embedder: IEmbeddingProvider
    memory: IMemoryRepository
    history_store: SQLiteHistoryStore
    history: ConversationHistory
    skills: YamlSkillRepository
    tools: ToolRegistry
    knowledge: KnowledgeBundle
    consolidate_memory: ConsolidateMemoryUseCase | None
    reconcile_memory: ReconcileMemoryUseCase | None
    transcribe_audio: TranscribeAudioUseCase | None
    channel_outbound_registry: ChannelOutboundRegistry

    # Wired por el harness (pasada 3). ``None`` por CONFIG, no por orden.
    schedule_task: ScheduleTaskUseCase | None
    manual_task_runner: IManualTaskRunner | None
    process_photo: ProcessPhotoUseCase | None
    broadcast_egress: BroadcastEgress | None
    broadcast_adapter: TcpBroadcastAdapter | None
    group_rate_limiter: FixedWindowRateLimiter | None
    telegram_file_repo: IFileRecordRepo | None
    telegram_file_downloader: IFileDownloader | None


@dataclass(frozen=True)
class HarnessRuntime:
    """El proceso: tier harness-global + los agentes + los canales."""

    global_config: GlobalConfig
    registry: AgentRegistry
    agents: Mapping[str, AgentRuntime]
    channels: tuple[IChannel, ...]
    scheduler: SchedulerBundle
    background_queue: BackgroundDelegationQueueAdapter
    consolidate_all: ConsolidateAllAgentsUseCase
    router: ChannelRouter
    dispatcher: LLMDispatcherAdapter
    reloader: DaemonReloader
    tool_config_store: IToolConfigStore
    scope_registry: IScopeRegistry
    tracer: ITurnTracer
    telegram_bots: Mapping[str, TelegramBot]

    def get_agent(self, agent_id: str) -> AgentRuntime:
        if agent_id not in self.agents:
            raise AgentNotFoundError(
                f"Agente '{agent_id}' no encontrado o falló al inicializar. "
                f"Disponibles: {list(self.agents)}"
            )
        return self.agents[agent_id]

    async def startup(self) -> None:
        """Arranca el scheduler (tras sembrar sus builtins) y la cola de background.
        Los canales (bots, broadcast) los arranca el runner vía ``channels``."""
        if self.global_config.scheduler.enabled:
            await self._reconciliar_builtins()
            await self.scheduler.service.start()
            logger.info("SchedulerService iniciado")
        await self.background_queue.start()
        logger.info("BackgroundDelegationQueue iniciada")

    async def shutdown(self) -> None:
        await self.background_queue.stop()
        logger.info("BackgroundDelegationQueue detenida")
        await self.scheduler.service.stop()
        logger.info("SchedulerService detenido")

    async def _reconciliar_builtins(self) -> None:
        """QUÉ reconciliar lo decide el harness; CÓMO, el módulo scheduler."""
        photos_cfg = getattr(self.global_config, "photos", None)
        face_dedup: tuple[str, str] | None = None
        if photos_cfg is not None and photos_cfg.enabled and photos_cfg.dedup.enabled:
            agent_id = next(
                (aid for aid, a in self.agents.items() if a.process_photo is not None), None
            )
            if agent_id is None:
                logger.warning("face_dedup_nightly: no hay agentes con photos wired — omitido")
            else:
                face_dedup = (photos_cfg.dedup.schedule, agent_id)
        await reconciliar_builtins(
            self.scheduler,
            consolidation_schedule=self.global_config.memories.consolidation.schedule,
            reconciliaciones=[
                (cfg.id, cfg.memories.reconciliation.schedule)
                for cfg in self.registry.list_all()
                if not self.registry.is_sub_agent(cfg.id) and cfg.memories.reconciliation.enabled
            ],
            face_dedup=face_dedup,
        )
