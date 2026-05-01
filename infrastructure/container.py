"""
Container de inyección de dependencias.

AgentContainer — instancia todos los adaptadores para un agente concreto.
AppContainer — container raíz, carga todos los agentes al arrancar.

Este es el ÚNICO lugar donde se instancian adaptadores concretos.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Literal

from croniter import croniter

if TYPE_CHECKING:
    from core.domain.services.knowledge_orchestrator import KnowledgeOrchestrator
    from core.domain.value_objects.channel_context import ChannelContext
    from core.ports.outbound.knowledge_port import IKnowledgeSource

from adapters.outbound.history.sqlite_history_store import SQLiteHistoryStore
from adapters.outbound.memory.sqlite_memory_repo import SQLiteMemoryRepository
from adapters.outbound.scheduler.builtin_tasks import (
    CONSOLIDATE_MEMORY_TASK_ID,
    FACE_DEDUP_TASK_ID,
    build_consolidate_memory_task,
    build_face_dedup_task,
)
from adapters.outbound.scheduler.dispatch_adapters import (
    ChannelRouter,
    ConsolidationDispatchAdapter,
    HttpCallerAdapter,
    LLMDispatcherAdapter,
    SchedulerDispatchPorts,
)
from adapters.outbound.sinks.sink_factory import SinkFactory
from adapters.outbound.sinks.telegram_sink import TelegramSink
from adapters.outbound.scheduler.sqlite_scheduler_repo import SQLiteSchedulerRepo
from adapters.outbound.embedding.sqlite_embedding_cache import SqliteEmbeddingCache
from adapters.outbound.skills.yaml_skill_repo import YamlSkillRepository
from adapters.outbound.tools.tool_registry import ToolRegistry
from core.domain.entities.task import TaskStatus
from core.domain.errors import AgentNotFoundError, IñakiError
from core.domain.services.broadcast_buffer import BroadcastBuffer
from core.domain.services.rate_limiter import FixedWindowRateLimiter
from core.domain.services.scheduler_service import SchedulerService
from core.ports.outbound.transcription_port import ITranscriptionProvider
from core.use_cases.consolidate_all_agents import ConsolidateAllAgentsUseCase
from core.use_cases.consolidate_memory import ConsolidateMemoryUseCase
from core.use_cases.run_agent import RunAgentUseCase
from core.use_cases.run_agent_one_shot import RunAgentOneShotUseCase
from core.use_cases.schedule_task import ScheduleTaskUseCase
from infrastructure.config import AgentConfig, AgentRegistry, GlobalConfig, TelegramChannelConfig
from infrastructure.factories.embedding_factory import EmbeddingProviderFactory
from infrastructure.factories.llm_factory import LLMProviderFactory
from infrastructure.factories.transcription_factory import TranscriptionProviderFactory

logger = logging.getLogger(__name__)


class AgentContainer:
    """Container de dependencias para un agente concreto."""

    def __init__(self, agent_config: AgentConfig, global_config: GlobalConfig) -> None:
        cfg = agent_config
        self.agent_config = agent_config

        # Stash global_config so wire_delegation can access delegation limits (task 5.1)
        self._global_config = global_config

        # Idempotency guard for wire_delegation (task 5.1)
        self._delegation_wired: bool = False

        # Idempotency guard for wire_scheduler
        self._scheduler_wired: bool = False

        # Idempotency guard for wire_photos
        self._photos_wired: bool = False

        # ScheduleTaskUseCase — wired en fase 3 por AppContainer. None hasta entonces.
        self.schedule_task: ScheduleTaskUseCase | None = None

        # ProcessPhotoUseCase — wired en fase 5 por AppContainer. None si photos no habilitado.
        self.process_photo = None

        # Broadcast adapter — wired en fase 4 por AppContainer. None si el agente no
        # tiene ningún canal telegram con bloque broadcast:.
        # Tipo: TcpBroadcastAdapter | None (evitamos importar el adapter en __init__
        # para no crear dependencia circular; el tipo se declara como object).
        self.broadcast_adapter: object | None = None
        self.broadcast_rate_limiter: FixedWindowRateLimiter | None = None

        # Contexto de canal activo — se setea en cada turno de conversación
        self._channel_context: ChannelContext | None = None

        # Factories resuelven el proveedor correcto leyendo cfg.embedding.provider y cfg.llm.provider
        # y componen ResolvedXConfig contra el registry top-level de providers.
        self._embedder = EmbeddingProviderFactory.create(cfg.embedding, cfg.providers)
        self._embedding_cache = SqliteEmbeddingCache(cfg.embedding.cache_filename)
        self._memory = SQLiteMemoryRepository(cfg.memory.db_filename, self._embedder)
        self._llm = LLMProviderFactory.create(cfg.llm, cfg.providers)
        self._skills = YamlSkillRepository(
            embedder=self._embedder,
            cache=self._embedding_cache,
            dimension=cfg.embedding.dimension,
        )
        self._history = SQLiteHistoryStore(cfg.chat_history)
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

        self.run_agent = RunAgentUseCase(
            llm=self._llm,
            memory=self._memory,
            embedder=self._embedder,
            skills=self._skills,
            history=self._history,
            tools=self._tools,
            agent_config=cfg,
            knowledge_orchestrator=self._knowledge_orchestrator,
        )

        # Every agent gets a one-shot use case unconditionally so it can always
        # be a delegation target, regardless of whether it can INITIATE delegation.
        # (REQ-DG-1 still holds: the `delegate` tool is only registered when
        # delegation.enabled=True — see wire_delegation.)
        self.run_agent_one_shot = RunAgentOneShotUseCase(
            llm=self._llm,
            tools=self._tools,
            agent_config=cfg,
        )

        # LLM de consolidación: solo se instancia cuando `memory.enabled` es true.
        # Si no, no tiene sentido resolver el provider (no se va a usar) y además
        # ahorra el log de "LLM de consolidación dedicado" para sub-agentes one-shot.
        # AppContainer ya filtra por memory.enabled cuando arma enabled_consolidators.
        self.consolidate_memory: ConsolidateMemoryUseCase | None = None
        if cfg.memory.enabled:
            llm_consolidator = self._resolve_memory_llm(cfg, self._llm)
            self.consolidate_memory = ConsolidateMemoryUseCase(
                llm=llm_consolidator,
                memory=self._memory,
                embedder=self._embedder,
                history=self._history,
                agent_id=cfg.id,
                memory_config=cfg.memory,
            )

    @staticmethod
    def _resolve_memory_llm(cfg: AgentConfig, base_llm):
        """
        Devuelve el ``ILLMProvider`` que debe inyectarse en
        ``ConsolidateMemoryUseCase``.

        - Si ``cfg.memory.llm`` no existe o produce una config efectiva idéntica
          al ``cfg.llm``, REUSA la instancia ``base_llm`` (evita duplicar
          clientes HTTP).
        - Si la config efectiva difiere, instancia un provider nuevo vía
          ``LLMProviderFactory.create_from_resolved``, que toma el
          ``ResolvedLLMConfig`` (feature + creds del registry) ya compuesto.

        Puede lanzar ``ConfigError`` si el provider del override requiere creds
        y no existe entrada en el registry.
        """
        merged = cfg.memory.merged_llm_config(cfg.llm)
        if merged == cfg.llm:
            return base_llm

        resolved = cfg.memory.resolved_llm_config(cfg.llm, cfg.providers)
        logger.info(
            "Agente '%s': LLM de consolidación dedicado — provider=%s, model=%s, "
            "reasoning_effort=%s, max_tokens=%d",
            cfg.id,
            resolved.provider,
            resolved.model,
            resolved.reasoning_effort,
            resolved.max_tokens,
        )
        return LLMProviderFactory.create_from_resolved(resolved)

    def _collect_knowledge_sources(
        self,
    ) -> "tuple[list[IKnowledgeSource], dict]":
        """
        Recolecta las fuentes de conocimiento de nivel 1 y 2 (memoria + config).

        Retorna (fuentes, params) donde params es un dict con los parámetros
        del orquestrador: max_total_chunks, token_budget_threshold,
        pre_fetch_enabled, default_top_k_per_source, default_min_score.
        Las fuentes de nivel 3 (extensiones) se añaden en _register_extensions().
        Orden garantizado: (1) memoria, (2) fuentes configuradas.
        """
        from adapters.outbound.knowledge.sqlite_memory_knowledge_source import (
            SqliteMemoryKnowledgeSource,
        )

        knowledge_cfg = getattr(self._global_config, "knowledge", None)

        # Leer flags desde la config o usar defaults
        include_memory = True
        params = {
            "max_total_chunks": 10,
            "token_budget_threshold": 4000,
            "pre_fetch_enabled": True,
            "default_top_k_per_source": 3,
            "default_min_score": 0.5,
        }

        if knowledge_cfg is not None:
            include_memory = getattr(knowledge_cfg, "include_memory", True)
            params["max_total_chunks"] = getattr(knowledge_cfg, "max_total_chunks", 10)
            params["token_budget_threshold"] = getattr(
                knowledge_cfg, "token_budget_warn_threshold", 4000
            )
            params["pre_fetch_enabled"] = getattr(knowledge_cfg, "enabled", True)
            params["default_top_k_per_source"] = getattr(knowledge_cfg, "top_k_per_source", 3)
            params["default_min_score"] = getattr(knowledge_cfg, "min_score", 0.5)

        fuentes: list[IKnowledgeSource] = []

        # Nivel 1 — memoria (auto-registrada por defecto)
        if include_memory:
            fuentes.append(SqliteMemoryKnowledgeSource(memory=self._memory))
            logger.debug(
                "AgentContainer '%s': SqliteMemoryKnowledgeSource registrada",
                self.agent_config.id,
            )

        # Nivel 2 — fuentes configuradas en GlobalConfig.knowledge.sources
        if knowledge_cfg is not None:
            sources_cfg = getattr(knowledge_cfg, "sources", []) or []
            for fuente_cfg in sources_cfg:
                if not getattr(fuente_cfg, "enabled", True):
                    continue

                tipo = getattr(fuente_cfg, "type", "")
                if tipo == "document":
                    fuentes.append(self._build_document_source(fuente_cfg))
                elif tipo == "sqlite":
                    sqlite_source = self._build_sqlite_source(fuente_cfg)
                    if sqlite_source is not None:
                        fuentes.append(sqlite_source)
                else:
                    logger.warning(
                        "AgentContainer '%s': tipo de fuente '%s' no reconocido para '%s' — skipping",
                        self.agent_config.id,
                        tipo,
                        getattr(fuente_cfg, "id", "<sin-id>"),
                    )

        return fuentes, params

    def _build_knowledge_orchestrator(
        self,
        fuentes: "list[IKnowledgeSource]",
        params: dict,
    ) -> "KnowledgeOrchestrator":
        """
        Construye el KnowledgeOrchestrator con la lista de fuentes ya resuelta.

        Recibe las fuentes ordenadas (memoria → config → ext) y los parámetros
        del orquestrador (ver _collect_knowledge_sources). Separado de
        _collect_knowledge_sources() para que _register_extensions() pueda añadir
        fuentes de nivel 3 antes de que se construya el orquestrador definitivo.
        """
        from core.domain.services.knowledge_orchestrator import KnowledgeOrchestrator

        return KnowledgeOrchestrator(
            sources=fuentes,
            max_total_chunks=params["max_total_chunks"],
            token_budget_threshold=params["token_budget_threshold"],
            pre_fetch_enabled=params["pre_fetch_enabled"],
            default_top_k_per_source=params["default_top_k_per_source"],
            default_min_score=params["default_min_score"],
        )

    def _build_document_source(self, fuente_cfg: object) -> "IKnowledgeSource":
        """Instancia un DocumentKnowledgeSource a partir de la config de fuente."""
        from adapters.outbound.knowledge.document_knowledge_source import (
            DocumentKnowledgeSource,
        )

        return DocumentKnowledgeSource(
            source_id=fuente_cfg.id,
            description=getattr(fuente_cfg, "description", ""),
            path=fuente_cfg.path,
            embedder=self._embedder,
            glob=getattr(fuente_cfg, "glob", "**/*.md"),
            chunk_size=getattr(fuente_cfg, "chunk_size", 500),
            chunk_overlap=getattr(fuente_cfg, "chunk_overlap", 80),
            dimension=self.agent_config.embedding.dimension,
        )

    def _build_sqlite_source(self, fuente_cfg: object) -> "IKnowledgeSource | None":
        """
        Instancia un SqliteKnowledgeSource a partir de la config de fuente.

        Captura KnowledgeConfigError al construir (validación diferida al primer search),
        pero registra el error ahora si el path no está configurado.
        Si hay un error de config irrecuperable, loguea y retorna None para que el
        container omita esta fuente sin abortar el arranque del agente.
        """
        from adapters.outbound.knowledge.sqlite_knowledge_source import (
            SqliteKnowledgeSource,
        )
        from core.domain.errors import KnowledgeConfigError

        fuente_id = getattr(fuente_cfg, "id", "<sin-id>")
        db_path = getattr(fuente_cfg, "path", None)

        if not db_path:
            logger.error(
                "AgentContainer '%s': fuente sqlite '%s' no tiene 'path' configurado — skipping",
                self.agent_config.id,
                fuente_id,
            )
            return None

        try:
            return SqliteKnowledgeSource(
                source_id=fuente_id,
                description=getattr(fuente_cfg, "description", ""),
                db_path=db_path,
            )
        except KnowledgeConfigError as exc:
            logger.error(
                "AgentContainer '%s': error de configuración en fuente sqlite '%s': %s — skipping",
                self.agent_config.id,
                fuente_id,
                exc,
            )
            return None

    def _register_tools(self) -> None:
        """Registra tools built-in del núcleo. Las extensiones se cargan aparte."""
        from pathlib import Path

        from adapters.outbound.tools.knowledge_search_tool import KnowledgeSearchTool
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
                workspace_path,
                self.agent_config.id,
                exc,
            )
            raise
        logger.info(
            "Agente '%s': workspace='%s' containment='%s'",
            self.agent_config.id,
            workspace_path,
            ws_cfg.containment,
        )

        # Recolectar fuentes de nivel 1 (memoria) y nivel 2 (config).
        # Las de nivel 3 (extensiones) se añaden en _register_extensions() sobre la misma lista.
        # El orquestrador almacena la referencia a esa lista, por lo que las fuentes de extensiones
        # quedan incorporadas automáticamente sin reconstruir el objeto orquestrador.
        (
            self._pending_knowledge_sources,
            self._knowledge_params,
        ) = self._collect_knowledge_sources()
        self._knowledge_orchestrator = self._build_knowledge_orchestrator(
            self._pending_knowledge_sources,
            self._knowledge_params,
        )
        self._tools.register(
            KnowledgeSearchTool(
                orchestrator=self._knowledge_orchestrator,
                embedder=self._embedder,
            )
        )
        self._tools.register(WebSearchTool())
        self._tools.register(ReadFileTool(workspace=workspace_path, containment=ws_cfg.containment))
        self._tools.register(
            WriteFileTool(workspace=workspace_path, containment=ws_cfg.containment)
        )
        self._tools.register(
            PatchFileTool(workspace=workspace_path, containment=ws_cfg.containment)
        )

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
        tg_cfg = cfg.channels.get("telegram")
        if tg_cfg is None:
            return None

        voice_enabled = tg_cfg.get("voice_enabled", True)
        if voice_enabled is False:
            return None

        if cfg.transcription is None:
            raise IñakiError(
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

    def set_channel_context(self, ctx: "ChannelContext | None") -> None:
        """Actualiza el contexto de canal activo para este agente."""
        self._channel_context = ctx

    def get_channel_context(self) -> "ChannelContext | None":
        """Devuelve el contexto de canal activo, o None si no hay conversación en curso."""
        return self._channel_context

    def wire_delegation(
        self,
        get_agent_container: Callable[[str], "AgentContainer | None"],
        sub_agent_ids: list[str] | None = None,
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

        if not targets:
            self._delegation_wired = True
            logger.debug(
                "AgentContainer '%s': wire_delegation no-op (sin sub-agentes configurados)",
                self.agent_config.id,
            )
            return

        from adapters.outbound.tools.delegate_tool import DelegateTool

        # Build and register the delegate tool.
        # (run_agent_one_shot is already set in __init__ — no construction here.)
        delegate_tool = DelegateTool(
            allowed_targets=targets,
            get_agent_container=get_agent_container,
            max_iterations_per_sub=self._global_config.delegation.max_iterations_per_sub,
            timeout_seconds=self._global_config.delegation.timeout_seconds,
        )
        self._tools.register(delegate_tool)

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
        discovery_section = self._build_discovery_section(get_agent_container, targets)
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

    def wire_scheduler(self, schedule_task_uc: ScheduleTaskUseCase, user_timezone: str) -> None:
        """
        Phase-3 wiring: registers the scheduler tool.

        Must be called AFTER AppContainer has constructed schedule_task_uc
        (which depends on scheduler_repo, available only at AppContainer level).
        Idempotente: segunda llamada es no-op. No-op también si schedule_task_uc es None.
        """
        if schedule_task_uc is None:
            return
        if self._scheduler_wired:
            return

        from adapters.outbound.tools.scheduler_tool import SchedulerTool

        self._tools.register(
            SchedulerTool(
                schedule_task_uc=schedule_task_uc,
                agent_id=self.agent_config.id,
                user_timezone=user_timezone,
                get_channel_context=self.get_channel_context,
            )
        )
        self.schedule_task = schedule_task_uc
        self._scheduler_wired = True
        logger.info("AgentContainer '%s': scheduler tool registrada", self.agent_config.id)

    def wire_photos(
        self,
        vision,
        face_registry,
        global_config: GlobalConfig,
    ) -> None:
        """Phase-5 wiring: instantiates photo processing use case and face tools.

        Receives the shared vision and face registry singletons from AppContainer.
        Creates per-agent adapters: scene describer, annotator, metadata repo, use case.
        No-op when photos is not enabled or already wired.
        """
        if self._photos_wired:
            return

        photos_cfg = getattr(global_config, "photos", None)
        if photos_cfg is None or not photos_cfg.enabled:
            self._photos_wired = True
            return

        if vision is None or face_registry is None:
            logger.warning(
                "AgentContainer '%s': vision o face_registry no disponibles — "
                "photos wiring omitido",
                self.agent_config.id,
            )
            self._photos_wired = True
            return

        try:
            from pathlib import Path

            from adapters.outbound.history.sqlite_message_face_metadata_repo import (
                SqliteMessageFaceMetadataRepo,
            )
            from adapters.outbound.imaging.pillow_annotator import PillowPhotoAnnotator
            from core.use_cases.process_photo import ProcessPhotoUseCase

            # Metadata repo: side-table en el mismo history.db del agente.
            history_db = self.agent_config.chat_history.db_filename
            metadata_repo = SqliteMessageFaceMetadataRepo(history_db)

            # Scene describer: por agente, resuelto desde global photos config.
            scene_describer = self._build_scene_describer(photos_cfg)

            annotator = PillowPhotoAnnotator()

            self.process_photo = ProcessPhotoUseCase(
                vision=vision,
                face_registry=face_registry,
                scene_describer=scene_describer,
                annotator=annotator,
                metadata_repo=metadata_repo,
                config=photos_cfg,
            )

            # Registrar las 8 face tools.
            self._register_face_tools(face_registry, metadata_repo, photos_cfg)

            self._photos_wired = True
            logger.info(
                "AgentContainer '%s': photos wired (scene_provider=%s)",
                self.agent_config.id,
                photos_cfg.scene.provider,
            )
        except Exception as exc:
            logger.error(
                "Error en wire_photos para agente '%s': %s", self.agent_config.id, exc
            )
            self._photos_wired = True

    def _build_scene_describer(self, photos_cfg):
        """Instancia el adaptador de descripción de escena según el provider configurado."""
        from core.domain.errors import IñakiError

        provider = photos_cfg.scene.provider
        model = photos_cfg.scene.model
        prompt = photos_cfg.scene.prompt_template

        # Fallback al api_key del provider global cuando no se especifica en photos.scene.
        api_key = photos_cfg.scene.api_key
        if not api_key:
            providers = self.agent_config.providers
            # Buscar por key directa (ej: providers.openai) o por type explícito.
            match = providers.get(provider) or next(
                (p for p in providers.values() if p.type == provider), None
            )
            api_key = (match.api_key if match else None) or ""

        if provider == "anthropic":
            from adapters.outbound.scene.anthropic_describer import (
                AnthropicSceneDescriberAdapter,
            )
            return AnthropicSceneDescriberAdapter(api_key, model, prompt)
        elif provider == "openai":
            from adapters.outbound.scene.openai_describer import (
                OpenAISceneDescriberAdapter,
            )
            return OpenAISceneDescriberAdapter(api_key, model, prompt)
        elif provider == "groq":
            from adapters.outbound.scene.groq_describer import GroqSceneDescriberAdapter
            return GroqSceneDescriberAdapter(api_key, model, prompt)
        else:
            raise IñakiError(f"Scene provider desconocido: '{provider}'. Válidos: anthropic, openai, groq")

    def _register_face_tools(self, face_registry, metadata_repo, photos_cfg) -> None:
        """Registra las 8 face tools en el registry del agente."""
        from adapters.outbound.tools.face_tools import (
            AddPhotoToPersonTool,
            FindDuplicatePersonsTool,
            ForgetPersonTool,
            ListKnownPersonsTool,
            MergePersonsTool,
            RegisterFaceTool,
            SkipFaceTool,
            UpdatePersonMetadataTool,
        )

        agent_id = self.agent_config.id
        get_ctx = self.get_channel_context
        dedup_threshold = photos_cfg.dedup.similarity_threshold

        tools = [
            RegisterFaceTool(face_registry, metadata_repo, agent_id, get_ctx),
            AddPhotoToPersonTool(face_registry, metadata_repo, agent_id, get_ctx),
            UpdatePersonMetadataTool(face_registry),
            ListKnownPersonsTool(face_registry),
            ForgetPersonTool(face_registry),
            SkipFaceTool(face_registry, metadata_repo, agent_id, get_ctx),
            MergePersonsTool(face_registry),
            FindDuplicatePersonsTool(face_registry, dedup_threshold),
        ]
        for tool in tools:
            self._tools.register(tool)

        logger.info(
            "AgentContainer '%s': %d face tools registradas", self.agent_config.id, len(tools)
        )

    def _build_discovery_section(
        self,
        get_agent_container: Callable[[str], "AgentContainer | None"],
        sub_agent_ids: list[str] | None = None,
    ) -> str:
        """
        Build a human-readable section listing available delegation targets.

        Returns an empty string when:
        - sub_agent_ids is empty (sin sub-agentes)
        - todos los IDs resuelven a None (containers no encontrados)

        Format:

            # Available agents for delegation

            You can delegate tasks to other agents via the `delegate` tool.

            ## When to delegate
            - ...heuristics...

            ## When NOT to delegate
            - ...anti-heuristics...

            ## Available agents

            - **<id>** (<name>) — <description>.
              Tools: <tool1>, <tool2>, ...
        """
        target_ids = sub_agent_ids or []

        if not target_ids:
            return ""

        lines: list[str] = []
        for target_id in target_ids:
            target_container = get_agent_container(target_id)
            if target_container is None:
                logger.debug(
                    "AgentContainer '%s': target '%s' not found in registry — skipping in discovery",
                    self.agent_config.id,
                    target_id,
                )
                continue

            name = target_container.agent_config.name
            description = target_container.agent_config.description
            # Collect tool names from the target's registry (all registered tools)
            tool_names = list(target_container._tools._tools.keys())
            tool_list = ", ".join(tool_names) if tool_names else "(no tools)"

            lines.append(f"- **{target_id}** ({name}) — {description}.")
            lines.append(f"  Tools: {tool_list}")

        if not lines:
            # All targets were unknown — do not emit an empty header
            return ""

        header = (
            "# Available agents for delegation\n\n"
            "You can delegate tasks to other agents via the `delegate` tool.\n\n"
            "## When to delegate\n\n"
            "- The task matches another agent's specialty "
            "(see their description and tools below).\n"
            "- You lack a tool that the target agent has.\n"
            "- The task requires multiple tool calls to complete, especially multi-step "
            'workflows like: "search the web about X, summarize the highlights, and send '
            'the result to Y". Delegating keeps your context clean and lets a specialized '
            "agent orchestrate the steps.\n\n"
            "## When NOT to delegate\n\n"
            "- The task is trivial or you already have the tools to solve it in 1-2 steps.\n"
            "- You need tight back-and-forth with the user — the child is stateless and "
            "returns a single structured result.\n"
            "- You already delegated the same task and it failed — try a different approach "
            "or ask the user.\n\n"
            "## Available agents\n"
        )
        return "\n" + header + "\n" + "\n".join(lines)

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
                        ext_name,
                        exc,
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
                                ext_name,
                                tool_instance.name,
                            )
                            continue
                        self._tools.register(tool_instance)
                        logger.info(
                            "Extensión '%s': tool '%s' registrada",
                            ext_name,
                            tool_instance.name,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Extensión '%s': falló al instanciar %r (%s) — skipping tool",
                            ext_name,
                            tool_cls,
                            exc,
                        )

                # Registrar skills
                for skill_rel in getattr(module, "SKILLS", []) or []:
                    skill_path = (manifest_path.parent / skill_rel).resolve()
                    if not skill_path.exists():
                        logger.warning(
                            "Extensión '%s': skill file no encontrado: %s",
                            ext_name,
                            skill_path,
                        )
                        continue
                    self._skills.add_file(skill_path)
                    logger.info(
                        "Extensión '%s': skill '%s' añadida",
                        ext_name,
                        skill_path.name,
                    )

                # Registrar knowledge sources — compatibilidad hacia atrás:
                # manifests sin KNOWLEDGE_SOURCES simplemente no declaran el atributo.
                for factory in getattr(module, "KNOWLEDGE_SOURCES", []) or []:
                    try:
                        fuente = factory(
                            self.agent_config,
                            self._global_config,
                            self._embedder,
                        )
                        self._pending_knowledge_sources.append(fuente)
                        logger.info(
                            "Extensión '%s': knowledge source '%s' registrada",
                            ext_name,
                            fuente.source_id,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Extensión '%s': factory de knowledge source falló (%s) — skipping",
                            ext_name,
                            exc,
                        )

        # El KnowledgeOrchestrator ya fue construido con una referencia a la misma lista
        # _pending_knowledge_sources. Al añadir fuentes de nivel 3 (extensiones) arriba,
        # el orquestrador las ve automáticamente porque comparte el mismo objeto lista.
        # Orden garantizado: (1) memoria, (2) config, (3) extensiones.
        fuentes_total = getattr(self, "_pending_knowledge_sources", None)
        if fuentes_total is not None:
            agent_id = getattr(self, "agent_config", None)
            agent_id = agent_id.id if agent_id is not None else "<desconocido>"
            logger.debug(
                "AgentContainer '%s': KnowledgeOrchestrator actualizado con %d fuente(s) total",
                agent_id,
                len(fuentes_total),
            )


class AppContainer:
    """Container raíz. Carga todos los agentes al arrancar."""

    def __init__(self, global_config: GlobalConfig, registry: AgentRegistry) -> None:
        self.global_config = global_config
        self.registry = registry
        self.agents: dict[str, AgentContainer] = {}

        # Registro de bots de Telegram — el daemon runner los registra al arrancar
        self._telegram_bots: dict[str, object] = {}

        # Phase 1: build all AgentContainers (existing loop, unchanged)
        for agent_cfg in registry.list_all():
            try:
                self.agents[agent_cfg.id] = AgentContainer(agent_cfg, global_config)
                logger.info("AgentContainer creado para '%s'", agent_cfg.id)
            except Exception as exc:
                logger.error("Error creando container para agente '%s': %s", agent_cfg.id, exc)

        # Phase 2: wire delegation AFTER all containers are built so that the
        # get_agent_container closure can resolve any sibling (two-phase init).
        # Solo los agentes regulares reciben el delegate tool; los sub-agentes son
        # el destino de la delegación (not the source) y se ejecutan one-shot.
        def _get_agent_container(agent_id: str) -> "AgentContainer | None":
            return self.agents.get(agent_id)

        sub_agent_ids = [cfg.id for cfg in registry.list_sub_agents()]

        for agent_id, container in self.agents.items():
            if registry.is_sub_agent(agent_id):
                continue
            try:
                container.wire_delegation(_get_agent_container, sub_agent_ids)
            except Exception as exc:
                logger.error("Error en wire_delegation para agente '%s': %s", agent_id, exc)

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
        self.scheduler_repo = SQLiteSchedulerRepo(scheduler_cfg.db_filename)
        self.schedule_task_uc = ScheduleTaskUseCase(
            repo=self.scheduler_repo,
            on_mutation=self._on_scheduler_mutation,
        )
        telegram_sink = TelegramSink(get_telegram_bot=self._get_telegram_bot)
        sink_factory = SinkFactory(get_telegram_bot=self._get_telegram_bot)
        channel_router = ChannelRouter(
            native_sinks={"telegram": telegram_sink},
            fallback_config=scheduler_cfg.channel_fallback,
            sink_factory=sink_factory.from_target,
        )
        dispatch_ports = SchedulerDispatchPorts(
            channel_sender=channel_router,
            llm_dispatcher=LLMDispatcherAdapter(self.agents),
            consolidator=ConsolidationDispatchAdapter(self.consolidate_all_agents),
            http_caller=HttpCallerAdapter(),
        )
        self.scheduler_service = SchedulerService(
            repo=self.scheduler_repo,
            dispatch=dispatch_ports,
            config=scheduler_cfg,
        )

        # Phase 3: wire scheduler tool into each agent now that schedule_task_uc is ready.
        # Sub-agentes excluidos: se ejecutan one-shot y no programan tareas directamente.
        user_timezone = global_config.user.timezone
        for agent_id, container in self.agents.items():
            if registry.is_sub_agent(agent_id):
                continue
            try:
                container.wire_scheduler(self.schedule_task_uc, user_timezone)
            except Exception as exc:
                logger.error("Error en wire_scheduler para agente '%s': %s", agent_id, exc)

        # Phase 4: wire broadcast adapters.
        # Runs AFTER Phase 1 (todos los containers existen) y después de Phase 2+3.
        # Por cada agente con un canal telegram que incluya bloque broadcast:, se
        # instancia un TcpBroadcastAdapter (+ BroadcastBuffer + FixedWindowRateLimiter)
        # y se almacena en el container. El lifecycle (start/stop) se gestiona en
        # AppContainer.startup() / shutdown().
        self._broadcast_adapters: list[object] = []  # TcpBroadcastAdapter instances
        for agent_cfg in registry.list_regular():
            try:
                self._wire_broadcast_for_agent(agent_cfg)
            except Exception as exc:
                logger.error("Error en wire_broadcast para agente '%s': %s", agent_cfg.id, exc)

        # Phase 5: wire photos — singletons compartidos (vision lazy, face registry)
        # + per-agent wiring (scene describer, annotator, metadata repo, use case, tools).
        self._vision_adapter = None
        self._face_registry_adapter = None
        photos_cfg = getattr(global_config, "photos", None)
        if photos_cfg is not None and photos_cfg.enabled:
            try:
                from pathlib import Path

                from adapters.outbound.faces.sqlite_face_registry import (
                    SqliteFaceRegistryAdapter,
                )
                from adapters.outbound.vision.insightface_adapter import (
                    InsightFaceVisionAdapter,
                )

                # faces.db: misma carpeta que el history.db del primer agente.
                first_agent = next(iter(self.agents.values()), None)
                if first_agent is not None:
                    history_path = Path(first_agent.agent_config.chat_history.db_filename)
                    faces_db_path = str(history_path.parent / "faces.db")
                else:
                    faces_db_path = "~/.inaki/data/faces.db"

                self._vision_adapter = InsightFaceVisionAdapter(photos_cfg.faces.model)
                self._face_registry_adapter = SqliteFaceRegistryAdapter(
                    faces_db_path, embedding_dim=512
                )
                logger.info(
                    "Photos singletons inicializados (faces.db=%s, vision=lazy)", faces_db_path
                )
            except Exception as exc:
                logger.error("Error inicializando photos singletons: %s", exc)

        for agent_id, container in self.agents.items():
            if registry.is_sub_agent(agent_id):
                continue
            try:
                container.wire_photos(
                    self._vision_adapter,
                    self._face_registry_adapter,
                    global_config,
                )
            except Exception as exc:
                logger.error("Error en wire_photos para agente '%s': %s", agent_id, exc)

        # Phase 6: wire memory extractor sub-agents.
        # Si memory.llm.agent_id apunta a un sub-agente, le pasamos su
        # run_agent_one_shot al ConsolidateMemoryUseCase. Si el agent_id
        # no existe o no es un sub-agente, se loggea ERROR y la consolidación
        # cae de vuelta al prompt hardcodeado + LLM resuelto (graceful).
        delegation_cfg = global_config.delegation
        for agent_id, container in self.agents.items():
            if registry.is_sub_agent(agent_id):
                continue
            if container.consolidate_memory is None:
                continue
            mem_llm = container.agent_config.memory.llm
            extractor_id = mem_llm.agent_id if mem_llm is not None else None
            if not extractor_id:
                continue
            extractor_container = self.agents.get(extractor_id)
            if extractor_container is None:
                logger.error(
                    "Agente '%s': memory.llm.agent_id='%s' no existe — "
                    "consolidación usará el prompt extractor por defecto",
                    agent_id,
                    extractor_id,
                )
                continue
            if not registry.is_sub_agent(extractor_id):
                logger.error(
                    "Agente '%s': memory.llm.agent_id='%s' debe apuntar a un "
                    "sub-agente (en agents/sub-agents/), no a un agente regular — "
                    "consolidación usará el prompt extractor por defecto",
                    agent_id,
                    extractor_id,
                )
                continue
            container.consolidate_memory.set_extractor(
                extractor_container.run_agent_one_shot,
                max_iterations=delegation_cfg.max_iterations_per_sub,
                timeout_seconds=delegation_cfg.timeout_seconds,
            )
            logger.info(
                "Agente '%s': memory extractor wired → sub-agente '%s'",
                agent_id,
                extractor_id,
            )

    def _wire_broadcast_for_agent(self, agent_cfg: AgentConfig) -> None:
        """
        Instancia y almacena el TcpBroadcastAdapter para un agente, si aplica.

        Reglas:
        - Solo aplica si el agente tiene channel ``telegram`` con bloque ``broadcast:``.
        - Si broadcast.port → rol server; host = "0.0.0.0".
        - Si broadcast.remote → rol client; host/port derivados de remote.host ("ip:port").
        - Se almacena ``broadcast_adapter`` y ``broadcast_rate_limiter`` en el
          AgentContainer correspondiente.
        - Si el agente no tiene container (falló en Phase 1) se omite silenciosamente.
        """
        from adapters.broadcast.tcp import TcpBroadcastAdapter

        container = self.agents.get(agent_cfg.id)
        if container is None:
            return

        tg_raw = agent_cfg.channels.get("telegram")
        if tg_raw is None:
            return

        # Coercionar a TelegramChannelConfig para acceso tipado.
        try:
            tg_cfg = TelegramChannelConfig.model_validate(tg_raw)
        except Exception as exc:
            logger.warning(
                "Agente '%s': no se pudo parsear TelegramChannelConfig — "
                "broadcast wiring omitido: %s",
                agent_cfg.id,
                exc,
            )
            return

        broadcast_cfg = tg_cfg.broadcast
        if broadcast_cfg is None:
            # Sin bloque broadcast → nada que hacer.
            return

        # Determinar rol y parámetros de conexión.
        # El validador de BroadcastConfig garantiza port XOR remote y auth obligatorio
        # en modo server — cast para mypy que no puede inferirlo en este scope.
        role: Literal["server", "client"]
        auth_str: str

        if broadcast_cfg.port is not None:
            # Modo server: escucha en todas las interfaces de la LAN.
            role = "server"
            host = "0.0.0.0"
            port = broadcast_cfg.port
            # auth requerido en server mode — validado por BroadcastConfig
            auth_str = broadcast_cfg.auth  # type: ignore[assignment]
        else:
            # Modo client: remote.host tiene formato "ip:port".
            # BroadcastConfig validator garantiza que remote is not None en este branch.
            role = "client"
            assert broadcast_cfg.remote is not None  # satisface narrowing de mypy
            remote = broadcast_cfg.remote
            remote_parts = remote.host.rsplit(":", 1)
            if len(remote_parts) != 2:
                logger.error(
                    "Agente '%s': broadcast.remote.host='%s' no tiene formato 'ip:port' — "
                    "broadcast wiring omitido",
                    agent_cfg.id,
                    remote.host,
                )
                return
            host = remote_parts[0]
            try:
                port = int(remote_parts[1])
            except ValueError:
                logger.error(
                    "Agente '%s': broadcast.remote.host='%s' — puerto no es entero — "
                    "broadcast wiring omitido",
                    agent_cfg.id,
                    remote.host,
                )
                return
            auth_str = remote.auth

        buffer = BroadcastBuffer()
        rate_limiter = FixedWindowRateLimiter(
            window_seconds=float(broadcast_cfg.rate_limiter_window)
        )
        adapter = TcpBroadcastAdapter(
            agent_id=agent_cfg.id,
            role=role,
            host=host,
            port=port,
            auth=auth_str,
            buffer=buffer,
        )

        container.broadcast_adapter = adapter
        container.broadcast_rate_limiter = rate_limiter
        self._broadcast_adapters.append(adapter)

        logger.info(
            "Agente '%s': broadcast adapter wired (role=%s, host=%s, port=%d)",
            agent_cfg.id,
            role,
            host,
            port,
        )

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

        existing = await self.scheduler_repo.get_task(CONSOLIDATE_MEMORY_TASK_ID)

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
            updated = existing.model_copy(
                update={
                    "schedule": new_schedule,
                    "next_run": new_next_run,
                    "status": new_status,
                    "retry_count": new_retry,
                }
            )
            await self.scheduler_repo.save_task(updated)

    async def _reconcile_face_dedup_task(self) -> None:
        """Garantiza que la tarea builtin `face_dedup_nightly` en la DB refleja la config actual.

        No-op si photos está deshabilitado o si dedup.enabled=False.
        Si existe y el schedule cambió → actualiza next_run.
        Si status FAILED → resetea a PENDING.
        """
        photos_cfg = getattr(self.global_config, "photos", None)
        if photos_cfg is None or not photos_cfg.enabled:
            return
        if not photos_cfg.dedup.enabled:
            return

        # Elegir el primer agente que tiene photos wired.
        agent_id = next(
            (
                aid
                for aid, container in self.agents.items()
                if container.process_photo is not None
            ),
            None,
        )
        if agent_id is None:
            logger.warning("face_dedup_nightly: no hay agentes con photos wired — omitido")
            return

        target_schedule = photos_cfg.dedup.schedule
        target = build_face_dedup_task(target_schedule, agent_id)

        await self.scheduler_repo.ensure_schema()
        existing = await self.scheduler_repo.get_task(FACE_DEDUP_TASK_ID)

        if existing is None:
            await self.scheduler_repo.seed_builtin(target)
            logger.info(
                "Tarea builtin face_dedup_nightly sembrada (agent=%s, schedule='%s')",
                agent_id,
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
                "face_dedup_nightly: schedule actualizado '%s' → '%s'",
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
            logger.info("face_dedup_nightly: estado FAILED reseteado a PENDING")
            needs_save = True

        if new_next_run is None:
            new_next_run = datetime.fromtimestamp(
                croniter(new_schedule, now).get_next(), tz=timezone.utc
            )
            needs_save = True

        if needs_save:
            updated = existing.model_copy(
                update={
                    "schedule": new_schedule,
                    "next_run": new_next_run,
                    "status": new_status,
                    "retry_count": new_retry,
                }
            )
            await self.scheduler_repo.save_task(updated)

    async def startup(self) -> None:
        """Arranca el scheduler service y los adapters de broadcast. Llamar en el daemon lifecycle."""
        if self.global_config.scheduler.enabled:
            await self._reconcile_consolidate_memory_task()
            await self._reconcile_face_dedup_task()
            await self.scheduler_service.start()
            logger.info("SchedulerService iniciado")

        # Arrancar todos los adapters de broadcast (start es idempotente).
        for adapter in self._broadcast_adapters:
            try:
                await adapter.start()  # type: ignore[attr-defined]
                logger.info(
                    "broadcast adapter iniciado: role=%s host=%s port=%d",
                    adapter._role,  # type: ignore[attr-defined]
                    adapter._host,  # type: ignore[attr-defined]
                    adapter._port,  # type: ignore[attr-defined]
                )
            except Exception as exc:
                logger.error("Error arrancando broadcast adapter: %s", exc)

    async def shutdown(self) -> None:
        """Detiene el scheduler service y los adapters de broadcast graciosamente."""
        await self.scheduler_service.stop()
        logger.info("SchedulerService detenido")

        # Detener todos los adapters de broadcast (stop es idempotente).
        for adapter in self._broadcast_adapters:
            try:
                await adapter.stop()  # type: ignore[attr-defined]
            except Exception as exc:
                logger.error("Error deteniendo broadcast adapter: %s", exc)

    def get_agent(self, agent_id: str) -> AgentContainer:
        if agent_id not in self.agents:
            raise AgentNotFoundError(
                f"Agente '{agent_id}' no encontrado o falló al inicializar. "
                f"Disponibles: {list(self.agents)}"
            )
        return self.agents[agent_id]
