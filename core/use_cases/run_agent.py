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
from datetime import datetime
from core.domain.entities.message import Message, Role
from core.domain.entities.skill import Skill
from core.domain.errors import ToolLoopMaxIterationsError
from core.domain.services.knowledge_orchestrator import KnowledgeOrchestrator
from core.domain.services.sticky_selector import apply_sticky
from core.domain.value_objects.agent_context import AgentContext
from core.domain.value_objects.agent_info import AgentInfoDTO
from core.domain.value_objects.conversation_state import ConversationState
from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.history_port import IHistoryStore
from core.ports.outbound.intermediate_sink_port import IIntermediateSink
from core.ports.outbound.llm_port import ILLMProvider
from core.ports.outbound.memory_port import IMemoryRepository
from core.ports.outbound.skill_port import ISkillRepository
from core.ports.outbound.tool_port import IToolExecutor
from core.use_cases._tool_loop import run_tool_loop
from infrastructure.config import AgentConfig

logger = logging.getLogger(__name__)


def _workspace_absolute_path(agent_config: AgentConfig) -> str:
    """Raíz del workspace del agente, coherente con `AgentContainer` y las tools de FS."""
    return str(Path(agent_config.workspace.path).expanduser().resolve())


def _extract_trailing_user_batch(history: list[Message]) -> str:
    """Extrae los `role=user` consecutivos al final del historial, concatenados con `\\n`.

    Representa "lo que el usuario (o los usuarios/bots de un grupo) acaban de decir
    desde el último turno del assistant". Vacío si no hay nada al final.
    """
    trailing: list[str] = []
    for msg in reversed(history):
        if msg.role == Role.USER:
            trailing.append(msg.content)
        else:
            break
    trailing.reverse()
    return "\n".join(trailing)


def _coalesce_consecutive_same_role(messages: list[Message]) -> list[Message]:
    """Junta mensajes consecutivos con el mismo rol en uno solo, content joined con `\\n`.

    Necesario cuando el historial puede contener múltiples `role=user` seguidos
    (caso del buffer de grupo): muchos providers de LLM exigen alternación estricta
    user/assistant. Solo coalesce mensajes "limpios" — preserva intactos los que
    tengan `tool_calls` o `tool_call_id` (semántica de tool loop).
    """
    if not messages:
        return []
    result: list[Message] = [messages[0]]
    for msg in messages[1:]:
        prev = result[-1]
        if (
            msg.role == prev.role
            and msg.tool_calls is None
            and msg.tool_call_id is None
            and prev.tool_calls is None
            and prev.tool_call_id is None
        ):
            result[-1] = Message(
                role=prev.role,
                content=prev.content + "\n" + msg.content,
                timestamp=prev.timestamp,
            )
        else:
            result.append(msg)
    return result


