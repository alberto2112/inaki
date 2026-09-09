"""
Composition root (transitorio): orquesta los ``wiring.py`` de cada módulo.

AgentContainer — arma un agente llamando al wiring de cada módulo en orden.
AppContainer — container raíz: el tier harness-global y las pasadas cruzadas.

Ningún adapter se instancia acá: cada módulo sabe ensamblarse a sí mismo en
su ``wiring.py`` (config → adapters, use cases, tools) y este fichero solo
decide el ORDEN y reparte lo que un módulo necesita de otro. La fase 9c lo
disuelve en ``inaki/app/assembly.py`` con runtimes tipados.
"""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path
from collections.abc import Sequence
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from inaki.kernel.ports.outbound.background_delegation_port import IBackgroundDelegationQueue
    from inaki.channels.telegram.files.ports import IFileDownloader, IFileRecordRepo
    from inaki.perception.use_cases.process_photo import ProcessPhotoUseCase
    from inaki.shared.channel_context import ChannelContext

from inaki.agents.dispatcher import LLMDispatcherAdapter
from inaki.agents.scope_registry import InMemoryScopeRegistryAdapter
from inaki.agents.wiring import (
    build_background_queue,
    build_delegate_tool,
    build_discovery_section,
    build_ephemeral_child,
)
from inaki.app.extensions import registrar_extensiones
from inaki.app.settings import build_one_shot_settings, build_run_agent_settings
from inaki.channels.telegram.files.ports import IFileRecordRepo
from inaki.channels.telegram.wiring import (
    TelegramAgentResources,
    build_broadcast,
    build_channel,
    build_telegram_bot_ports,
    build_telegram_file_repo,
    build_telegram_outbound,
    build_telegram_tools,
)
from inaki.perception.wiring import (
    build_photos_singletons,
    PhotosSingletons,
    build_photos_for_agent,
    build_transcribe_audio,
)
from inaki.scheduler.wiring import (
    build_dispatch_ports,
    build_scheduler,
    build_scheduler_tool,
    reconciliar_builtins,
)
from inaki.config.wiring import build_config_tool
from inaki.knowledge.wiring import build_knowledge, build_knowledge_tools
from inaki.memory.wiring import (
    MemoryJobs,
    SubAgenteDeMemoria,
    build_consolidate_all,
    build_history_store,
    build_memory_jobs,
    build_memory_repo,
    build_memory_tools,
    wire_sub_agentes_de_memoria,
)
from inaki.tools.registry import ToolRegistry
from inaki.tools.wiring import build_builtin_tools, build_tool_config_store, resolver_workspace
from inaki.kernel.domain.services.channel_outbound_registry import ChannelOutboundRegistry
from inaki.kernel.domain.services.channel_router import ChannelFallbackSettings, ChannelRouter
from inaki.scheduler.ports.use_case import IManualTaskRunner
from inaki.kernel.ports.outbound.channel_port import IChannel
from inaki.kernel.ports.outbound.memory_port import IMemoryRepository
from inaki.kernel.ports.outbound.scope_registry_port import IScopeRegistry
from inaki.kernel.ports.outbound.tool_config_port import IToolConfigStore
from inaki.kernel.ports.outbound.turn_tracer_port import ITurnTracer, NullTurnTracer
from inaki.kernel.use_cases.run_agent import RunAgentUseCase
from inaki.kernel.use_cases.run_agent_one_shot import RunAgentOneShotUseCase
from inaki.scheduler.use_cases.schedule_task import ScheduleTaskUseCase
from inaki.app.reloader import DaemonReloader
from inaki.channels.telegram.broadcast.egress import BroadcastEgress
from inaki.channels.telegram.broadcast.rate_limiter import FixedWindowRateLimiter
from inaki.channels.telegram.broadcast.tcp import TcpBroadcastAdapter
from inaki.channels.telegram.config import telegram_config
from inaki.config import (
    AgentConfig,
    AgentRegistry,
    GlobalConfig,
    migrate_tool_config_to_own_file,
)
from inaki.config.home import get_inaki_home
from inaki.embedding.cache import SqliteEmbeddingCache
from inaki.memory.adapters.sqlite_history_store import (
    SQLiteHistoryStore,
)
from inaki.memory.use_cases.consolidate_memory import ConsolidateMemoryUseCase
from inaki.memory.use_cases.reconcile_memory import ReconcileMemoryUseCase
from inaki.observability import JsonlTurnTracer, is_debug_enabled, startup_event
from inaki.perception.ports.transcription import ITranscriptionProvider
from inaki.perception.use_cases.transcribe_audio import TranscribeAudioUseCase
from inaki.shared.channel_context import current_channel_context
from inaki.shared.errors import AgentNotFoundError, ConfigError, InakiError
from inaki.skills.yaml_skill_repo import YamlSkillRepository
from inaki.embedding.wiring import EmbeddingProviderFactory
from inaki.llm.wiring import LLMProviderFactory
from inaki.perception.wiring import TranscriptionProviderFactory

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mapeo config (YAML mergeado) → settings VOs de core
#
# Este es el ÚNICO punto del sistema donde el composition root traduce el schema
# user-facing a los parámetros que cada use case declara. El kernel no conoce
# AgentConfig; cada use case recibe exactamente lo que consume.
# ---------------------------------------------------------------------------


class _DescripcionDesdeContainer:
    """Adapta un ``AgentContainer`` a lo que la sección de descubrimiento lee de él."""

    def __init__(self, container: AgentContainer) -> None:
        self._c = container

    @property
    def name(self) -> str:
        return self._c.agent_config.name

    @property
    def description(self) -> str:
        return self._c.agent_config.description

    @property
    def tool_names(self) -> list[str]:
        return list(self._c._tools._tools.keys())


