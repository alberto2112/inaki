"""
Fases del turno conversacional — helpers compartidos de ``RunAgentUseCase``.

Mismo contrato que ``_tool_loop.py``: funciones libres con dependencias
explícitas (ports, settings, VOs) — nada de ``self``. ``_execute_turn``
queda como orquestador que las encadena; cada fase es testeable aislada.

Fases:
  - ``run_semantic_routing`` — selección RAG de skills/tools + sticky TTL,
    con bypass para inputs cortos que heredan la selección previa.
  - ``prefetch_knowledge`` — retrieval de knowledge chunks pre-turno
    (compartida por ``execute()`` e ``inspect()``).
  - ``warn_if_token_budget_exceeded`` — heurística de presupuesto de tokens.
  - ``assemble_turn_messages`` — arma la lista de mensajes del LLM según el
    modo del turno (user_input directo vs history-derived coalesced).

También viven acá los helpers puros del turno (trailing batch, coalesce,
secciones in-flight del system prompt, debug de foto).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from core.domain.entities.background_task import BackgroundTaskView
from core.domain.entities.message import Message, Role
from core.domain.entities.skill import Skill
from core.domain.services.knowledge_orchestrator import KnowledgeOrchestrator
from core.domain.services.prepend_timestamps import prepend_timestamps
from core.domain.services.sticky_selector import apply_sticky
from core.domain.value_objects.agent_settings import RunAgentSettings
from core.domain.value_objects.conversation_state import ConversationState
from core.domain.value_objects.knowledge_chunk import KnowledgeChunk
from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.skill_port import ISkillRepository
from core.ports.outbound.tool_port import IToolExecutor

logger = logging.getLogger(__name__)


def extract_trailing_user_batch(history: list[Message]) -> str:
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


def coalesce_consecutive_same_role(messages: list[Message]) -> list[Message]:
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


# Texto que se inyecta como sección del system prompt para que el LLM sepa
# interpretar mensajes role=user que aparezcan EN MEDIO de un tool loop como
# aclaraciones/correcciones/aborts del trabajo en curso (feature
# in-flight-message-injection). En INGLÉS por convención del proyecto
# (system-prompts-language).
INFLIGHT_CLARIFICATIONS_SECTION = (
    "## In-flight user clarifications\n\n"
    "While you are working through tool calls, the user may send additional "
    "messages on the same conversation. These messages will appear as new "
    "`role=user` entries interleaved with your tool results — they are NOT a "
    "new conversation turn.\n\n"
    "Treat them as clarifications, corrections, additional constraints, or "
    "abort signals for the work you are currently doing. Incorporate them into "
    "the in-progress task without restarting completed steps when possible. "
    "If the user clearly asks you to stop or change direction, abandon the "
    "current branch and follow the new instruction. Respond once when the "
    "combined task is complete — do not send a separate reply per injected "
    "message."
)


def render_in_flight_section(snap: list[BackgroundTaskView]) -> str:
    """Construye la sección del system prompt que lista delegaciones in-flight.

    Texto en INGLÉS por convención del proyecto: todo lo que va al LLM va en
    inglés, aunque el resto del codebase esté en español
    (``convention/system-prompts-language``). El prompt_preview de cada bullet
    se preserva verbatim (puede venir en español del agente padre).

    Pure function: no side effects, deterministic por input.
    """
    bullets = "\n".join(
        f'- {v.id} → {v.target_agent_id} | status: {v.status} | started {v.elapsed_seconds}s ago | "{v.prompt_preview}"'
        for v in snap
    )
    return (
        "## In-flight background delegations\n\n"
        "You have one or more delegations launched via `delegate(... wait=false)` running in\n"
        "the background. When they finish, you will receive a message starting with `[bg-N] ...`.\n"
        "That message is NOT user input — it is the result of YOUR own delegation. Process it\n"
        "directly: no greetings, no preambles. A `[bg-N] failed: ...` message means that\n"
        "delegation errored — report the failure to the user, do NOT keep waiting for it.\n\n"
        "If the user asks how a delegation is going, ANSWER FROM THE LIST BELOW — it is your\n"
        "live source of truth. State the task_id, its status, and how long it has been running.\n"
        "`queued` = waiting for a free slot, `running` = the child agent is working now. Never\n"
        "say you don't know: the data is right here. A delegation that finished (success or\n"
        "failure) is NOT in this list — its result already arrived as a `[bg-N] ...` message,\n"
        "so check the conversation for it instead.\n\n"
        "Do NOT re-launch a task that is already `queued` or `running` below — that would\n"
        "duplicate the work. Wait for its `[bg-N]` result first.\n\n"
        "Currently in flight:\n"
        f"{bullets}"
    )


def should_bypass_routing_for_short_input(
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


@dataclass(frozen=True)
class RoutingOutcome:
    """Resultado de la fase de semantic routing de un turno.

    ``new_sticky_skills``/``new_sticky_tools`` solo difieren del estado previo
    cuando ``state_dirty`` es ``True`` (el routing corrió y tocó TTLs) — el
    caller decide si persistirlos. ``query_vec`` se expone para que la fase de
    knowledge pre-fetch lo reutilice sin re-embeddear.
    """

    retrieved_skills: list[Skill]
    tool_schemas: list[dict]
    new_sticky_skills: dict[str, int]
    new_sticky_tools: dict[str, int]
    state_dirty: bool
    query_vec: list[float] | None
    routing_bypass: bool


async def run_semantic_routing(
    *,
    query: str,
    tools_override: list[dict] | None,
    prev_state: ConversationState,
    settings: RunAgentSettings,
    embedder: IEmbeddingProvider,
    skills: ISkillRepository,
    tools: IToolExecutor,
) -> RoutingOutcome:
    """Selecciona skills y tool schemas para el turno (RAG + sticky TTL).

    Tres caminos:
      - routing inactivo (pocos skills/tools) → pasa todo sin filtrar.
      - bypass por input corto → hereda la selección sticky previa intacta,
        sin calcular embedding ni tocar TTLs.
      - routing activo → embed de la query, retrieve top-k, ``apply_sticky``
        sobre la selección y marca ``state_dirty`` para que el caller persista.

    ``tools_override`` fuerza ese set de schemas y desactiva el routing de
    tools (el de skills sigue corriendo) — usado por triggers del scheduler.
    """
    agent_id = settings.agent_id
    all_skills = await skills.list_all()
    all_schemas = tools.get_schemas()
    skills_routing_active = len(all_skills) > settings.skills_min_skills
    tools_routing_active = tools_override is None and len(all_schemas) > settings.tools_min_tools
    retrieved_skills: list[Skill] = all_skills
    tool_schemas: list[dict] = tools_override if tools_override is not None else all_schemas

    new_sticky_skills = dict(prev_state.sticky_skills)
    new_sticky_tools = dict(prev_state.sticky_tools)
    state_dirty = False

    # query_vec se calcula como máximo una vez por turno y se reutiliza
    # tanto para semantic routing como para knowledge pre-fetch.
    query_vec: list[float] | None = None

    routing_bypass = should_bypass_routing_for_short_input(
        user_input=query,
        min_words_threshold=settings.min_words_threshold,
        prev_state=prev_state,
    )

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
            settings.min_words_threshold,
            len(prev_state.sticky_skills),
            len(prev_state.sticky_tools),
        )
    elif skills_routing_active or tools_routing_active:
        query_vec = await embedder.embed_query(query)
        if skills_routing_active:
            routing_skills = await skills.retrieve(
                query_vec,
                top_k=settings.skills_top_k,
                min_score=settings.skills_min_score,
            )
            routing_ids = {s.id for s in routing_skills}
            active_ids, new_sticky_skills = apply_sticky(
                routing_ids, prev_state.sticky_skills, settings.skills_sticky_ttl
            )
            skills_by_id = {s.id: s for s in all_skills}
            retrieved_skills = [skills_by_id[i] for i in active_ids if i in skills_by_id]
            state_dirty = True
        if tools_routing_active:
            routing_schemas = await tools.get_schemas_relevant(
                query_vec,
                top_k=settings.tools_top_k,
                min_score=settings.tools_min_score,
            )
            routing_names = {sch["function"]["name"] for sch in routing_schemas}
            active_names, new_sticky_tools = apply_sticky(
                routing_names, prev_state.sticky_tools, settings.tools_sticky_ttl
            )
            schemas_by_name = {sch["function"]["name"]: sch for sch in all_schemas}
            tool_schemas = [schemas_by_name[n] for n in active_names if n in schemas_by_name]
            state_dirty = True

    return RoutingOutcome(
        retrieved_skills=retrieved_skills,
        tool_schemas=tool_schemas,
        new_sticky_skills=new_sticky_skills,
        new_sticky_tools=new_sticky_tools,
        state_dirty=state_dirty,
        query_vec=query_vec,
        routing_bypass=routing_bypass,
    )


async def prefetch_knowledge(
    *,
    routing_bypass: bool,
    orchestrator: KnowledgeOrchestrator | None,
    embedder: IEmbeddingProvider,
    query: str,
    query_vec: list[float] | None,
    agent_id: str,
) -> tuple[list[KnowledgeChunk], list[float] | None]:
    """Pre-fetch de knowledge chunks para el turno (compartida por execute/inspect).

    Corre post-routing reutilizando ``query_vec`` si ya fue calculado; si el
    routing no corrió pero hay orquestrador, embeddea acá. Se saltea con el
    bypass activo (misma condición que el routing bypass) o sin orquestrador.

    Devuelve ``(chunks, query_vec)`` — el vec puede haberse calculado acá y el
    caller lo puede reutilizar.
    """
    if routing_bypass or orchestrator is None or not orchestrator.pre_fetch_enabled:
        return [], query_vec
    if query_vec is None:
        query_vec = await embedder.embed_query(query)
    chunks = await orchestrator.retrieve_all(
        query_vec=query_vec,
        top_k=orchestrator.default_top_k_per_source,
        min_score=orchestrator.default_min_score,
    )
    logger.debug(
        "[knowledge] pre-fetch completado (agent=%s chunks=%d)",
        agent_id,
        len(chunks),
    )
    return chunks, query_vec


def warn_if_token_budget_exceeded(
    *,
    orchestrator: KnowledgeOrchestrator | None,
    knowledge_chunks: list[KnowledgeChunk],
    digest_text: str,
    retrieved_skills: list[Skill],
    agent_id: str,
) -> None:
    """Verificación de presupuesto de tokens (heurística: len(texto) / 4).

    El threshold se almacena en el orquestrador para evitar pasar GlobalConfig.
    Solo loguea WARNING — no recorta nada (decisión V1: visibilidad sin poda).
    """
    if orchestrator is None:
        return
    threshold = orchestrator.token_budget_threshold
    if threshold <= 0:
        return
    chunks_tokens = sum(len(c.content) // 4 for c in knowledge_chunks)
    digest_tokens = len(digest_text) // 4
    skills_tokens = sum(len(getattr(s, "instructions", "") or "") // 4 for s in retrieved_skills)
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


def assemble_turn_messages(
    *,
    history: list[Message],
    user_input: str | None,
    channel: str,
    timestamp_channels: frozenset[str],
) -> tuple[Message | None, list[Message]]:
    """Arma ``(user_msg, messages)`` para el LLM según el modo del turno.

    Con ``user_input`` el mensaje del turno se appendea al historial con
    timestamp seteado acá (no al persistir) para que ``prepend_timestamps``
    también lo alcance. En modo history-derived (``user_input=None``) los
    mensajes ya están en el historial individualmente — se coalescen los
    consecutivos mismo-rol para que el LLM reciba alternación limpia, y
    ``user_msg`` es ``None`` (nada nuevo que persistir).
    """
    user_msg: Message | None = None
    if user_input is not None:
        user_msg = Message(
            role=Role.USER,
            content=user_input,
            timestamp=datetime.now(timezone.utc),
        )
        messages: list[Message] = history + [user_msg]
    else:
        messages = coalesce_consecutive_same_role(history)

    if channel and channel in timestamp_channels:
        messages = prepend_timestamps(messages)

    return user_msg, messages


def write_debug_phase2(
    *,
    debug_path: str,
    user_input: str | None,
    channel: str,
    chat_id: str,
    history: list[Message],
    messages: list[Message],
    extra_sections: list[str],
    system_prompt: str,
) -> None:
    """Escribe la Fase 2 del archivo de debug de foto (historial + prompt + mensajes)."""
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