def _should_bypass_routing_for_short_input(
    *,
    user_input: str,
    min_words_threshold: int,
    prev_state: ConversationState,
) -> bool:
    """Decide si el turno debe saltear el embed + re-selección de semantic routing.

    Se skipea cuando todas estas condiciones se cumplen:
      - el threshold está activado (``> 0``),
      - el input tiene MENOS palabras que el threshold,
      - existe alguna selección sticky previa (skills o tools) de la cual heredar.

    Si no hay sticky previo (primer turno o TTL ya expiró) el routing corre normal,
    aunque el input sea corto — no hay contexto del cual heredar.
    """
    if min_words_threshold <= 0:
        return False
    if len(user_input.split()) >= min_words_threshold:
        return False
    return bool(prev_state.sticky_skills or prev_state.sticky_tools)


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
        agent_config: AgentConfig,
        knowledge_orchestrator: KnowledgeOrchestrator | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._embedder = embedder
        self._skills = skills
        self._history = history
        self._tools = tools
        self._cfg = agent_config
        # KnowledgeOrchestrator — None si no hay fuentes configuradas
        self._knowledge_orchestrator = knowledge_orchestrator
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

    def get_agent_info(self) -> AgentInfoDTO:
        """Retorna información pública del agente sin exponer _cfg."""
        return AgentInfoDTO(
            id=self._cfg.id,
            name=self._cfg.name,
            description=self._cfg.description,
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

    def _read_user_context(self) -> str:
        """Lee ~/.inaki/USER.md. Retorna '' si no existe."""
        path = Path("~/.inaki/USER.md").expanduser()
        try:
            return path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return ""

    def _read_digest(self) -> str:
        """Lee el digest markdown. Retorna '' si no existe o falla la lectura."""
        path = Path(self._cfg.memory.digest_filename)
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
        await self._history.append(self._cfg.id, msg, channel=channel, chat_id=chat_id)

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
        row_id = await self._history.append(self._cfg.id, msg, channel=channel, chat_id=chat_id)
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
        return await self._history.update_content(self._cfg.id, message_id, new_content)

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
        await self._history.append(self._cfg.id, msg, channel=channel, chat_id=chat_id)

    async def execute(
        self,
        user_input: str | None = None,
        tools_override: list[dict] | None = None,
        intermediate_sink: IIntermediateSink | None = None,
        channel: str = "",
        chat_id: str = "",
        ephemeral: bool = False,
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
            channel: canal de origen del mensaje (ej: ``"telegram"``, ``"cli"``).
                Cadena vacía cuando no aplica.
            chat_id: identificador del chat dentro del canal (ej: ID de grupo
                Telegram). Cadena vacía para chats privados o sin distinción.
            ephemeral: si True, carga el historial para contexto pero NO persiste
                el turno ni actualiza el estado sticky. Usado por ``--task``.
                Solo aplica cuando ``user_input`` es provisto.
        """
        agent_id = self._cfg.id

        # Snapshot antes del primer await para evitar carrera con flushes concurrentes
        # de distintos grupos: set_extra_system_sections puede ser sobreescrito por otro
        # flush mientras este execute() espera en la carga del historial.
        extra_sections_snapshot = list(self._extra_system_sections)

        # Aislar historial por (channel, chat_id) salvo que merge_chats esté activo.
        # Sin filtro, el LLM recibiría mensajes de otros chats del mismo agente
        # (p. ej. privado de Telegram viendo mensajes del grupo).
        if self._cfg.chat_history.merge_chats:
            history = await self._history.load(agent_id)
        else:
            history = await self._history.load(agent_id, channel=channel, chat_id=chat_id)

        # Modo "history-derived": el caller ya persistió los mensajes vía
        # ``record_user_message``. La query para embedding/routing se deriva del
        # trailing batch de role=user del historial.
        if user_input is not None:
            query: str = user_input
        else:
            query = _extract_trailing_user_batch(history)
            if not query:
                logger.warning(
                    "execute() llamado sin user_input pero el historial no tiene "
                    "trailing role=user (agent=%s, channel=%s, chat_id=%s) — abortando turno",
                    agent_id,
                    channel,
                    chat_id,
                )
                return ""

        digest_text = self._read_digest()
        all_skills = await self._skills.list_all()
        all_schemas = self._tools.get_schemas()
        skills_routing_active = len(all_skills) > self._cfg.skills.semantic_routing_min_skills
        tools_routing_active = (
            tools_override is None and len(all_schemas) > self._cfg.tools.semantic_routing_min_tools
        )
        retrieved_skills: list[Skill] = all_skills
        tool_schemas: list[dict] = tools_override if tools_override is not None else all_schemas

        prev_state = await self._history.load_state(agent_id)
        new_sticky_skills = dict(prev_state.sticky_skills)
        new_sticky_tools = dict(prev_state.sticky_tools)
        state_dirty = False

        routing_bypass = _should_bypass_routing_for_short_input(
            user_input=query,
            min_words_threshold=self._cfg.semantic_routing.min_words_threshold,
            prev_state=prev_state,
        )

        # query_vec se calcula como máximo una vez por turno y se reutiliza
        # tanto para semantic routing como para knowledge pre-fetch.
        query_vec: list[float] | None = None

        if routing_bypass:
            # Input corto con selección sticky previa → heredar intacta.
            # No se calcula embedding, no se toca el TTL, no se persiste estado.
            if skills_routing_active and prev_state.sticky_skills:
                skills_by_id = {s.id: s for s in all_skills}
                retrieved_skills = [
                    skills_by_id[i] for i in prev_state.sticky_skills if i in skills_by_id
                ]
            if tools_routing_active and prev_state.sticky_tools:
                schemas_by_name = {sch["function"]["name"]: sch for sch in all_schemas}
                tool_schemas = [
                    schemas_by_name[n] for n in prev_state.sticky_tools if n in schemas_by_name
                ]
            logger.info(
                "[routing] short-input bypass (agent=%s words=%d threshold=%d sticky_skills=%d sticky_tools=%d)",
                agent_id,
                len(query.split()),
                self._cfg.semantic_routing.min_words_threshold,
                len(prev_state.sticky_skills),
                len(prev_state.sticky_tools),
            )
        elif skills_routing_active or tools_routing_active:
            query_vec = await self._embedder.embed_query(query)
            if skills_routing_active:
                routing_skills = await self._skills.retrieve(
                    query_vec,
                    top_k=self._cfg.skills.semantic_routing_top_k,
                    min_score=self._cfg.skills.semantic_routing_min_score,
                )
                routing_ids = {s.id for s in routing_skills}
                active_ids, new_sticky_skills = apply_sticky(
                    routing_ids, prev_state.sticky_skills, self._cfg.skills.sticky_ttl
                )
                skills_by_id = {s.id: s for s in all_skills}
                retrieved_skills = [skills_by_id[i] for i in active_ids if i in skills_by_id]
                state_dirty = True
            if tools_routing_active:
                routing_schemas = await self._tools.get_schemas_relevant(
                    query_vec,
                    top_k=self._cfg.tools.semantic_routing_top_k,
                    min_score=self._cfg.tools.semantic_routing_min_score,
                )
                routing_names = {sch["function"]["name"] for sch in routing_schemas}
                active_names, new_sticky_tools = apply_sticky(
                    routing_names, prev_state.sticky_tools, self._cfg.tools.sticky_ttl
                )
                schemas_by_name = {sch["function"]["name"]: sch for sch in all_schemas}
                tool_schemas = [schemas_by_name[n] for n in active_names if n in schemas_by_name]
                state_dirty = True

        # Pre-fetch de knowledge: se ejecuta post-routing, reutilizando query_vec si ya fue
        # calculado. Se saltea si el bypass está activo (misma condición que routing bypass).
        from core.domain.value_objects.knowledge_chunk import KnowledgeChunk

        knowledge_chunks: list[KnowledgeChunk] = []
        if (
            not routing_bypass
            and self._knowledge_orchestrator is not None
            and self._knowledge_orchestrator.pre_fetch_enabled
        ):
            if query_vec is None:
                # Routing no corrió pero hay orquestrador → calcular embedding ahora
                query_vec = await self._embedder.embed_query(query)
            knowledge_chunks = await self._knowledge_orchestrator.retrieve_all(
                query_vec=query_vec,
                top_k=self._knowledge_orchestrator.default_top_k_per_source,
                min_score=self._knowledge_orchestrator.default_min_score,
            )
            logger.debug(
                "[knowledge] pre-fetch completado (agent=%s chunks=%d)",
                agent_id,
                len(knowledge_chunks),
            )

        # Verificación de presupuesto de tokens (heurística: len(texto) / 4).
        # El threshold se almacena en el orquestrador para evitar pasar GlobalConfig aquí.
        if self._knowledge_orchestrator is not None:
            threshold = self._knowledge_orchestrator.token_budget_threshold
            if threshold > 0:
                chunks_tokens = sum(len(c.content) // 4 for c in knowledge_chunks)
                digest_tokens = len(digest_text) // 4
                skills_tokens = sum(
                    len(getattr(s, "instructions", "") or "") // 4 for s in retrieved_skills
                )
                total_estimado = chunks_tokens + digest_tokens + skills_tokens

                if total_estimado > threshold:
                    logger.warning(
                        "[knowledge] presupuesto de tokens superado "
                        "(agent=%s total_estimado=%d threshold=%d "
                        "chunks_tokens=%d digest_tokens=%d skills_tokens=%d)",
                        agent_id,
                        total_estimado,
                        threshold,
                        chunks_tokens,
                        digest_tokens,
                        skills_tokens,
                    )

        user_context = self._read_user_context()
        context = AgentContext(
            agent_id=agent_id,
            user_context=user_context,
            memory_digest=digest_text,
            skills=retrieved_skills,
            timezone=self._user_timezone,
            workspace_root=_workspace_absolute_path(self._cfg),
            knowledge_chunks=knowledge_chunks,
        )
        system_prompt = context.build_system_prompt(
            self._cfg.system_prompt,
            extra_sections=extra_sections_snapshot or None,
        )

        user_msg: Message | None = None
        if user_input is not None:
            user_msg = Message(role=Role.USER, content=user_input)
            messages: list[Message] = history + [user_msg]
        else:
            # Los mensajes ya están en el historial individualmente. Coalescer
            # consecutivos mismo-rol para que el LLM reciba alternación limpia.
            messages = _coalesce_consecutive_same_role(history)

        if self._photo_debug_path:
            self._write_debug_phase2(
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

        try:
            response = await run_tool_loop(
                llm=self._llm,
                tools=self._tools,
                messages=messages,
                system_prompt=system_prompt,
                tool_schemas=tool_schemas,
                max_iterations=self._cfg.tools.tool_call_max_iterations,
                circuit_breaker_threshold=self._cfg.tools.circuit_breaker_threshold,
                agent_id=self._cfg.id,
                intermediate_sink=intermediate_sink,
            )
        except ToolLoopMaxIterationsError as e:
            response = e.last_response or (
                "No pude completar la tarea: se alcanzó el límite de "
                "iteraciones de tools sin obtener una respuesta final."
            )

        if not ephemeral:
            if state_dirty:
                await self._history.save_state(
                    agent_id,
                    ConversationState(
                        sticky_skills=new_sticky_skills,
                        sticky_tools=new_sticky_tools,
                    ),
                )
            # En modo history-derived (caller_provided_input=False) los mensajes
            # del usuario ya fueron persistidos vía record_user_message. Solo
            # falta la respuesta del assistant.
            if user_msg is not None:
                await self._history.append(agent_id, user_msg, channel=channel, chat_id=chat_id)
            await self._history.append(
                agent_id,
                Message(role=Role.ASSISTANT, content=response),
                channel=channel,
                chat_id=chat_id,
            )

        return response

    async def get_history(self) -> list[Message]:
        """Devuelve el historial activo del agente (sin archivados ni infused)."""
        return await self._history.load(self._cfg.id)

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
        await self._history.clear(self._cfg.id, channel=channel, chat_id=chat_id)

    @staticmethod
    def _write_debug_phase2(
        *,
        debug_path: str,
        user_input: str | None,
        channel: str,
        chat_id: str,
        history: list,
        messages: list,
        extra_sections: list[str],
        system_prompt: str,
    ) -> None:
        lines: list[str] = [
            "",
            "--- Fase 2: RunAgentUseCase.execute() ---",
            f"Timestamp: {datetime.now().isoformat()}",
            f"channel={channel!r}  chat_id={chat_id!r}",
            f"user_input (photo text_context): {user_input!r}",
            "",
            f"Historial cargado ({len(history)} mensajes para channel={channel!r}, chat_id={chat_id!r}):",
        ]
        for i, msg in enumerate(history, 1):
            content_preview = (msg.content or "")[:300].replace("\n", "\\n")
            lines.append(f"  [{i}] role={msg.role.value}  content={content_preview!r}")
        lines += [
            "",
            f"Mensajes enviados al LLM ({len(messages)} en total, historial + user_input):",
        ]
        for i, msg in enumerate(messages, 1):
            content_preview = (msg.content or "")[:300].replace("\n", "\\n")
            lines.append(f"  [{i}] role={msg.role.value}  content={content_preview!r}")
        lines += [
            "",
            f"Extra sections inyectadas ({len(extra_sections)}):",
        ]
        for i, sec in enumerate(extra_sections, 1):
            lines.append(f"  [{i}] {sec[:500]!r}")
        if not extra_sections:
            lines.append("  (ninguna)")
        lines += [
            "",
            "--- System Prompt ---",
            system_prompt,
            "--- Fin System Prompt ---",
            "",
        ]
        try:
            with open(debug_path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
            logger.debug("photo-debug Phase 2 escrito en %s", debug_path)
        except OSError as exc:
            logger.warning("No se pudo escribir photo-debug Phase 2: %s", exc)

    async def inspect(self, user_input: str) -> InspectResult:
        """
        Corre el pipeline RAG completo sin llamar al LLM ni persistir historial.
        Útil para debuggear qué ve el LLM en cada turno.
        """
        digest_text = self._read_digest()
        all_skills = await self._skills.list_all()
        all_schemas = self._tools.get_schemas()
        skills_routing_active = len(all_skills) > self._cfg.skills.semantic_routing_min_skills
        tools_routing_active = len(all_schemas) > self._cfg.tools.semantic_routing_min_tools
        retrieved_skills: list[Skill] = all_skills
        selected_schemas: list[dict] = all_schemas
        skill_scores: list[tuple[str, float]] = []
        tool_scores: list[tuple[str, float]] = []

        prev_state = await self._history.load_state(self._cfg.id)
        routing_bypass = _should_bypass_routing_for_short_input(
            user_input=user_input,
            min_words_threshold=self._cfg.semantic_routing.min_words_threshold,
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
                    top_k=self._cfg.skills.semantic_routing_top_k,
                    min_score=self._cfg.skills.semantic_routing_min_score,
                )
                retrieved_skills = [s for s, _ in scored_skills]
                skill_scores = [(s.id, sc) for s, sc in scored_skills]
            if tools_routing_active:
                scored_tools = await self._tools.get_schemas_relevant_with_scores(
                    query_vec,
                    top_k=self._cfg.tools.semantic_routing_top_k,
                    min_score=self._cfg.tools.semantic_routing_min_score,
                )
                selected_schemas = [sch for sch, _ in scored_tools]
                tool_scores = [(sch["function"]["name"], sc) for sch, sc in scored_tools]

        # Pre-fetch de knowledge — espeja execute(): skip en bypass o si pre-fetch está deshabilitado.
        from core.domain.value_objects.knowledge_chunk import KnowledgeChunk

        knowledge_chunks: list[KnowledgeChunk] = []
        if (
            not routing_bypass
            and self._knowledge_orchestrator is not None
            and self._knowledge_orchestrator.pre_fetch_enabled
        ):
            if query_vec is None:
                query_vec = await self._embedder.embed_query(user_input)
            knowledge_chunks = await self._knowledge_orchestrator.retrieve_all(
                query_vec=query_vec,
                top_k=self._knowledge_orchestrator.default_top_k_per_source,
                min_score=self._knowledge_orchestrator.default_min_score,
            )

        user_context = self._read_user_context()
        context = AgentContext(
            agent_id=self._cfg.id,
            user_context=user_context,
            memory_digest=digest_text,
            skills=retrieved_skills,
            timezone=self._user_timezone,
            workspace_root=_workspace_absolute_path(self._cfg),
            knowledge_chunks=knowledge_chunks,
        )
        system_prompt = context.build_system_prompt(self._cfg.system_prompt)

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