class AgentContainer:
    """Container de dependencias para un agente concreto."""

    def __init__(
        self,
        agent_config: AgentConfig,
        global_config: GlobalConfig,
        scope_registry: IScopeRegistry | None = None,
        tool_config_store: IToolConfigStore | None = None,
        tracer: ITurnTracer | None = None,
    ) -> None:
        cfg = agent_config
        self.agent_config = agent_config
        # Trazas del modo debug — una instancia por proceso (la crea AppContainer);
        # Null cuando el debug está apagado o en construcciones sueltas (tests).
        self._tracer: ITurnTracer = tracer or NullTurnTracer()

        # Registry compartido de scopes activos para in-flight-message-injection.
        # Si el caller no lo provee (tests directos), creamos uno local — pero la
        # idea normal es que AppContainer pase la MISMA instancia a todos los
        # agentes para que el state esté centralizado por scope.
        self.scope_registry: IScopeRegistry = scope_registry or InMemoryScopeRegistryAdapter()

        # Tool Config Protocol — store compartido entre TODOS los agentes (lo
        # construye AppContainer con el config_dir real). El fallback local es
        # solo para tests directos / arranques sueltos.
        self._tool_config_store: IToolConfigStore = tool_config_store or build_tool_config_store(
            get_inaki_home() / "config"
        )

        # Stash global_config so wire_delegation can access delegation limits (task 5.1)
        self._global_config = global_config

        # Idempotency guard for wire_delegation (task 5.1)
        self._delegation_wired: bool = False

        # Idempotency guard for wire_scheduler
        self._scheduler_wired: bool = False

        # Idempotency guard for wire_photos
        self._photos_wired: bool = False

        # Idempotency guard for wire_telegram_tools
        self._telegram_tools_wired: bool = False

        # Registry de adapters de canal saliente (ej: telegram).
        # Se puebla en wire_telegram_tools y en futuros wire_* de otros canales.
        self.channel_outbound_registry: ChannelOutboundRegistry = ChannelOutboundRegistry()

        # ScheduleTaskUseCase — wired en fase 3 por AppContainer. None hasta entonces.
        self.schedule_task: ScheduleTaskUseCase | None = None

        # IManualTaskRunner (el SchedulerService harness-global) — wired en la misma
        # fase 3, junto a schedule_task: ambos None o ambos seteados.
        self.manual_task_runner: IManualTaskRunner | None = None

        # ProcessPhotoUseCase — wired en fase 5 por AppContainer. None si photos no habilitado.
        self.process_photo: ProcessPhotoUseCase | None = None

        # IFileRecordRepo — wired en fase 7 si el agente tiene canal Telegram.
        # El bot lo lee defensivamente para persistir file_id de cada media entrante.
        self.telegram_file_repo: IFileRecordRepo | None = None
        # IFileDownloader — wired en fase 7. El bot lo usa para pre-descargar
        # media que llegue con caption (entrega un path concreto al LLM en el
        # user_input, sin depender del RAG de tools).
        self.telegram_file_downloader: IFileDownloader | None = None

        # Broadcast adapter — wired en fase 4 por AppContainer. None si el agente no
        # tiene ningún canal telegram con bloque broadcast:.
        # Tipo: TcpBroadcastAdapter | None (evitamos importar el adapter en __init__
        # para no crear dependencia circular; el tipo se declara como object).
        self.broadcast_adapter: TcpBroadcastAdapter | None = None
        # Política de emisión al LAN del agente (flags emit.*); la comparten el
        # outbound del canal y el bot. None si el agente no tiene canal telegram.
        self.broadcast_egress: BroadcastEgress | None = None
        # Rate limiter de grupos (behavior=autonomous). Vive a nivel de grupos, NO de
        # broadcast: un bot autónomo sin LAN igual lo necesita para no spammear el
        # grupo (migración groups-vs-broadcast). Lo consume group_flow y, si hay
        # broadcast, el receiver bot-to-bot.
        self.group_rate_limiter: FixedWindowRateLimiter | None = None

        # Factories resuelven el proveedor correcto leyendo cfg.embedding.provider y cfg.llm.provider
        # y componen ResolvedXConfig contra el registry top-level de providers.
        self._embedder = EmbeddingProviderFactory.create(cfg.embedding, cfg.providers)
        self._embedding_cache = SqliteEmbeddingCache(cfg.embedding.cache_filename)
        # Anotamos como el port (IMemoryRepository) en vez del adapter concreto
        # para que tests puedan inyectar fakes via Protocol estructural sin
        # error de assignment.
        self._memory: IMemoryRepository = build_memory_repo(cfg, self._embedder)
        self._llm = LLMProviderFactory.create(cfg.llm, cfg.providers)
        self._skills = YamlSkillRepository(
            embedder=self._embedder,
            cache=self._embedding_cache,
            dimension=cfg.embedding.dimension,
        )
        self._history = build_history_store(cfg)
        self._tools = ToolRegistry(
            embedder=self._embedder,
            cache=self._embedding_cache,
            dimension=cfg.embedding.dimension,
        )
        self._register_tools()
        self._register_extensions(global_config.app.ext_dirs)

        # Transcripción (voz Telegram) — se resuelve bajo reglas cruzadas con
        # channels.telegram.voice_enabled; si el agente no usa voz, queda None.
        self._transcription = self._resolve_transcription(cfg)
        # La transcripción es una capacidad de percepción: el canal recibe el use
        # case (límites + idioma + provider), no el provider pelado.
        self.transcribe_audio: TranscribeAudioUseCase | None = (
            build_transcribe_audio(self._transcription, cfg.transcription)
            if self._transcription is not None and cfg.transcription is not None
            else None
        )
        self.run_agent: RunAgentUseCase = RunAgentUseCase(
            llm=self._llm,
            memory=self._memory,
            embedder=self._embedder,
            skills=self._skills,
            history=self._history,
            tools=self._tools,
            settings=build_run_agent_settings(cfg),
            knowledge_orchestrator=self._knowledge_orchestrator,
            thinking_indicator=global_config.channels.thinking_indicator,
            scope_registry=self.scope_registry,
            tracer=self._tracer,
        )

        # Every agent gets a one-shot use case unconditionally so it can always
        # be a delegation target, regardless of whether it can INITIATE delegation.
        # (REQ-DG-1 still holds: the `delegate` tool is only registered when
        # delegation.enabled=True — see wire_delegation.)
        # Anotación explícita: el callsite en DelegateTool importa AgentContainer
        # vía TYPE_CHECKING — el round trip cae en "Cannot determine type" si
        # mypy no ve el tipo declarado acá.
        self.run_agent_one_shot: RunAgentOneShotUseCase = RunAgentOneShotUseCase(
            llm=self._llm,
            tools=self._tools,
            settings=build_one_shot_settings(cfg),
            thinking_indicator=global_config.channels.thinking_indicator,
            tracer=self._tracer,
        )

        # LLM de memoria COMPARTIDO por consolidación y reconciliación. Se resuelve
        # UNA sola vez desde `memories.llm`; en modo directo ambos jobs usan la
        # misma instancia (sin duplicar clientes HTTP). La delegación a sub-agente
        # (por job, con prompts distintos) se wirea post-construcción en
        # AppContainer._wire_memory_sub_agents.
        # Cada job es INDEPENDIENTE: se instancia solo si SU flag enabled es true.
        # AppContainer filtra por `*_memory is not None` al armar los dicts enabled.
        jobs = build_memory_jobs(
            cfg,
            base_llm=self._llm,
            memory=self._memory,
            embedder=self._embedder,
            history=self._history,
        )
        self.consolidate_memory: ConsolidateMemoryUseCase | None = jobs.consolidate
        self.reconcile_memory: ReconcileMemoryUseCase | None = jobs.reconcile

    def _register_tools(self) -> None:
        """Registra las tools built-in del agente: cada módulo arma las suyas."""
        cfg = self.agent_config
        workspace = resolver_workspace(cfg)
        knowledge = build_knowledge(
            self._global_config, cfg, memory=self._memory, embedder=self._embedder
        )
        # La lista de fuentes es la MISMA que ve el orquestador: las de nivel (3),
        # las extensiones, se añaden después sobre ella sin reconstruir nada.
        self._pending_knowledge_sources = knowledge.sources
        self._knowledge_orchestrator = knowledge.orchestrator
        self._manage_knowledge = knowledge.manage
        for tool in (
            *build_knowledge_tools(knowledge, self._embedder),
            *build_memory_tools(
                memory=self._memory, embedder=self._embedder, history=self._history, agent_id=cfg.id
            ),
            *build_builtin_tools(cfg, workspace=workspace, config_store=self._tool_config_store),
            build_config_tool(self._global_config, cfg),
        ):
            self._tools.register(tool)

    @staticmethod
    def _resolve_transcription(cfg: AgentConfig) -> ITranscriptionProvider | None:
        """Decide si crear un `ITranscriptionProvider` para este agente.

        Reglas (espejan la validación cruzada de la spec):
        - Si el agente NO tiene canal `telegram` → `None` (no hay voz posible).
        - Si `channels.telegram.voice_enabled` es explícitamente `False` → `None`.
        - Si `voice_enabled` es `True` (default cuando hay telegram) y existe
          `cfg.transcription` → crea la instancia vía factory.
        - Si `voice_enabled` está activo y `cfg.transcription` es `None` →
          error claro en bootstrap (no degradamos silenciosamente).
        """
        tg_cfg = telegram_config(cfg)
        if tg_cfg is None:
            return None

        if tg_cfg.voice_enabled is False:
            return None

        if cfg.transcription is None:
            raise InakiError(
                f"Agent '{cfg.id}': channels.telegram.voice_enabled=True requiere "
                "un bloque 'transcription:' en la config (del agente o global). "
                "Agregá `transcription:` con provider y api_key, o poné "
                "channels.telegram.voice_enabled=false para deshabilitar voz."
            )

        return TranscriptionProviderFactory.create(cfg.transcription, cfg.providers)

    @property
    def transcription(self) -> ITranscriptionProvider | None:
        """Provider de transcripción para este agente (o None si voz deshabilitada)."""
        return self._transcription

    @property
    def history(self) -> SQLiteHistoryStore:
        """Historial conversacional de este agente.

        Accesor público: el outbound del canal lo recibe por inyección y los
        adapters del scheduler resuelven por ``agent_id`` duck-typed (mismo patrón
        que ``run_agent`` con el ``LLMDispatcherAdapter``).
        """
        return self._history

    def get_channel_context(self) -> "ChannelContext | None":
        """Devuelve el ``ChannelContext`` del turno en curso, o ``None`` si no hay turno.

        Lee el ``ContextVar`` que ``RunAgentUseCase.execute`` publica al inicio de
        cada turno (task-safe: turnos concurrentes del mismo agente ven cada uno su
        propio contexto). Las tools lo consumen vía el bound method inyectado en el
        wiring — no hay estado mutable compartido entre turnos.
        """
        return current_channel_context()

    def wire_delegation(
        self,
        get_agent_container: Callable[[str], "AgentContainer | None"],
        sub_agent_ids: list[str] | None = None,
        background_queue: "IBackgroundDelegationQueue | None" = None,
        get_sub_agent_raw: Callable[[str], dict | None] | None = None,
    ) -> None:
        """
        Phase-2 wiring: registers the delegate tool when delegation is enabled.

        Must be called AFTER all AgentContainers have been constructed (two-phase
        init in AppContainer) so that get_agent_container can resolve siblings.

        No-op when:
        - delegation.enabled is False (REQ-DG-1: tool never registered → never in schemas)
        - sub_agent_ids is empty (nada para delegar)
        - called a second time (idempotency guard)
        """
        if not self.agent_config.delegation.enabled:
            return

        if self._delegation_wired:
            logger.debug(
                "AgentContainer '%s': wire_delegation ya ejecutado — skipping (idempotent)",
                self.agent_config.id,
            )
            return

        targets = sub_agent_ids or []

        if self.agent_config.delegation.allowed_targets:
            allowed = set(self.agent_config.delegation.allowed_targets)
            targets = [t for t in targets if t in allowed]

        if not targets:
            self._delegation_wired = True
            logger.debug(
                "AgentContainer '%s': wire_delegation no-op (sin sub-agentes elegibles)",
                self.agent_config.id,
            )
            return

        # Builder de la instancia efímera del hijo contra ESTE caller: cada
        # delegación construye un hijo que hereda la config del caller vía `inherit`.
        # `get_sub_agent_raw` provee el delta crudo del sub; None → no es un
        # sub-agente conocido (el tool se registra igual, solo no resuelve hijos).
        def _build_child(target_id: str) -> RunAgentOneShotUseCase | None:
            raw = get_sub_agent_raw(target_id) if get_sub_agent_raw is not None else None
            if raw is None:
                return None
            return self.build_ephemeral_child(raw)

        # `background_queue` puede ser None hasta que AppContainer la inyecte: el
        # path sync sigue funcionando y el async reporta failed (fail-fast).
        delegate_tool = build_delegate_tool(
            self._global_config,
            allowed_targets=targets,
            build_child=_build_child,
            caller_agent_id=self.agent_config.id,
            caller=self,
            queue=background_queue,
        )
        self._tools.register(delegate_tool)

        # REQ-BGD-7: propagar la cola al RunAgentUseCase para que pueda inyectar
        # la sección de delegaciones in-flight en el system prompt cada turno.
        self.run_agent.set_background_queue(background_queue)

        self._delegation_wired = True

        # -----------------------------------------------------------------------
        # Build agent-discovery section and inject into RunAgentUseCase.
        #
        # Enumera los sub-agentes disponibles y construye la sección que el LLM
        # recibirá en el system prompt para saber cuándo y cómo delegar.
        #
        # - The section is PARENT-SIDE ONLY. RunAgentOneShotUseCase (child path)
        #   is NEVER passed extra_sections — it has no _extra_system_sections attr.
        # -----------------------------------------------------------------------
        def _describir(target_id: str) -> _DescripcionDesdeContainer | None:
            target = get_agent_container(target_id)
            return _DescripcionDesdeContainer(target) if target is not None else None

        discovery_section = build_discovery_section(
            self.agent_config.id,
            targets,
            get_sub_agent_raw=get_sub_agent_raw,
            describir_agente=_describir,
        )
        if discovery_section:
            self.run_agent.set_extra_system_sections([discovery_section])
            logger.debug(
                "AgentContainer '%s': agent-discovery section injected into run_agent",
                self.agent_config.id,
            )

        logger.info(
            "AgentContainer '%s': delegation wired (sub_agents=%s)",
            self.agent_config.id,
            targets,
        )

    def build_ephemeral_child(self, definition_raw: dict) -> RunAgentOneShotUseCase:
        """Hijo efímero one-shot resuelto contra ESTE caller (ver ``inaki.agents.wiring``)."""
        return build_ephemeral_child(
            definition_raw,
            caller_cfg=self.agent_config,
            caller_llm=self._llm,
            tools=self._tools,
            tracer=self._tracer,
            thinking_indicator=self._global_config.channels.thinking_indicator,
        )

    def wire_scheduler(
        self,
        schedule_task_uc: ScheduleTaskUseCase | None,
        manual_runner: IManualTaskRunner | None,
        user_timezone: str,
    ) -> None:
        """
        Phase-3 wiring: registers the scheduler tool.

        Must be called AFTER AppContainer has constructed schedule_task_uc y
        scheduler_service (ambos dependen de scheduler_repo, disponible solo a
        nivel AppContainer). El ``manual_runner`` es el ``SchedulerService``
        harness-global: corre tareas on-demand (op ``run`` de la tool) sin tocar
        su agenda. Idempotente: segunda llamada es no-op. No-op también si
        schedule_task_uc o manual_runner son None.
        """
        if schedule_task_uc is None or manual_runner is None:
            return
        if self._scheduler_wired:
            return

        self._tools.register(
            build_scheduler_tool(
                use_case=schedule_task_uc,
                runner=manual_runner,
                agent_id=self.agent_config.id,
                user_timezone=user_timezone,
                get_channel_context=self.get_channel_context,
            )
        )
        self.schedule_task = schedule_task_uc
        self.manual_task_runner = manual_runner
        self._scheduler_wired = True
        logger.info("AgentContainer '%s': scheduler tool registrada", self.agent_config.id)

    def wire_telegram_tools(
        self,
        get_telegram_bot: Callable[[], object | None],
        telegram_file_repo: IFileRecordRepo | None,
    ) -> None:
        """Phase-7 wiring: el egress del canal y las tools que dependen del bot.

        Se llama por agente con ``channels.telegram.token``. ``get_telegram_bot``
        resuelve el bot de ESTE agente en runtime; ``telegram_file_repo`` es el
        singleton compartido (cada record lleva ``agent_id``). Idempotente.
        """
        if self._telegram_tools_wired:
            return
        tg_cfg = telegram_config(self.agent_config)
        if tg_cfg is None or not tg_cfg.token:
            self._telegram_tools_wired = True
            return
        self.channel_outbound_registry.register(
            build_telegram_outbound(
                get_telegram_bot=get_telegram_bot,
                history=self._history,
                agent_id=self.agent_config.id,
                egress=self.broadcast_egress,
            )
        )
        if telegram_file_repo is None:
            logger.warning(
                "AgentContainer '%s': telegram_file_repo es None — tools no registradas",
                self.agent_config.id,
            )
            self._telegram_tools_wired = True
            return
        armado = build_telegram_tools(
            self.agent_config,
            outbounds=self.channel_outbound_registry,
            get_telegram_bot=get_telegram_bot,
            file_repo=telegram_file_repo,
            get_channel_context=self.get_channel_context,
        )
        self.telegram_file_repo = telegram_file_repo
        self.telegram_file_downloader = armado.downloader
        for tool in armado.tools:
            self._tools.register(tool)
        self._telegram_tools_wired = True
        logger.info(
            "AgentContainer '%s': send_to_telegram + send_telegram_message + "
            "download_from_telegram registradas",
            self.agent_config.id,
        )

    def wire_photos(
        self,
        singletons: PhotosSingletons | None,
        global_config: GlobalConfig,
    ) -> None:
        """Phase-5 wiring: el use case de fotos y las face tools del agente.

        Recibe los singletons del harness (visión + registro de caras). No-op si
        photos no está habilitado o ya se wireó; si algo falla, las fotos quedan
        deshabilitadas para ESTE agente y el resto arranca normal.
        """
        if self._photos_wired:
            return
        photos_cfg = getattr(global_config, "photos", None)
        if photos_cfg is None or not photos_cfg.enabled:
            self._photos_wired = True
            return
        if singletons is None:
            logger.warning(
                "AgentContainer '%s': vision o face_registry no disponibles — "
                "photos wiring omitido",
                self.agent_config.id,
            )
            self._photos_wired = True
            return
        try:
            armado = build_photos_for_agent(
                self.agent_config,
                photos_cfg,
                singletons,
                get_channel_context=self.get_channel_context,
            )
        except Exception as exc:
            logger.error(
                "Agente '%s': el procesamiento de fotos QUEDA DESHABILITADO — %s. "
                "El resto del agente arranca normal; las fotos entrantes no se analizan.",
                self.agent_config.id,
                exc,
            )
            self._photos_wired = True
            return
        self.process_photo = armado.process_photo
        for tool in armado.tools:
            self._tools.register(tool)
        self._photos_wired = True
        logger.info(
            "AgentContainer '%s': photos wired (scene_provider=%s, %d face tools)",
            self.agent_config.id,
            photos_cfg.scene.provider,
            len(armado.tools),
        )

    def _register_extensions(self, ext_dirs: Sequence[str]) -> None:
        registrar_extensiones(
            ext_dirs,
            tools=self._tools,
            skills=self._skills,
            knowledge_sources=self._pending_knowledge_sources,
            config_store=self._tool_config_store,
            agent_cfg=self.agent_config,
            global_cfg=self._global_config,
            embedder=self._embedder,
        )


