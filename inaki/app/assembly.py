"""El ensamblador: cinco pasadas explícitas que producen un ``HarnessRuntime``.

Es el composition root de verdad: el ÚNICO sitio que decide el ORDEN y reparte
lo que un módulo necesita de otro. No sabe cómo se arma nada (eso es del
``wiring.py`` de cada módulo); sabe cuándo y con qué.

Las pasadas, en el orden en que corren y con lo que cada una necesita de la
anterior como PARÁMETROS (no como atributos que "ya deberían estar"):

0. **Estado compartido** — store del Tool Config Protocol, scope registry,
   tracer, reloader, y los registros de enlace tardío (ver abajo).
1. **Por agente** — ``_construir_agente``: llm, embedding, memoria, historial,
   skills, tools builtin, knowledge, extensiones, voz, ``run_agent``, one-shot y
   los jobs de memoria. Produce un ``_Borrador``: una dataclass PRIVADA y mutable
   del ensamblador que nunca sale de este módulo.
2. **Harness** — router de canales, dispatcher, cola background, consolidate-all,
   scheduler, singletons de fotos, repo de ficheros de Telegram.
3. **Cruzar** — con todos los agentes construidos: delegación, tool del
   scheduler, fotos, tools de Telegram y broadcast, sub-agentes de memoria. Cada
   ``_wire_*`` registra tools sobre los objetos del borrador y anota en él lo que
   irá al runtime. Las mutaciones legítimas (registrar una tool,
   ``set_background_queue``, ``set_extractor``) actúan sobre los objetos; el
   runtime nunca las ve.
4. **Canales** — bot + ``TelegramChannel`` por agente con token.
5. **Ensamblar** — ``AgentRuntime`` por agente y ``HarnessRuntime``: el único
   sitio que construye los runtimes, inmutables.

Enlace tardío: el router, el dispatcher y las tools de Telegram necesitan
resolver un agente o un bot EN RUNTIME, y en la pasada 2 los runtimes todavía
no existen. Reciben un dict vacío que la pasada 5 rellena (``_Registros``):
el objeto es el mismo, el contenido llega al final. Es la única mutación que
sobrevive al ensamblado, y está acotada a esos dos dicts.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from inaki.agents.dispatcher import LLMDispatcherAdapter
from inaki.agents.scope_registry import InMemoryScopeRegistryAdapter
from inaki.agents.wiring import (
    build_background_queue,
    build_delegate_tool,
    build_discovery_section,
    build_ephemeral_child,
)
from inaki.app.extensions import registrar_extensiones
from inaki.app.reloader import DaemonReloader
from inaki.app.runtime import AgentRuntime, HarnessRuntime
from inaki.app.settings import build_one_shot_settings, build_run_agent_settings
from inaki.channels.telegram.bot import TelegramBot
from inaki.channels.telegram.config import telegram_config
from inaki.channels.telegram.files.ports import IFileDownloader, IFileRecordRepo
from inaki.channels.telegram.wiring import (
    TelegramAgentResources,
    build_broadcast,
    build_channel,
    build_telegram_bot_ports,
    build_telegram_file_repo,
    build_telegram_outbound,
    build_telegram_tools,
)
from inaki.config import AgentConfig, AgentRegistry, GlobalConfig, migrate_tool_config_to_own_file
from inaki.config.home import get_inaki_home
from inaki.config.wiring import build_config_tool
from inaki.embedding.cache import SqliteEmbeddingCache
from inaki.embedding.wiring import EmbeddingProviderFactory
from inaki.kernel.domain.services.channel_outbound_registry import ChannelOutboundRegistry
from inaki.kernel.domain.services.channel_router import ChannelFallbackSettings, ChannelRouter
from inaki.kernel.ports.outbound.channel_port import IChannel
from inaki.kernel.ports.outbound.embedding_port import IEmbeddingProvider
from inaki.kernel.ports.outbound.llm_port import ILLMProvider
from inaki.kernel.ports.outbound.memory_port import IMemoryRepository
from inaki.kernel.ports.outbound.scope_registry_port import IScopeRegistry
from inaki.kernel.ports.outbound.tool_config_port import IToolConfigStore
from inaki.kernel.ports.outbound.turn_tracer_port import ITurnTracer, NullTurnTracer
from inaki.kernel.use_cases.conversation_history import ConversationHistory
from inaki.kernel.use_cases.run_agent import RunAgentUseCase
from inaki.kernel.use_cases.run_agent_one_shot import RunAgentOneShotUseCase
from inaki.knowledge.wiring import KnowledgeBundle, build_knowledge, build_knowledge_tools
from inaki.llm.wiring import LLMProviderFactory
from inaki.memory.adapters.sqlite_history_store import SQLiteHistoryStore
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
from inaki.observability import JsonlTurnTracer, is_debug_enabled, startup_event
from inaki.perception.ports.transcription import ITranscriptionProvider
from inaki.perception.use_cases.process_photo import ProcessPhotoUseCase
from inaki.perception.use_cases.transcribe_audio import TranscribeAudioUseCase
from inaki.perception.wiring import (
    PhotosSingletons,
    TranscriptionProviderFactory,
    build_photos_for_agent,
    build_photos_singletons,
    build_transcribe_audio,
)
from inaki.scheduler.wiring import (
    SchedulerBundle,
    build_dispatch_ports,
    build_scheduler,
    build_scheduler_tool,
)
from inaki.shared.channel_context import ChannelContext, current_channel_context
from inaki.shared.errors import ConfigError, InakiError
from inaki.skills.yaml_skill_repo import YamlSkillRepository
from inaki.tools.registry import ToolRegistry
from inaki.tools.wiring import build_builtin_tools, build_tool_config_store, resolver_workspace

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Estructuras privadas del ensamblador
# ---------------------------------------------------------------------------


@dataclass
class _Borrador:
    """Un agente EN CONSTRUCCIÓN. Mutable a propósito y privado a propósito: las
    pasadas 3 y 4 lo completan; la pasada 5 lo congela en un ``AgentRuntime``."""

    cfg: AgentConfig
    llm: ILLMProvider
    embedder: IEmbeddingProvider
    memory: IMemoryRepository
    history: SQLiteHistoryStore
    skills: YamlSkillRepository
    tools: ToolRegistry
    knowledge: KnowledgeBundle
    transcribe_audio: TranscribeAudioUseCase | None
    run_agent: RunAgentUseCase
    run_agent_one_shot: RunAgentOneShotUseCase
    conversation: ConversationHistory
    jobs: MemoryJobs
    scope_registry: IScopeRegistry
    tracer: ITurnTracer
    outbounds: ChannelOutboundRegistry = field(default_factory=ChannelOutboundRegistry)
    # pasada 3
    scheduler: tuple[SchedulerBundle, str] | None = None
    process_photo: ProcessPhotoUseCase | None = None
    telegram: TelegramAgentResources | None = None
    telegram_file_repo: IFileRecordRepo | None = None
    telegram_file_downloader: IFileDownloader | None = None

    # Lo que la sección de descubrimiento de la delegación lee de un target.
    @property
    def name(self) -> str:
        return self.cfg.name

    @property
    def description(self) -> str:
        return self.cfg.description

    @property
    def tool_names(self) -> list[str]:
        return list(self.tools._tools.keys())


@dataclass
class _Registros:
    """Enlace tardío: dicts que se entregan vacíos en la pasada 2 y se rellenan al final."""

    agentes: dict[str, AgentRuntime] = field(default_factory=dict)
    bots: dict[str, TelegramBot] = field(default_factory=dict)
    outbounds: dict[str, ChannelOutboundRegistry] = field(default_factory=dict)


class _ContextoDelTurno:
    """Lo que las tools y ``delegate`` piden del "container llamador": el
    ``ChannelContext`` del turno en curso (un ``ContextVar`` task-safe)."""

    @staticmethod
    def get_channel_context() -> ChannelContext | None:
        return current_channel_context()


contexto_del_turno = _ContextoDelTurno()


@dataclass(frozen=True)
class _Harness:
    """Lo que la pasada 2 produce y las pasadas 3-5 consumen."""

    router: ChannelRouter
    dispatcher: LLMDispatcherAdapter
    background_queue: object
    consolidate_all: object
    scheduler: SchedulerBundle
    photos: PhotosSingletons | None
    telegram_file_repo: IFileRecordRepo | None


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------


def ensamblar(
    global_config: GlobalConfig, registry: AgentRegistry, config_dir: Path | None = None
) -> HarnessRuntime:
    """Construye el proceso entero a partir de la config validada. Ver el módulo."""
    # 0. Estado compartido
    resolved_config_dir = config_dir or get_inaki_home() / "config"
    migrate_tool_config_to_own_file(resolved_config_dir)
    tool_config_store = build_tool_config_store(resolved_config_dir)
    scope_registry: IScopeRegistry = InMemoryScopeRegistryAdapter()
    tracer: ITurnTracer = (
        JsonlTurnTracer(get_inaki_home() / "debug" / "turns")
        if is_debug_enabled(global_config.app.debug)
        else NullTurnTracer()
    )
    reloader = DaemonReloader()
    registros = _Registros()

    # 1. Por agente
    borradores: dict[str, _Borrador] = {}
    for agent_cfg in registry.list_all():
        try:
            borradores[agent_cfg.id] = _construir_agente(
                agent_cfg,
                global_config,
                tool_config_store=tool_config_store,
                scope_registry=scope_registry,
                tracer=tracer,
            )
            logger.info("Agente '%s' construido", agent_cfg.id)
        except Exception as exc:
            raise ConfigError(
                f"Agente '{agent_cfg.id}': no se pudo construir. "
                f"El agente quedaría declarado pero inexistente. Detalle: {exc}"
            ) from exc
    registros.outbounds.update({aid: b.outbounds for aid, b in borradores.items()})

    # 2. Harness
    harness = _construir_harness(global_config, registry, borradores, registros)

    # 3. Cruzar
    for agent_id, borrador in borradores.items():
        if registry.is_sub_agent(agent_id):
            continue
        _wire_delegation(borrador, global_config, registry, borradores, harness)
        _wire_scheduler(borrador, global_config, harness)
        _wire_broadcast(borrador)
        _wire_photos(borrador, global_config, harness)
        _wire_telegram_tools(borrador, global_config, harness, registros)
    _wire_memory_sub_agents(global_config, registry, borradores)

    # 4. Canales
    channels = _construir_canales(registry, borradores, registros, reloader)

    # 5. Ensamblar
    for agent_id, borrador in borradores.items():
        registros.agentes[agent_id] = _congelar(borrador)
    return HarnessRuntime(
        global_config=global_config,
        registry=registry,
        agents=dict(registros.agentes),
        channels=tuple(channels),
        scheduler=harness.scheduler,
        background_queue=harness.background_queue,  # type: ignore[arg-type]
        consolidate_all=harness.consolidate_all,  # type: ignore[arg-type]
        router=harness.router,
        dispatcher=harness.dispatcher,
        reloader=reloader,
        tool_config_store=tool_config_store,
        scope_registry=scope_registry,
        tracer=tracer,
        telegram_bots=registros.bots,
    )


# ---------------------------------------------------------------------------
# 1. Por agente
# ---------------------------------------------------------------------------


def _construir_agente(
    cfg: AgentConfig,
    global_cfg: GlobalConfig,
    *,
    tool_config_store: IToolConfigStore,
    scope_registry: IScopeRegistry,
    tracer: ITurnTracer,
) -> _Borrador:
    embedder = EmbeddingProviderFactory.create(cfg.embedding, cfg.providers)
    cache = SqliteEmbeddingCache(cfg.embedding.cache_filename)
    llm = LLMProviderFactory.create(cfg.llm, cfg.providers)
    memory = build_memory_repo(cfg, embedder)
    history = build_history_store(cfg)
    skills = YamlSkillRepository(embedder=embedder, cache=cache, dimension=cfg.embedding.dimension)
    tools = ToolRegistry(embedder=embedder, cache=cache, dimension=cfg.embedding.dimension)

    # Tools built-in: cada módulo arma las suyas; acá solo se registran.
    workspace = resolver_workspace(cfg)
    knowledge = build_knowledge(global_cfg, cfg, memory=memory, embedder=embedder)
    for tool in (
        *build_knowledge_tools(knowledge, embedder),
        *build_memory_tools(memory=memory, embedder=embedder, history=history, agent_id=cfg.id),
        *build_builtin_tools(cfg, workspace=workspace, config_store=tool_config_store),
        build_config_tool(global_cfg, cfg),
    ):
        tools.register(tool)
    registrar_extensiones(
        global_cfg.app.ext_dirs,
        tools=tools,
        skills=skills,
        knowledge_sources=knowledge.sources,
        config_store=tool_config_store,
        agent_cfg=cfg,
        global_cfg=global_cfg,
        embedder=embedder,
    )

    transcription = resolver_transcripcion(cfg)
    transcribe_audio = (
        build_transcribe_audio(transcription, cfg.transcription)
        if transcription is not None and cfg.transcription is not None
        else None
    )
    run_agent = RunAgentUseCase(
        llm=llm,
        memory=memory,
        embedder=embedder,
        skills=skills,
        history=history,
        tools=tools,
        settings=build_run_agent_settings(cfg, user_timezone=global_cfg.user.timezone),
        knowledge_orchestrator=knowledge.orchestrator,
        scope_registry=scope_registry,
        tracer=tracer,
    )
    run_agent_one_shot = RunAgentOneShotUseCase(
        llm=llm,
        tools=tools,
        settings=build_one_shot_settings(cfg),
        tracer=tracer,
    )
    jobs = build_memory_jobs(cfg, base_llm=llm, memory=memory, embedder=embedder, history=history)
    return _Borrador(
        cfg=cfg,
        llm=llm,
        embedder=embedder,
        memory=memory,
        history=history,
        skills=skills,
        tools=tools,
        knowledge=knowledge,
        transcribe_audio=transcribe_audio,
        run_agent=run_agent,
        run_agent_one_shot=run_agent_one_shot,
        conversation=ConversationHistory(history, cfg.id),
        jobs=jobs,
        scope_registry=scope_registry,
        tracer=tracer,
    )


def resolver_transcripcion(cfg: AgentConfig) -> ITranscriptionProvider | None:
    """Si el agente tiene voz lo decide el CANAL (``channels.telegram.voice_enabled``);
    por eso la decisión vive en el composition root y no en ``perception``.

    - Sin canal telegram → ``None`` (no hay voz posible).
    - ``voice_enabled=False`` explícito → ``None``.
    - Voz activa sin bloque ``transcription:`` → error claro en el arranque: no
      se degrada en silencio.
    """
    tg_cfg = telegram_config(cfg)
    if tg_cfg is None or tg_cfg.voice_enabled is False:
        return None
    if cfg.transcription is None:
        raise InakiError(
            f"Agent '{cfg.id}': channels.telegram.voice_enabled=True requiere "
            "un bloque 'transcription:' en la config (del agente o global). "
            "Agregá `transcription:` con provider y api_key, o poné "
            "channels.telegram.voice_enabled=false para deshabilitar voz."
        )
    return TranscriptionProviderFactory.create(cfg.transcription, cfg.providers)


# ---------------------------------------------------------------------------
# 2. Harness
# ---------------------------------------------------------------------------


def _construir_harness(
    global_cfg: GlobalConfig,
    registry: AgentRegistry,
    borradores: dict[str, _Borrador],
    registros: _Registros,
) -> _Harness:
    scheduler_cfg = global_cfg.scheduler

    def _outbounds_de(agent_id: str | None) -> ChannelOutboundRegistry | None:
        if agent_id and agent_id in registros.outbounds:
            return registros.outbounds[agent_id]
        for outbounds in registros.outbounds.values():
            if outbounds.list_channels():
                return outbounds
        return None

    router = ChannelRouter(
        resolve_outbounds=_outbounds_de,
        fallback=ChannelFallbackSettings(
            default=scheduler_cfg.channel_fallback.default,
            overrides=dict(scheduler_cfg.channel_fallback.overrides),
        ),
        hardcoded_fallback=f"file://{scheduler_cfg.fallback_log_filename}",
    )
    # UNA instancia del dispatcher entre scheduler y cola de background: comparten
    # el dict de locks por scope (REQ-BGD-6). Resuelve agentes por enlace tardío.
    dispatcher = LLMDispatcherAdapter(registros.agentes)

    def _resolve_one_shot(caller_id: str, target_id: str) -> RunAgentOneShotUseCase | None:
        caller = registros.agentes.get(caller_id)
        raw = registry.get_sub_agent_raw(target_id)
        if caller is None or raw is None:
            return None
        return build_ephemeral_child(
            raw,
            caller_cfg=caller.agent_config,
            caller_llm=caller.llm,
            tools=caller.tools,
            tracer=caller.tracer,
        )

    background_queue = build_background_queue(
        global_cfg,
        dispatcher=dispatcher,
        one_shot_resolver=_resolve_one_shot,
        result_sender=router,
    )
    consolidate_all = build_consolidate_all(
        {aid: b.jobs.consolidate for aid, b in borradores.items() if b.jobs.consolidate},
        delay_seconds=global_cfg.memories.consolidation.delay_seconds,
    )
    scheduler = build_scheduler(
        global_cfg,
        dispatch=build_dispatch_ports(
            channel_sender=router,
            llm_dispatcher=dispatcher,
            consolidate_all=consolidate_all,
            reconcilers={
                aid: b.jobs.reconcile for aid, b in borradores.items() if b.jobs.reconcile
            },
        ),
    )

    photos: PhotosSingletons | None = None
    photos_cfg = getattr(global_cfg, "photos", None)
    if photos_cfg is not None and photos_cfg.enabled:
        try:
            photos = build_photos_singletons(
                photos_cfg, faces_db_path=_data_db_path(borradores, "faces.db")
            )
            logger.info("Photos singletons inicializados (vision=lazy)")
        except Exception as exc:
            logger.error(
                "Reconocimiento facial y descripción de escena QUEDAN DESHABILITADOS "
                "para TODOS los agentes — no se pudieron inicializar los singletons "
                "de photos: %s",
                exc,
            )

    telegram_file_repo: IFileRecordRepo | None = None
    if any(
        (tg := telegram_config(cfg)) is not None and tg.token for cfg in registry.list_regular()
    ):
        telegram_file_repo = build_telegram_file_repo(
            _data_db_path(borradores, "telegram_files.db")
        )

    return _Harness(
        router=router,
        dispatcher=dispatcher,
        background_queue=background_queue,
        consolidate_all=consolidate_all,
        scheduler=scheduler,
        photos=photos,
        telegram_file_repo=telegram_file_repo,
    )


def _data_db_path(borradores: dict[str, _Borrador], filename: str) -> str:
    """Un fichero junto al ``history.db`` del primer agente (el directorio de datos)."""
    primero = next(iter(borradores.values()), None)
    if primero is None:
        return f"~/.inaki/data/{filename}"
    return str(Path(primero.cfg.chat_history.db_filename).parent / filename)


# ---------------------------------------------------------------------------
# 3. Cruzar
# ---------------------------------------------------------------------------


def _wire_delegation(
    b: _Borrador,
    global_cfg: GlobalConfig,
    registry: AgentRegistry,
    borradores: dict[str, _Borrador],
    harness: _Harness,
) -> None:
    """Registra ``delegate`` y la sección de descubrimiento si la delegación está
    habilitada y hay sub-agentes elegibles (REQ-DG-1: sin tool, nunca en los schemas)."""
    if not b.cfg.delegation.enabled:
        return
    targets = [cfg.id for cfg in registry.list_sub_agents()]
    if b.cfg.delegation.allowed_targets:
        allowed = set(b.cfg.delegation.allowed_targets)
        targets = [t for t in targets if t in allowed]
    if not targets:
        logger.debug("Agente '%s': delegación sin sub-agentes elegibles", b.cfg.id)
        return
    try:
        build_child = _constructor_de_hijos(b, global_cfg, registry)
        b.tools.register(
            build_delegate_tool(
                global_cfg,
                allowed_targets=targets,
                build_child=build_child,
                caller_agent_id=b.cfg.id,
                caller=contexto_del_turno,
                queue=harness.background_queue,  # type: ignore[arg-type]
            )
        )
        b.run_agent.set_background_queue(harness.background_queue)  # type: ignore[arg-type]
        seccion = build_discovery_section(
            b.cfg.id,
            targets,
            get_sub_agent_raw=registry.get_sub_agent_raw,
            describir_agente=borradores.get,
        )
        if seccion:
            b.run_agent.set_extra_system_sections([seccion])
    except Exception as exc:
        raise ConfigError(
            f"Agente '{b.cfg.id}': falló el wiring de delegación. El agente "
            f"declara 'delegation' pero no podría delegar. Detalle: {exc}"
        ) from exc
    logger.info("Agente '%s': delegation wired (sub_agents=%s)", b.cfg.id, targets)


def _constructor_de_hijos(
    b: _Borrador, global_cfg: GlobalConfig, registry: AgentRegistry
) -> Callable[[str], RunAgentOneShotUseCase | None]:
    """Un hijo efímero por delegación, resuelto contra ESTE caller. Cierra sobre
    valores (config, llm, tools, tracer), no sobre el borrador."""
    caller_cfg, caller_llm, tools, tracer = b.cfg, b.llm, b.tools, b.tracer

    def _build_child(target_id: str) -> RunAgentOneShotUseCase | None:
        raw = registry.get_sub_agent_raw(target_id)
        if raw is None:
            return None
        return build_ephemeral_child(
            raw,
            caller_cfg=caller_cfg,
            caller_llm=caller_llm,
            tools=tools,
            tracer=tracer,
        )

    return _build_child


def _wire_scheduler(b: _Borrador, global_cfg: GlobalConfig, harness: _Harness) -> None:
    try:
        b.tools.register(
            build_scheduler_tool(
                use_case=harness.scheduler.use_case,
                runner=harness.scheduler.service,
                agent_id=b.cfg.id,
                user_timezone=global_cfg.user.timezone,
                get_channel_context=contexto_del_turno.get_channel_context,
            )
        )
    except Exception as exc:
        raise ConfigError(
            f"Agente '{b.cfg.id}': falló el wiring del scheduler. Sus tareas "
            f"programadas no se ejecutarían. Detalle: {exc}"
        ) from exc
    b.scheduler = (harness.scheduler, global_cfg.user.timezone)


def _wire_broadcast(b: _Borrador) -> None:
    """Rate limiter de grupos, egress y transporte de broadcast (nota
    ``broadcast-arranque-observable``: un fallo acá aborta, no degrada)."""
    try:
        b.telegram = build_broadcast(b.cfg)
    except Exception as exc:
        raise ConfigError(
            f"Agente '{b.cfg.id}': falló el wiring de broadcast/grupos. Detalle: {exc}"
        ) from exc


def _wire_photos(b: _Borrador, global_cfg: GlobalConfig, harness: _Harness) -> None:
    """Fotos degradan por agente: si algo falla, ESTE agente no analiza fotos y el
    resto arranca normal. El operador lee QUÉ capacidad perdió y por qué."""
    photos_cfg = getattr(global_cfg, "photos", None)
    if photos_cfg is None or not photos_cfg.enabled:
        return
    if harness.photos is None:
        logger.warning(
            "Agente '%s': vision o face_registry no disponibles — photos wiring omitido",
            b.cfg.id,
        )
        return
    try:
        armado = build_photos_for_agent(
            b.cfg,
            photos_cfg,
            harness.photos,
            get_channel_context=contexto_del_turno.get_channel_context,
            tracer=b.tracer,
        )
    except Exception as exc:
        logger.error(
            "Agente '%s': el procesamiento de fotos QUEDA DESHABILITADO — %s. "
            "El resto del agente arranca normal; las fotos entrantes no se analizan.",
            b.cfg.id,
            exc,
        )
        return
    b.process_photo = armado.process_photo
    for tool in armado.tools:
        b.tools.register(tool)
    logger.info(
        "Agente '%s': photos wired (scene_provider=%s, %d face tools)",
        b.cfg.id,
        photos_cfg.scene.provider,
        len(armado.tools),
    )


def _wire_telegram_tools(
    b: _Borrador, global_cfg: GlobalConfig, harness: _Harness, registros: _Registros
) -> None:
    """El egress del canal y las tools que dependen del bot, para agentes con token.
    El bot se resuelve por enlace tardío: existe recién en la pasada 4."""
    tg_cfg = telegram_config(b.cfg)
    if tg_cfg is None or not tg_cfg.token:
        return
    agent_id = b.cfg.id

    def _get_bot() -> object | None:
        return registros.bots.get(agent_id)

    try:
        b.outbounds.register(
            build_telegram_outbound(
                get_telegram_bot=_get_bot,
                history=b.history,
                agent_id=agent_id,
                egress=b.telegram.egress if b.telegram else None,
                thinking_indicator=global_cfg.channels.thinking_indicator,
            )
        )
        if harness.telegram_file_repo is None:
            logger.warning(
                "Agente '%s': telegram_file_repo es None — tools no registradas", agent_id
            )
            return
        armado = build_telegram_tools(
            b.cfg,
            outbounds=b.outbounds,
            get_telegram_bot=_get_bot,
            file_repo=harness.telegram_file_repo,
            get_channel_context=contexto_del_turno.get_channel_context,
        )
    except Exception as exc:
        raise ConfigError(
            f"Agente '{agent_id}': falló el wiring de las tools de Telegram. "
            f"El agente tiene canal telegram pero no podría usarlas. Detalle: {exc}"
        ) from exc
    b.telegram_file_repo = harness.telegram_file_repo
    b.telegram_file_downloader = armado.downloader
    for tool in armado.tools:
        b.tools.register(tool)
    logger.info(
        "Agente '%s': send_to_telegram + send_telegram_message + download_from_telegram registradas",
        agent_id,
    )


def _wire_memory_sub_agents(
    global_cfg: GlobalConfig, registry: AgentRegistry, borradores: dict[str, _Borrador]
) -> None:
    candidatos = {
        aid: SubAgenteDeMemoria(one_shot=b.run_agent_one_shot, system_prompt=b.cfg.system_prompt)
        for aid, b in borradores.items()
    }
    for agent_id, b in borradores.items():
        if registry.is_sub_agent(agent_id):
            continue
        wire_sub_agentes_de_memoria(
            agent_id,
            b.jobs,
            b.cfg.memories,
            agentes=candidatos,
            es_sub_agente=registry.is_sub_agent,
            max_iterations=global_cfg.delegation.max_iterations_per_sub,
            timeout_seconds=global_cfg.delegation.timeout_seconds,
        )


# ---------------------------------------------------------------------------
# 4. Canales
# ---------------------------------------------------------------------------


def _construir_canales(
    registry: AgentRegistry,
    borradores: dict[str, _Borrador],
    registros: _Registros,
    reloader: DaemonReloader,
) -> list[IChannel]:
    """Un ``IChannel`` por agente regular con token. Un bot que no se puede
    construir se reporta como ``startup.resource`` con ``status=error`` y no
    tumba al daemon."""
    channels: list[IChannel] = []
    for agent_cfg in registry.list_regular():
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
        b = borradores.get(agent_cfg.id)
        if b is None:
            continue
        try:
            bot, channel = build_channel(
                agent_cfg, build_telegram_bot_ports(_FuenteDelBot(b)), b.telegram, reloader=reloader
            )
        except ValueError as exc:
            startup_event(
                logger, "telegram_bot", status="error", agent=agent_cfg.id, reason=str(exc)
            )
            continue
        registros.bots[agent_cfg.id] = bot
        channels.append(channel)
    return channels


class _FuenteDelBot:
    """Vista de un borrador con los nombres que ``build_telegram_bot_ports`` lee
    (los mismos del ``AgentRuntime``)."""

    def __init__(self, b: _Borrador) -> None:
        self.run_agent = b.run_agent
        self.history = b.conversation
        self.scope_registry = b.scope_registry
        self.consolidate_memory = b.jobs.consolidate
        self.reconcile_memory = b.jobs.reconcile
        self.schedule_task = b.scheduler[0].use_case if b.scheduler else None
        self.manual_task_runner = b.scheduler[0].service if b.scheduler else None
        self.process_photo = b.process_photo
        self.transcribe_audio = b.transcribe_audio
        self.telegram_file_repo = b.telegram_file_repo
        self.telegram_file_downloader = b.telegram_file_downloader
        self.channel_outbound_registry = b.outbounds


# ---------------------------------------------------------------------------
# 5. Ensamblar
# ---------------------------------------------------------------------------


def _congelar(b: _Borrador) -> AgentRuntime:
    return AgentRuntime(
        agent_config=b.cfg,
        run_agent=b.run_agent,
        run_agent_one_shot=b.run_agent_one_shot,
        scope_registry=b.scope_registry,
        tracer=b.tracer,
        llm=b.llm,
        embedder=b.embedder,
        memory=b.memory,
        history_store=b.history,
        history=b.conversation,
        skills=b.skills,
        tools=b.tools,
        knowledge=b.knowledge,
        consolidate_memory=b.jobs.consolidate,
        reconcile_memory=b.jobs.reconcile,
        transcribe_audio=b.transcribe_audio,
        channel_outbound_registry=b.outbounds,
        schedule_task=b.scheduler[0].use_case if b.scheduler else None,
        manual_task_runner=b.scheduler[0].service if b.scheduler else None,
        process_photo=b.process_photo,
        broadcast_egress=b.telegram.egress if b.telegram else None,
        broadcast_adapter=b.telegram.broadcast if b.telegram else None,
        group_rate_limiter=b.telegram.rate_limiter if b.telegram else None,
        telegram_file_repo=b.telegram_file_repo,
        telegram_file_downloader=b.telegram_file_downloader,
    )
