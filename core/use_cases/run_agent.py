"""
RunAgentUseCase — orquesta un turno completo de conversación.

Flujo completo:
  1. Cargar historial del agente
  2. Leer digest markdown de memoria
  3. Listar skills y tools disponibles
  4. Si semantic routing activo: generar embedding y filtrar skills/tools relevantes
  5. Construir AgentContext y system prompt dinámico
  6. Llamar al LLM con historial + tools disponibles
  7. Si el LLM devuelve tool calls → ejecutar tools, añadir resultados, rellamar LLM
  8. Persistir solo los mensajes user/assistant en historial
  9. Devolver respuesta final

El historial que se guarda en fichero NO incluye mensajes de tipo tool ni tool_result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from core.domain.entities.message import Message, Role
from core.domain.entities.skill import Skill
from core.domain.errors import ToolLoopMaxIterationsError
from core.domain.services.knowledge_orchestrator import KnowledgeOrchestrator
from core.domain.value_objects.agent_context import AgentContext
from core.domain.value_objects.agent_info import AgentInfoDTO
from core.domain.value_objects.channel_context import (
    ChannelContext,
    current_channel_context,
    reset_current_channel_context,
    set_current_channel_context,
)
from core.domain.value_objects.conversation_state import ConversationState
from core.ports.outbound.background_delegation_port import IBackgroundDelegationQueue
from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.history_port import IHistoryStore
from core.ports.outbound.intermediate_sink_port import IIntermediateSink
from core.ports.outbound.llm_port import ILLMProvider
from core.ports.outbound.memory_port import IMemoryRepository
from core.ports.outbound.skill_port import ISkillRepository
from core.ports.outbound.tool_port import IToolExecutor
from core.use_cases._tool_loop import run_tool_loop
from core.use_cases._turn_pipeline import (
    INFLIGHT_CLARIFICATIONS_SECTION,
    assemble_turn_messages,
    extract_trailing_user_batch,
    prefetch_knowledge,
    render_in_flight_section,
    run_semantic_routing,
    should_bypass_routing_for_short_input,
    warn_if_token_budget_exceeded,
    write_debug_phase2,
)
from core.domain.value_objects.agent_settings import RunAgentSettings

logger = logging.getLogger(__name__)


@dataclass
class InspectResult:
    """
    Resultado del pipeline de inspect (sin LLM).

    selected_skill_scores / selected_tool_scores: pares (id o nombre, similitud coseno)
    solo cuando el RAG correspondiente estuvo activo; si no, listas vacías.
    """

    user_input: str
    memory_digest: str
    all_skills: list[Skill]
    selected_skills: list[Skill]
    skills_routing_active: bool
    selected_skill_scores: list[tuple[str, float]]
    all_tool_schemas: list[dict]
    selected_tool_schemas: list[dict]
    tools_routing_active: bool
    selected_tool_scores: list[tuple[str, float]]
    system_prompt: str


class RunAgentUseCase:
    def __init__(
        self,
        llm: ILLMProvider,
        memory: IMemoryRepository,
        embedder: IEmbeddingProvider,
        skills: ISkillRepository,
        history: IHistoryStore,
        tools: IToolExecutor,
        settings: RunAgentSettings,
        knowledge_orchestrator: KnowledgeOrchestrator | None = None,
        background_queue: IBackgroundDelegationQueue | None = None,
        thinking_indicator: bool = False,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._embedder = embedder
        self._skills = skills
        self._history = history
        self._tools = tools
        self._settings = settings
        # Flag transversal del bloque global ``channels.thinking_indicator``.
        # Lo wirea el container desde ``GlobalConfig.channels.thinking_indicator``;
        # default ``False`` para tests que construyen el use case directo.
        self._thinking_indicator = thinking_indicator
        # KnowledgeOrchestrator — None si no hay fuentes configuradas
        self._knowledge_orchestrator = knowledge_orchestrator
        # IBackgroundDelegationQueue — None hasta que se wiree en AppContainer.
        # Cuando está set, execute() inyecta una sección con el snapshot de
        # delegaciones in-flight en cada turno (REQ-BGD-7).
        self._background_queue = background_queue
        # Extra sections injected by wire_delegation (task 6.1).
        # Empty by default — non-breaking when delegation is disabled.
        self._extra_system_sections: list[str] = []
        # Timezone del usuario para resolver {{TIMEZONE}}/{{DATETIME}}/etc. en el system prompt.
        # None → fallback a TZ local del sistema. Se puede inyectar via wire_user_timezone().
        self._user_timezone: str | None = None
        # Ruta del archivo de debug de foto. Si está seteado, execute() escribe Phase 2 y lo limpia.
        self._photo_debug_path: str | None = None

    def wire_user_timezone(self, tz: str | None) -> None:
        """Inyecta la timezone del usuario para la interpolación de variables en el system prompt."""
        self._user_timezone = tz

    def set_background_queue(self, queue: IBackgroundDelegationQueue | None) -> None:
        """Inyecta la cola de background-delegation tras la construcción del use case.

        Se llama desde ``wire_delegation`` cuando el ``AppContainer`` ya tiene
        construido el ``BackgroundDelegationQueueAdapter``. Encapsulación limpia
        del two-phase init: el use case se construye sin queue, y la recibe
        cuando todos los containers existen.
        """
        self._background_queue = queue

    def get_agent_info(self) -> AgentInfoDTO:
        """Retorna información pública del agente sin exponer _cfg."""
        return AgentInfoDTO(
            id=self._settings.agent_id,
            name=self._settings.name,
            description=self._settings.description,
        )

    def set_photo_debug_path(self, path: str | None) -> None:
        """Registra la ruta del archivo de debug de foto para el próximo execute().

        Llamado por el adapter de Telegram antes de invocar _run_pipeline cuando
        photos.debug=True. execute() escribe Phase 2 (historial + system prompt +
        mensajes al LLM) y limpia la ruta después de escribir.
        """
        self._photo_debug_path = path

    def set_extra_system_sections(self, sections: list[str]) -> None:
        """
        Set additional system-prompt sections (e.g. agent-discovery).

        Called by AgentContainer.wire_delegation after constructing the
        discovery section. Safe to call multiple times — replaces the list.
        """
        self._extra_system_sections = list(sections)

    def _snapshot_sender(
        self, ctx: ChannelContext | None
    ) -> tuple[str | None, str | None, str | None, str | None]:
        """Devuelve ``(sender_name, username, first_name, last_name)`` del turno.

        El ``ChannelContext`` puede ser ``None`` (canal que no lo pasa, ej: scheduler
        triggers que invocan ``execute`` sin pasar por un adapter inbound), y
        cualquiera de los 4 campos puede ser ``None`` (usuario sin ``@username``,
        canal sin first_name, grupos donde no hay un único sender, etc.). En todos
        esos casos las variables ``{{CHANNEL.*}}`` correspondientes quedan literales
        en el system prompt — mismo criterio que ``{{WORKSPACE}}``.
        """
        if ctx is None:
            return (None, None, None, None)
        return (ctx.sender_name, ctx.username, ctx.first_name, ctx.last_name)

    def _read_user_context(self, ctx: ChannelContext | None) -> str:
        """Lee el contexto per-user para el sender del turno actual.

        Concatena dos capas (la primera que falte se omite):

          1. ``~/.inaki/users/{channel_type}/_common.md`` — contexto común a
             TODOS los usuarios del canal (ej: formato de respuesta, "no uses
             tablas markdown en Telegram"). Se inyecta ANTES del archivo per-user.
             Prefijo ``_`` para no colisionar con un ``{username}.md`` que se
             llamara "common".
          2. El primer archivo per-user que exista, en este orden:
             a. ``~/.inaki/users/{channel_type}/{username}.md``
             b. ``~/.inaki/users/{channel_type}/{user_id}.md``

        Scope por canal: ``alberto`` en telegram ≠ ``alberto`` en cli — cada canal
        tiene su propio directorio. Username preferente porque es el handle humano
        legible; fallback a ``user_id`` para usuarios sin ``@username`` configurado
        (Telegram lo permite). El nombre se sanitiza: si contiene separadores de
        path o ``..`` se descarta el lookup (defensa básica contra path traversal,
        aunque los valores vienen del canal — paranoia barata).

        Sin ``ChannelContext`` (ej: scheduler triggers que no pasan por un adapter
        inbound) o ningún archivo presente → ``""``. Mismo criterio que el digest.
        """
        if ctx is None or not self._settings.users_dir:
            return ""

        base = Path(self._settings.users_dir) / ctx.channel_type

        instructions = ""
        try:
            instructions = (base / "_common.md").read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            pass

        user_specific = ""
        for candidate in (ctx.username, ctx.user_id):
            if not candidate or any(sep in candidate for sep in ("/", "\\", "..")):
                continue
            try:
                user_specific = (base / f"{candidate}.md").read_text(encoding="utf-8")
                break
            except (FileNotFoundError, OSError):
                continue

        return "\n\n".join(part for part in (instructions, user_specific) if part.strip())

    def _read_digest(self, channel: str | None = None, chat_id: str | None = None) -> str:
        """
        Lee el digest markdown del scope ``(channel, chat_id)``. Retorna ``''``
        si no existe o falla la lectura. ``None`` o cadena vacía en cualquier
        componente del scope se sanitizan a ``"default"`` (ver
        ``MemorySettings.resolved_digest_path``).
        """
        path = self._settings.memory.resolved_digest_path(channel, chat_id)
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.debug("Digest no encontrado en %s — primera vez o sin consolidate", path)
            return ""
        except UnicodeDecodeError as exc:
            logger.warning("Digest %s con encoding inválido, ignorando: %s", path, exc)
            return ""
        except OSError as exc:
            logger.warning("No se pudo leer el digest %s: %s", path, exc)
            return ""

    async def record_user_message(
        self,
        content: str,
        channel: str = "",
        chat_id: str = "",
    ) -> None:
        """Persiste un mensaje `role=user` en el historial sin invocar al LLM.

        Pensado para flujos de grupo donde múltiples mensajes (de varios usuarios o
        bots vía broadcast) llegan dentro de una ventana de delay y se acumulan en
        el historial individualmente. Cuando el delay vence, ``execute()`` se llama
        sin ``user_input`` y deriva el "turno actual" del trailing batch del historial.
        """
        msg = Message(role=Role.USER, content=content)
        await self._history.append(self._settings.agent_id, msg, channel=channel, chat_id=chat_id)

    async def record_photo_message(
        self,
        content: str,
        channel: str = "",
        chat_id: str = "",
    ) -> int:
        """Persiste un mensaje `role=user` y devuelve el history_id de la fila insertada.

        Usado por el handler de fotos de Telegram para obtener el ``history_id``
        necesario en ``ProcessPhotoUseCase.execute()``.
        """
        msg = Message(role=Role.USER, content=content)
        row_id = await self._history.append(
            self._settings.agent_id, msg, channel=channel, chat_id=chat_id
        )
        return row_id or 0

    async def update_message_content(
        self,
        message_id: int,
        new_content: str,
    ) -> bool:
        """Reemplaza el contenido de un mensaje persistido manteniendo su ``id`` y ``created_at``.

        Usado por el handler de fotos para enriquecer el placeholder ``__PHOTO__`` con
        el ``text_context`` final del descriptor de escena, evitando un segundo mensaje
        ``role=user`` consecutivo en el historial.
        """
        return await self._history.update_content(self._settings.agent_id, message_id, new_content)

    async def record_assistant_message(
        self,
        content: str,
        channel: str = "",
        chat_id: str = "",
    ) -> None:
        """Persiste un mensaje `role=assistant` en el historial sin invocar al LLM.

        Usado cuando el sistema genera una respuesta directa (ej: transcripción de imagen)
        que debe quedar en el historial para que el usuario pueda iterar sobre ella.
        """
        msg = Message(role=Role.ASSISTANT, content=content)
        await self._history.append(self._settings.agent_id, msg, channel=channel, chat_id=chat_id)

    async def execute(
        self,
        user_input: str | None = None,
        tools_override: list[dict] | None = None,
        intermediate_sink: IIntermediateSink | None = None,
        ctx: ChannelContext | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        ephemeral: bool = False,
        skip_marker: str | None = None,
    ) -> str:
        """Ejecuta un turno del agente.

        Args:
            user_input: mensaje del usuario para este turno. Si es ``None``, se
                asume que el caller pre-persistió uno o varios mensajes vía
                ``record_user_message`` y la "query" del turno se deriva del
                trailing batch de ``role=user`` en el historial. Modo usado por
                el flush de buffer de grupo.
            tools_override: si se provee, fuerza ese conjunto de tool schemas
                y bypasea la selección RAG de tools. La selección RAG de skills
                sigue activa. Usado por triggers ``agent_send`` del scheduler
                para restringir qué tools puede usar el agente en ese turno.
            intermediate_sink: si se provee, recibe los bloques de texto
                emitidos por el LLM junto con tool_calls (mensajes intermedios
                tipo "ok, voy a buscar..."). El mensaje FINAL del turno NO pasa
                por el sink — se retorna por el return. Default ``None`` → no
                se emiten intermedios (backwards-compat).
            ctx: ``ChannelContext`` del turno (identidad del sender + canal de
                origen). Viaja con la llamada — NO hay estado compartido entre
                turnos: durante el turno se publica en un ``contextvars.ContextVar``
                que las tools leen vía ``AgentContainer.get_channel_context()``.
                ``None`` para paths sin conversación (scheduler triggers, tests).
            channel: scope de historial del turno (ej: ``"telegram"``, ``"cli"``).
                ``None`` (default) deriva de ``ctx.channel_type`` (o ``""`` sin
                ctx). Pasar ``""`` explícito fuerza el scope legacy aunque haya
                ctx — usado por el admin REST hasta que se decida la semántica
                de scope de esa superficie.
            chat_id: identificador del chat dentro del scope. ``None`` (default)
                deriva de ``ctx.chat_id`` (o ``""``). Mismas reglas que ``channel``.
            ephemeral: si True, carga el historial para contexto pero NO persiste
                el turno ni actualiza el estado sticky. Usado por ``--task``.
                Solo aplica cuando ``user_input`` es provisto.
            skip_marker: si este string aparece en CUALQUIER parte de la respuesta
                del LLM (comparación case-insensitive), NO se persiste el turno
                (ni user_msg ni assistant ni state). Útil para markers como
                ``__SKIP__`` que indican "no aportar nada en este turno" en flujos
                broadcast/autonomous. La detección es tolerante a propósito: los
                LLMs no siempre cumplen "respondé EXACTAMENTE con __SKIP__" y
                suelen agregar pre/post-amble — antes era estricto y dejaba pasar
                el marker al chat. Default ``None`` → siempre se persiste.
        """
        # Una sola fuente de verdad: con ctx presente, el scope (channel, chat_id)
        # se deriva de él salvo override explícito del caller.
        effective_channel = channel if channel is not None else (ctx.channel_type if ctx else "")
        effective_chat_id = chat_id if chat_id is not None else ((ctx.chat_id or "") if ctx else "")

        # Publicar el contexto del turno para las tools (scheduler, faces, telegram,
        # delegate). Set incondicional — también con ctx=None — para que un execute()
        # anidado (delegación sync) no herede el contexto del turno padre.
        token = set_current_channel_context(ctx)
        try:
            return await self._execute_turn(
                user_input=user_input,
                tools_override=tools_override,
                intermediate_sink=intermediate_sink,
                ctx=ctx,
                channel=effective_channel,
                chat_id=effective_chat_id,
                ephemeral=ephemeral,
                skip_marker=skip_marker,
            )
        finally:
            reset_current_channel_context(token)

    async def _execute_turn(
        self,
        user_input: str | None,
        tools_override: list[dict] | None,
        intermediate_sink: IIntermediateSink | None,
        ctx: ChannelContext | None,
        channel: str,
        chat_id: str,
        ephemeral: bool,
        skip_marker: str | None,
    ) -> str:
        """Cuerpo del turno. ``channel``/``chat_id`` llegan ya normalizados por ``execute``.

        Orquestador puro: encadena las fases de ``_turn_pipeline`` (routing,
        knowledge, ensamblado de mensajes) y el ``run_tool_loop``, reteniendo
        acá solo lo que toca estado del use case (historial, persistencia,
        sticky state, debug de foto).
        """
        agent_id = self._settings.agent_id

        # Snapshot antes del primer await para evitar carrera con flushes concurrentes
        # de distintos grupos: set_extra_system_sections puede ser sobreescrito por otro
        # flush mientras este execute() espera en la carga del historial.
        extra_sections_snapshot = list(self._extra_system_sections)

        # REQ-BGD-7: inyectar sección de delegaciones in-flight si la queue está
        # wired y hay tasks pendientes para este agente. snapshot_inflight() es
        # sync — no await — y el adapter ya purgó las completadas tras dispatch.
        if self._background_queue is not None:
            inflight_snap = self._background_queue.snapshot_inflight(agent_id)
            if inflight_snap:
                extra_sections_snapshot.append(render_in_flight_section(inflight_snap))

        # Sección estática que le explica al LLM cómo interpretar mensajes
        # role=user que aparezcan en medio del tool loop (in-flight-message-injection).
        # Siempre presente: si nunca aparece un mensaje mid-loop, el LLM
        # ignora la guidance sin costo. ~100 palabras.
        extra_sections_snapshot.append(INFLIGHT_CLARIFICATIONS_SECTION)

        # Aislar historial por (channel, chat_id) salvo que merge_chats esté activo.
        # Sin filtro, el LLM recibiría mensajes de otros chats del mismo agente
        # (p. ej. privado de Telegram viendo mensajes del grupo).
        if self._settings.merge_chats:
            history = await self._history.load(agent_id)
        else:
            history = await self._history.load(agent_id, channel=channel, chat_id=chat_id)

        # Modo "history-derived": el caller ya persistió los mensajes vía
        # ``record_user_message``. La query para embedding/routing se deriva del
        # trailing batch de role=user del historial.
        if user_input is not None:
            query: str = user_input
        else:
            query = extract_trailing_user_batch(history)
            if not query:
                logger.warning(
                    "execute() llamado sin user_input pero el historial no tiene "
                    "trailing role=user (agent=%s, channel=%s, chat_id=%s) — abortando turno",
                    agent_id,
                    channel,
                    chat_id,
                )
                return ""

        digest_text = self._read_digest(channel=channel, chat_id=chat_id)
        prev_state = await self._history.load_state(agent_id, channel=channel, chat_id=chat_id)

        routing = await run_semantic_routing(
            query=query,
            tools_override=tools_override,
            prev_state=prev_state,
            settings=self._settings,
            embedder=self._embedder,
            skills=self._skills,
            tools=self._tools,
        )
        knowledge_chunks, _ = await prefetch_knowledge(
            routing_bypass=routing.routing_bypass,
            orchestrator=self._knowledge_orchestrator,
            embedder=self._embedder,
            query=query,
            query_vec=routing.query_vec,
            agent_id=agent_id,
        )
        warn_if_token_budget_exceeded(
            orchestrator=self._knowledge_orchestrator,
            knowledge_chunks=knowledge_chunks,
            digest_text=digest_text,
            retrieved_skills=routing.retrieved_skills,
            agent_id=agent_id,
        )

        user_context = self._read_user_context(ctx)
        sender_name, sender_username, sender_first_name, sender_last_name = self._snapshot_sender(
            ctx
        )
        context = AgentContext(
            agent_id=agent_id,
            user_context=user_context,
            memory_digest=digest_text,
            skills=routing.retrieved_skills,
            timezone=self._user_timezone,
            workspace_root=self._settings.workspace_root or None,
            channel=channel or None,
            chat_id=chat_id or None,
            sender_name=sender_name,
            sender_username=sender_username,
            sender_first_name=sender_first_name,
            sender_last_name=sender_last_name,
            knowledge_chunks=knowledge_chunks,
        )
        system_prompt = context.build_system_prompt(
            self._settings.system_prompt,
            extra_sections=extra_sections_snapshot or None,
        )

        user_msg, messages = assemble_turn_messages(
            history=history,
            user_input=user_input,
            channel=channel,
            timestamp_channels=self._settings.timestamp_channels,
        )

        if self._photo_debug_path:
            write_debug_phase2(
                debug_path=self._photo_debug_path,
                user_input=user_input,
                channel=channel,
                chat_id=chat_id,
                history=history,
                messages=messages,
                extra_sections=extra_sections_snapshot,
                system_prompt=system_prompt,
            )
            self._photo_debug_path = None

        # Persistir el user_msg ANTES del LLM call. Si el provider tira
        # (timeout, 4xx, 5xx, etc.) la pregunta del usuario queda guardada y
        # el próximo turno la puede ver. Antes la persistencia ocurría DESPUÉS
        # del LLM y cualquier error del provider hacía que el mensaje del
        # usuario se perdiera silenciosamente. En el flujo history-derived
        # (user_input=None) el mensaje ya fue persistido vía record_user_message,
        # así que no hay double-persist.
        if not ephemeral and user_msg is not None:
            await self._history.append(agent_id, user_msg, channel=channel, chat_id=chat_id)

        # Baseline del drain del tool loop. Contamos sobre `history` (crudo,
        # pre-coalesce) + 1 si vamos a persistir user_msg ANTES del loop. Sin
        # esto, en el flujo history-derived el `messages` coalesced reporta
        # menos user-msgs que la DB y el drain en checkpoint A reinyecta
        # mensajes que ya están dentro del bloque coalesced → duplicación
        # visible al LLM como "historial clonado" (regresión introducida al
        # combinar in-flight-injection con el coalesce del buffer de grupos).
        initial_db_user_count = sum(1 for m in history if m.role == Role.USER) + (
            1 if user_msg is not None and not ephemeral else 0
        )

        try:
            response = await run_tool_loop(
                llm=self._llm,
                tools=self._tools,
                messages=messages,
                system_prompt=system_prompt,
                tool_schemas=routing.tool_schemas,
                max_iterations=self._settings.tool_call_max_iterations,
                circuit_breaker_threshold=self._settings.circuit_breaker_threshold,
                agent_id=self._settings.agent_id,
                intermediate_sink=intermediate_sink,
                thinking_indicator=self._thinking_indicator,
                request_delay_seconds=self._settings.request_delay_seconds,
                # in-flight-message-injection: activamos drainage pasando el
                # history store y el scope del turno. El loop releerá history
                # entre iteraciones y drenará mensajes role=user nuevos.
                history_store=self._history,
                scope=(agent_id, channel, chat_id),
                initial_db_user_count=initial_db_user_count,
            )
        except ToolLoopMaxIterationsError as e:
            response = e.last_response or (
                "No pude completar la tarea: se alcanzó el límite de "
                "iteraciones de tools sin obtener una respuesta final."
            )

        # Detección tolerante del skip_marker: aceptamos que aparezca en cualquier
        # parte de la respuesta (case-insensitive) — los LLMs no siempre cumplen
        # "respondé EXACTAMENTE con __SKIP__" al pie de la letra y suelen agregar
        # pre/post-amble. Si está presente, descartamos persistencia.
        skip_persist = skip_marker is not None and skip_marker.upper() in response.upper()

        if not ephemeral and not skip_persist:
            if routing.state_dirty:
                await self._history.save_state(
                    agent_id,
                    ConversationState(
                        sticky_skills=routing.new_sticky_skills,
                        sticky_tools=routing.new_sticky_tools,
                    ),
                    channel=channel,
                    chat_id=chat_id,
                )
            await self._history.append(
                agent_id,
                Message(role=Role.ASSISTANT, content=response),
                channel=channel,
                chat_id=chat_id,
            )

        return response

    async def get_history(self) -> list[Message]:
        """Devuelve el historial activo del agente (sin archivados ni infused)."""
        return await self._history.load(self._settings.agent_id)

    async def clear_history(
        self,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        """Limpia el historial activo del agente.

        Si ``channel`` y ``chat_id`` son ``None`` (default) borra TODO el
        historial y resetea ``agent_state``. Si se proveen, borra solo los
        mensajes de ese (channel, chat_id) y preserva ``agent_state``.
        """
        await self._history.clear(self._settings.agent_id, channel=channel, chat_id=chat_id)

    async def inspect(
        self,
        user_input: str,
        channel: str = "",
        chat_id: str = "",
    ) -> InspectResult:
        """
        Corre el pipeline RAG completo sin llamar al LLM ni persistir historial.
        Útil para debuggear qué ve el LLM en cada turno.

        ``channel``/``chat_id`` permiten inspeccionar el digest del scope
        correspondiente. Defaults vacíos → digest del scope ``default``.
        """
        digest_text = self._read_digest(channel=channel, chat_id=chat_id)
        all_skills = await self._skills.list_all()
        all_schemas = self._tools.get_schemas()
        skills_routing_active = len(all_skills) > self._settings.skills_min_skills
        tools_routing_active = len(all_schemas) > self._settings.tools_min_tools
        retrieved_skills: list[Skill] = all_skills
        selected_schemas: list[dict] = all_schemas
        skill_scores: list[tuple[str, float]] = []
        tool_scores: list[tuple[str, float]] = []

        prev_state = await self._history.load_state(
            self._settings.agent_id, channel=channel, chat_id=chat_id
        )
        routing_bypass = should_bypass_routing_for_short_input(
            user_input=user_input,
            min_words_threshold=self._settings.min_words_threshold,
            prev_state=prev_state,
        )

        query_vec: list[float] | None = None

        if routing_bypass:
            # Refleja la realidad de execute(): selección heredada del sticky previo.
            if skills_routing_active and prev_state.sticky_skills:
                skills_by_id = {s.id: s for s in all_skills}
                retrieved_skills = [
                    skills_by_id[i] for i in prev_state.sticky_skills if i in skills_by_id
                ]
            if tools_routing_active and prev_state.sticky_tools:
                schemas_by_name = {sch["function"]["name"]: sch for sch in all_schemas}
                selected_schemas = [
                    schemas_by_name[n] for n in prev_state.sticky_tools if n in schemas_by_name
                ]
        elif skills_routing_active or tools_routing_active:
            query_vec = await self._embedder.embed_query(user_input)
            if skills_routing_active:
                scored_skills = await self._skills.retrieve_with_scores(
                    query_vec,
                    top_k=self._settings.skills_top_k,
                    min_score=self._settings.skills_min_score,
                )
                retrieved_skills = [s for s, _ in scored_skills]
                skill_scores = [(s.id, sc) for s, sc in scored_skills]
            if tools_routing_active:
                scored_tools = await self._tools.get_schemas_relevant_with_scores(
                    query_vec,
                    top_k=self._settings.tools_top_k,
                    min_score=self._settings.tools_min_score,
                )
                selected_schemas = [sch for sch, _ in scored_tools]
                tool_scores = [(sch["function"]["name"], sc) for sch, sc in scored_tools]

        # Pre-fetch de knowledge — misma fase que execute(): skip en bypass o
        # si pre-fetch está deshabilitado.
        knowledge_chunks, _ = await prefetch_knowledge(
            routing_bypass=routing_bypass,
            orchestrator=self._knowledge_orchestrator,
            embedder=self._embedder,
            query=user_input,
            query_vec=query_vec,
            agent_id=self._settings.agent_id,
        )

        inspect_ctx = current_channel_context()
        user_context = self._read_user_context(inspect_ctx)
        sender_name, sender_username, sender_first_name, sender_last_name = self._snapshot_sender(
            inspect_ctx
        )
        context = AgentContext(
            agent_id=self._settings.agent_id,
            user_context=user_context,
            memory_digest=digest_text,
            skills=retrieved_skills,
            timezone=self._user_timezone,
            workspace_root=self._settings.workspace_root or None,
            channel=channel or None,
            chat_id=chat_id or None,
            sender_name=sender_name,
            sender_username=sender_username,
            sender_first_name=sender_first_name,
            sender_last_name=sender_last_name,
            knowledge_chunks=knowledge_chunks,
        )
        system_prompt = context.build_system_prompt(self._settings.system_prompt)

        return InspectResult(
            user_input=user_input,
            memory_digest=digest_text,
            all_skills=all_skills,
            selected_skills=retrieved_skills,
            skills_routing_active=skills_routing_active,
            selected_skill_scores=skill_scores,
            all_tool_schemas=all_schemas,
            selected_tool_schemas=selected_schemas,
            tools_routing_active=tools_routing_active,
            selected_tool_scores=tool_scores,
            system_prompt=system_prompt,
        )