class AppContainer:
    """Container raíz. Carga todos los agentes al arrancar."""

    def __init__(
        self,
        global_config: GlobalConfig,
        registry: AgentRegistry,
        config_dir: Path | None = None,
    ) -> None:
        self.global_config = global_config
        self.registry = registry
        self.agents: dict[str, AgentContainer] = {}

        # El ORDEN de estas llamadas es el contrato (two-phase init): primero
        # poblar self.agents, luego construir dispatcher/queue/scheduler que los
        # necesitan, y al final los wirings per-agente. La secuencia explícita
        # reemplaza los viejos comentarios "Phase N" cuyos números se habían
        # desincronizado del orden real de ejecución.
        self._init_shared_state(config_dir)
        self._build_agent_containers()
        self._build_channel_router()
        self._build_background_delegation_queue()
        self._wire_all_delegation()
        self._build_consolidation()
        self._build_reconciliation()
        self._build_scheduler()
        self._wire_scheduler_tool()
        self._wire_broadcast_adapters()
        self._wire_photos()
        self._wire_telegram_tools()
        self._wire_memory_sub_agents()
        self._build_channels()

    def _init_shared_state(self, config_dir: Path | None) -> None:
        # Tool Config Protocol — UN store para toda la app, dueño de su propio
        # archivo ``config/tool_config.yaml`` (NO vive en global.secrets.yaml: ese
        # es del operador y el daemon no lo pisa). El store lo lee al construirse,
        # así la config sobrevive al reinicio. La vista en memoria se actualiza al
        # instante para todos los agentes. Migración idempotente justo antes para
        # cubrir el path de ``--config-dir`` override (que saltea ensure_user_config).
        resolved_config_dir = config_dir or get_inaki_home() / "config"
        migrate_tool_config_to_own_file(resolved_config_dir)
        self.tool_config_store: IToolConfigStore = build_tool_config_store(resolved_config_dir)

        # Registro de bots de Telegram — los registra ``_build_channels`` al construirlos
        self._telegram_bots: dict[str, object] = {}
        # Canales del daemon (``IChannel``): el runner los arranca y detiene sin
        # saber cuál es cuál. Se construyen al FINAL del init, con todo wired.
        self.channels: list[IChannel] = []

        # Coordinador de reload del daemon — lo consumen el admin REST y el bot de Telegram
        # para señalar al runner que debe reiniciar todos los channels.
        self.reloader = DaemonReloader()

        # Registry compartido de scopes activos (in-flight-message-injection).
        # Una sola instancia para TODOS los agentes — los scopes ya están
        # aislados por agent_id en la tupla `(agent_id, channel, chat_id)`.
        self.scope_registry: IScopeRegistry = InMemoryScopeRegistryAdapter()

        # Trazas de turno (modo debug): UN tracer por proceso, compartido por todos
        # los agentes; escribe en <home>/debug/turns/<agent_id>.jsonl. Con el debug
        # apagado es el Null (costo cero en el turno).
        self.turn_tracer: ITurnTracer = (
            JsonlTurnTracer(get_inaki_home() / "debug" / "turns")
            if is_debug_enabled(self.global_config.app.debug)
            else NullTurnTracer()
        )

    def _build_agent_containers(self) -> None:
        # Un AgentContainer por agente declarado. Debe correr primero: todo el
        # wiring posterior resuelve hermanos contra self.agents.
        for agent_cfg in self.registry.list_all():
            try:
                self.agents[agent_cfg.id] = AgentContainer(
                    agent_cfg,
                    self.global_config,
                    scope_registry=self.scope_registry,
                    tool_config_store=self.tool_config_store,
                    tracer=self.turn_tracer,
                )
                logger.info("AgentContainer creado para '%s'", agent_cfg.id)
            except Exception as exc:
                # Mismo criterio que ``load_agent_config``: un agente que no se
                # construye es indistinguible de uno que nunca se configuró.
                raise ConfigError(
                    f"Agente '{agent_cfg.id}': no se pudo construir su container. "
                    f"El agente quedaría declarado pero inexistente. Detalle: {exc}"
                ) from exc

    def _build_channel_router(self) -> None:
        # Router de canales — construido ANTES que la cola de background-delegation
        # porque lo comparten dos consumidores: el scheduler (channel_send /
        # agent_send) y la cola (entrega de la respuesta del padre a un [bg-N],
        # FIX bg-result-delivery). Solo necesita config + el resolver lazy de
        # bots (los bots de Telegram se registran después, al arrancar el daemon
        # — el sink los resuelve en tiempo de envío, no acá).
        scheduler_cfg = self.global_config.scheduler

        def _outbounds_de(agent_id: str | None) -> ChannelOutboundRegistry | None:
            # Resolución por DUEÑO: el envío sale por el bot y queda en el historial
            # del agente que lo agendó. Sin dueño (tarea creada desde el CLI) se usa
            # el registro del primer agente con canales, como antes hacía el sink
            # con "el primer bot" — pero sin persistir nada (el router no registra
            # historial cuando no hay agente).
            if agent_id and agent_id in self.agents:
                return self.agents[agent_id].channel_outbound_registry
            for container in self.agents.values():
                if container.channel_outbound_registry.list_channels():
                    return container.channel_outbound_registry
            return None

        self._channel_router = ChannelRouter(
            resolve_outbounds=_outbounds_de,
            fallback=ChannelFallbackSettings(
                default=scheduler_cfg.channel_fallback.default,
                overrides=dict(scheduler_cfg.channel_fallback.overrides),
            ),
            hardcoded_fallback=f"file://{scheduler_cfg.fallback_log_filename}",
        )

    def _build_background_delegation_queue(self) -> None:
        # UNA sola instancia del dispatcher entre scheduler y cola de background:
        # comparten el dict de locks por scope (REQ-BGD-6).
        self._llm_dispatcher = LLMDispatcherAdapter(self.agents)

        def _resolve_one_shot(caller_id: str, target_id: str) -> RunAgentOneShotUseCase | None:
            caller = self.agents.get(caller_id)
            raw = self.registry.get_sub_agent_raw(target_id)
            if caller is None or raw is None:
                return None
            return caller.build_ephemeral_child(raw)

        self.background_queue = build_background_queue(
            self.global_config,
            dispatcher=self._llm_dispatcher,
            one_shot_resolver=_resolve_one_shot,
            result_sender=self._channel_router,
        )

    def _wire_all_delegation(self) -> None:
        # Wire delegation AFTER all containers are built so that the
        # get_agent_container closure can resolve any sibling (two-phase init).
        # Solo los agentes regulares reciben el delegate tool; los sub-agentes son
        # el destino de la delegación (not the source) y se ejecutan one-shot.
        def _get_agent_container(agent_id: str) -> "AgentContainer | None":
            return self.agents.get(agent_id)

        sub_agent_ids = [cfg.id for cfg in self.registry.list_sub_agents()]

        for agent_id, container in self.agents.items():
            if self.registry.is_sub_agent(agent_id):
                continue
            try:
                container.wire_delegation(
                    _get_agent_container,
                    sub_agent_ids,
                    background_queue=self.background_queue,
                    get_sub_agent_raw=self.registry.get_sub_agent_raw,
                )
            except Exception as exc:
                raise ConfigError(
                    f"Agente '{agent_id}': falló el wiring de delegación. El agente "
                    f"declara 'delegation' pero no podría delegar. Detalle: {exc}"
                ) from exc

    def _build_consolidation(self) -> None:
        self.consolidate_all_agents = build_consolidate_all(
            {
                agent_id: container.consolidate_memory
                for agent_id, container in self.agents.items()
                if container.consolidate_memory is not None
            },
            delay_seconds=self.global_config.memories.consolidation.delay_seconds,
        )

    def _build_reconciliation(self) -> None:
        # Recolecta los use cases de reconciliación (per-agent, solo los habilitados).
        # El dict se pasa al ReconcileDispatchAdapter para que el scheduler
        # pueda invocar el use case correcto al disparar la task builtin.
        self._enabled_reconcilers: dict[str, ReconcileMemoryUseCase] = {
            agent_id: container.reconcile_memory
            for agent_id, container in self.agents.items()
            if container.reconcile_memory is not None
        }

    def _build_scheduler(self) -> None:
        # El router y el dispatcher son los MISMOS que usa la cola de background.
        self._scheduler = build_scheduler(
            self.global_config,
            dispatch=build_dispatch_ports(
                channel_sender=self._channel_router,
                llm_dispatcher=self._llm_dispatcher,
                consolidate_all=self.consolidate_all_agents,
                reconcilers=self._enabled_reconcilers,
            ),
        )
        self.scheduler_repo = self._scheduler.repo
        self.schedule_task_uc = self._scheduler.use_case
        self.scheduler_service = self._scheduler.service
        self._scheduler_reconciler = self._scheduler.reconciler

    def _wire_scheduler_tool(self) -> None:
        # Wire scheduler tool into each agent now that schedule_task_uc is ready.
        # Sub-agentes excluidos: se ejecutan one-shot y no programan tareas directamente.
        user_timezone = self.global_config.user.timezone
        for agent_id, container in self.agents.items():
            if self.registry.is_sub_agent(agent_id):
                continue
            try:
                container.wire_scheduler(
                    self.schedule_task_uc, self.scheduler_service, user_timezone
                )
            except Exception as exc:
                raise ConfigError(
                    f"Agente '{agent_id}': falló el wiring del scheduler. Sus tareas "
                    f"programadas no se ejecutarían. Detalle: {exc}"
                ) from exc

    def _wire_broadcast_adapters(self) -> None:
        # Wire de recursos telegram per-agente — requiere todos los containers ya
        # construidos. Por cada agente con canal telegram, _wire_broadcast_for_agent
        # resuelve el rate limiter de grupos (behavior=autonomous) y, si hay bloque
        # broadcast:, un TcpBroadcastAdapter (+ BroadcastBuffer). El lifecycle
        # (start/stop) lo gobierna el ``TelegramChannel`` del agente.
        for agent_cfg in self.registry.list_regular():
            try:
                self._wire_broadcast_for_agent(agent_cfg)
            except Exception as exc:
                # Ver nota `broadcast-arranque-observable`: este wiring resuelve el
                # transporte TCP Y el rate limiter de grupos. Degradarlo dejaba al
                # daemon arrancando sano con el puerto cerrado.
                raise ConfigError(
                    f"Agente '{agent_cfg.id}': falló el wiring de broadcast/grupos. Detalle: {exc}"
                ) from exc

    def _wire_photos(self) -> None:
        """Singletons del harness (visión + caras) y el wiring per-agente de fotos."""
        self._photos: PhotosSingletons | None = None
        photos_cfg = getattr(self.global_config, "photos", None)
        if photos_cfg is not None and photos_cfg.enabled:
            try:
                self._photos = build_photos_singletons(
                    photos_cfg, faces_db_path=self._data_db_path("faces.db")
                )
                logger.info("Photos singletons inicializados (vision=lazy)")
            except Exception as exc:
                logger.error(
                    "Reconocimiento facial y descripción de escena QUEDAN DESHABILITADOS "
                    "para TODOS los agentes — no se pudieron inicializar los singletons "
                    "de photos: %s",
                    exc,
                )
        for agent_id, container in self.agents.items():
            if self.registry.is_sub_agent(agent_id):
                continue
            try:
                container.wire_photos(self._photos, self.global_config)
            except Exception as exc:
                logger.error(
                    "Agente '%s': el procesamiento de fotos QUEDA DESHABILITADO — %s",
                    agent_id,
                    exc,
                )

    def _data_db_path(self, filename: str) -> str:
        """Un fichero junto al ``history.db`` del primer agente (el directorio de datos)."""
        first_agent = next(iter(self.agents.values()), None)
        if first_agent is None:
            return f"~/.inaki/data/{filename}"
        return str(Path(first_agent.agent_config.chat_history.db_filename).parent / filename)

    def _wire_telegram_tools(self) -> None:
        self._telegram_file_repo: IFileRecordRepo | None = None
        if any(
            (tg := telegram_config(cfg)) is not None and tg.token
            for cfg in self.registry.list_regular()
        ):
            self._telegram_file_repo = build_telegram_file_repo(
                self._data_db_path("telegram_files.db")
            )
        for agent_id, container in self.agents.items():
            if self.registry.is_sub_agent(agent_id):
                continue
            try:
                container.wire_telegram_tools(
                    partial(self._get_telegram_bot_for, agent_id),
                    self._telegram_file_repo,
                )
            except Exception as exc:
                raise ConfigError(
                    f"Agente '{agent_id}': falló el wiring de las tools de Telegram. "
                    f"El agente tiene canal telegram pero no podría usarlas. Detalle: {exc}"
                ) from exc

    def _wire_memory_sub_agents(self) -> None:
        """Conecta a cada agente regular su extractor y su reconciliador sub-agente."""
        delegation_cfg = self.global_config.delegation
        candidatos = {
            agent_id: SubAgenteDeMemoria(
                one_shot=container.run_agent_one_shot,
                system_prompt=container.agent_config.system_prompt,
            )
            for agent_id, container in self.agents.items()
        }
        for agent_id, container in self.agents.items():
            if self.registry.is_sub_agent(agent_id):
                continue
            wire_sub_agentes_de_memoria(
                agent_id,
                MemoryJobs(container.consolidate_memory, container.reconcile_memory),
                container.agent_config.memories,
                agentes=candidatos,
                es_sub_agente=self.registry.is_sub_agent,
                max_iterations=delegation_cfg.max_iterations_per_sub,
                timeout_seconds=delegation_cfg.timeout_seconds,
            )

    def _wire_broadcast_for_agent(self, agent_cfg: AgentConfig) -> None:
        """Recursos derivados de ``channels.telegram`` de un agente: rate limiter de
        grupos, egress y transporte de broadcast (ver ``channels.telegram.wiring``).
        Se omite si el agente no tiene container o no tiene canal telegram."""
        container = self.agents.get(agent_cfg.id)
        if container is None:
            return
        recursos = build_broadcast(agent_cfg)
        if recursos is None:
            return
        container.group_rate_limiter = recursos.rate_limiter
        container.broadcast_adapter = recursos.broadcast
        container.broadcast_egress = recursos.egress

    def _build_channels(self) -> None:
        """Un ``IChannel`` por agente regular con canal telegram configurado.

        Corre al final del init: el bot necesita los ports ya wireados (scheduler,
        fotos, tools, outbound). Un bot que no se puede construir se reporta como
        ``startup.resource`` con ``status=error`` y no tumba al daemon.
        """
        for agent_cfg in self.registry.list_regular():
            tg_cfg = telegram_config(agent_cfg)
            if tg_cfg is None:
                continue
            if not tg_cfg.token:
                startup_event(
                    logger,
                    "telegram_bot",
                    status="skip",
                    agent=agent_cfg.id,
                    reason="channels.telegram.token no configurado",
                )
                continue
            container = self.agents.get(agent_cfg.id)
            if container is None:
                continue
            recursos = (
                TelegramAgentResources(
                    egress=container.broadcast_egress,
                    broadcast=container.broadcast_adapter,
                    rate_limiter=container.group_rate_limiter,
                )
                if container.broadcast_egress is not None
                else None
            )
            try:
                bot, channel = build_channel(
                    agent_cfg, build_telegram_bot_ports(container), recursos, reloader=self.reloader
                )
            except ValueError as exc:
                startup_event(
                    logger, "telegram_bot", status="error", agent=agent_cfg.id, reason=str(exc)
                )
                continue
            self.register_telegram_bot(agent_cfg.id, bot)
            self.channels.append(channel)

    def register_telegram_bot(self, agent_id: str, bot: object) -> None:
        """Registra el bot de Telegram para un agente.

        Llamado por el daemon runner al arrancar cada bot. Permite que
        ChannelSenderAdapter resuelva el bot en tiempo de ejecución (lazy).
        """
        self._telegram_bots[agent_id] = bot
        logger.debug("Bot de Telegram registrado para agente '%s'", agent_id)

    def _get_telegram_bot(self) -> object | None:
        """Devuelve el primer bot de Telegram disponible, o None si no hay ninguno.

        Es el callable que se pasa a ChannelSenderAdapter para resolución lazy.
        Para uso multi-agente futuro se puede extender con agent_id como parámetro.
        """
        if not self._telegram_bots:
            return None
        return next(iter(self._telegram_bots.values()))

    def _get_telegram_bot_for(self, agent_id: str) -> object | None:
        """Devuelve el bot de Telegram registrado para ``agent_id`` o ``None``.

        Lo consume :meth:`AgentContainer.wire_telegram_tools` (resolución
        perezosa: el bot puede no estar registrado al llamarse, pero sí al
        ejecutarse la tool).
        """
        return self._telegram_bots.get(agent_id)

    async def _reconciliar_builtins(self) -> None:
        """Qué reconciliar lo decide el composition root; cómo, el módulo scheduler."""
        photos_cfg = getattr(self.global_config, "photos", None)
        face_dedup: tuple[str, str] | None = None
        if photos_cfg is not None and photos_cfg.enabled and photos_cfg.dedup.enabled:
            agent_id = next(
                (aid for aid, c in self.agents.items() if c.process_photo is not None), None
            )
            if agent_id is None:
                logger.warning("face_dedup_nightly: no hay agentes con photos wired — omitido")
            else:
                face_dedup = (photos_cfg.dedup.schedule, agent_id)
        await reconciliar_builtins(
            self._scheduler,
            consolidation_schedule=self.global_config.memories.consolidation.schedule,
            reconciliaciones=[
                (cfg.id, cfg.memories.reconciliation.schedule)
                for cfg in self.registry.list_all()
                if not self.registry.is_sub_agent(cfg.id) and cfg.memories.reconciliation.enabled
            ],
            face_dedup=face_dedup,
        )

    async def startup(self) -> None:
        """Arranca el scheduler service y la cola de background-delegation.

        Los canales (bots, broadcast) los arranca el daemon vía ``self.channels``."""
        if self.global_config.scheduler.enabled:
            await self._reconciliar_builtins()
            await self.scheduler_service.start()
            logger.info("SchedulerService iniciado")

        # REQ-BGD-1: arrancar la cola de background-delegation tras el scheduler.
        # Idempotente — segunda llamada a start() es no-op.
        await self.background_queue.start()
        logger.info("BackgroundDelegationQueue iniciada")

    async def shutdown(self) -> None:
        """Detiene la cola de background-delegation y el scheduler service."""
        # REQ-BGD-8: detener la cola PRIMERO. Las tasks in-flight se abandonan
        # sin dispatchar resultado — aceptable por la decisión in-memory only.
        await self.background_queue.stop()
        logger.info("BackgroundDelegationQueue detenida")

        await self.scheduler_service.stop()
        logger.info("SchedulerService detenido")

    def get_agent(self, agent_id: str) -> AgentContainer:
        if agent_id not in self.agents:
            raise AgentNotFoundError(
                f"Agente '{agent_id}' no encontrado o falló al inicializar. "
                f"Disponibles: {list(self.agents)}"
            )
        return self.agents[agent_id]
