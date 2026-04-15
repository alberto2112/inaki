"""
RunAgentUseCase — orquesta un turno completo de conversación.

Flujo completo:
  1. Cargar historial del agente
  2. Leer digest markdown de memoria
  3. Listar skills y tools disponibles
  4. Si RAG activo: generar embedding y filtrar skills/tools relevantes
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
from core.domain.value_objects.agent_context import AgentContext
from core.domain.value_objects.agent_info import AgentInfoDTO
from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.history_port import IHistoryStore
from core.ports.outbound.llm_port import ILLMProvider
from core.ports.outbound.memory_port import IMemoryRepository
from core.ports.outbound.skill_port import ISkillRepository
from core.ports.outbound.tool_port import IToolExecutor
from core.use_cases._tool_loop import run_tool_loop
from infrastructure.config import AgentConfig

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
    skills_rag_active: bool
    selected_skill_scores: list[tuple[str, float]]
    all_tool_schemas: list[dict]
    selected_tool_schemas: list[dict]
    tools_rag_active: bool
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
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._embedder = embedder
        self._skills = skills
        self._history = history
        self._tools = tools
        self._cfg = agent_config
        # Extra sections injected by wire_delegation (task 6.1).
        # Empty by default — non-breaking when delegation is disabled.
        self._extra_system_sections: list[str] = []
        # Timezone del usuario para resolver {{TIMEZONE}}/{{DATETIME}}/etc. en el system prompt.
        # None → fallback a TZ local del sistema. Se puede inyectar via wire_user_timezone().
        self._user_timezone: str | None = None

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
        path = self._cfg.memory.digest_path  # already an expanded Path (validator)
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

    async def execute(self, user_input: str) -> str:
        agent_id = self._cfg.id

        history = await self._history.load(agent_id)
        digest_text = self._read_digest()
        all_skills = await self._skills.list_all()
        all_schemas = self._tools.get_schemas()
        skills_rag_active = len(all_skills) > self._cfg.skills.rag_min_skills
        tools_rag_active = len(all_schemas) > self._cfg.tools.rag_min_tools
        retrieved_skills: list[Skill] = all_skills
        tool_schemas: list[dict] = all_schemas

        if skills_rag_active or tools_rag_active:
            query_vec = await self._embedder.embed_query(user_input)
            if skills_rag_active:
                retrieved_skills = await self._skills.retrieve(
                    query_vec,
                    top_k=self._cfg.skills.rag_top_k,
                    min_score=self._cfg.skills.rag_min_score,
                )
            if tools_rag_active:
                tool_schemas = await self._tools.get_schemas_relevant(
                    query_vec,
                    top_k=self._cfg.tools.rag_top_k,
                    min_score=self._cfg.tools.rag_min_score,
                )

        user_context = self._read_user_context()
        context = AgentContext(agent_id=agent_id, user_context=user_context, memory_digest=digest_text, skills=retrieved_skills, timezone=self._user_timezone)
        system_prompt = context.build_system_prompt(
            self._cfg.system_prompt,
            extra_sections=self._extra_system_sections or None,
        )

        user_msg = Message(role=Role.USER, content=user_input)
        messages = history + [user_msg]

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
            )
        except ToolLoopMaxIterationsError as e:
            response = e.last_response

        await self._history.append(agent_id, user_msg)
        await self._history.append(agent_id, Message(role=Role.ASSISTANT, content=response))

        return response

    async def get_history(self) -> list[Message]:
        """Devuelve el historial activo del agente (sin archivados ni infused)."""
        return await self._history.load(self._cfg.id)

    async def clear_history(self) -> None:
        """Limpia el historial activo del agente."""
        await self._history.clear(self._cfg.id)

    async def inspect(self, user_input: str) -> InspectResult:
        """
        Corre el pipeline RAG completo sin llamar al LLM ni persistir historial.
        Útil para debuggear qué ve el LLM en cada turno.
        """
        digest_text = self._read_digest()
        all_skills = await self._skills.list_all()
        all_schemas = self._tools.get_schemas()
        skills_rag_active = len(all_skills) > self._cfg.skills.rag_min_skills
        tools_rag_active = len(all_schemas) > self._cfg.tools.rag_min_tools
        retrieved_skills: list[Skill] = all_skills
        selected_schemas: list[dict] = all_schemas
        skill_scores: list[tuple[str, float]] = []
        tool_scores: list[tuple[str, float]] = []

        if skills_rag_active or tools_rag_active:
            query_vec = await self._embedder.embed_query(user_input)
            if skills_rag_active:
                scored_skills = await self._skills.retrieve_with_scores(
                    query_vec,
                    top_k=self._cfg.skills.rag_top_k,
                    min_score=self._cfg.skills.rag_min_score,
                )
                retrieved_skills = [s for s, _ in scored_skills]
                skill_scores = [(s.id, sc) for s, sc in scored_skills]
            if tools_rag_active:
                scored_tools = await self._tools.get_schemas_relevant_with_scores(
                    query_vec,
                    top_k=self._cfg.tools.rag_top_k,
                    min_score=self._cfg.tools.rag_min_score,
                )
                selected_schemas = [sch for sch, _ in scored_tools]
                tool_scores = [
                    (sch["function"]["name"], sc) for sch, sc in scored_tools
                ]

        user_context = self._read_user_context()
        context = AgentContext(agent_id=self._cfg.id, user_context=user_context, memory_digest=digest_text, skills=retrieved_skills, timezone=self._user_timezone)
        system_prompt = context.build_system_prompt(self._cfg.system_prompt)

        return InspectResult(
            user_input=user_input,
            memory_digest=digest_text,
            all_skills=all_skills,
            selected_skills=retrieved_skills,
            skills_rag_active=skills_rag_active,
            selected_skill_scores=skill_scores,
            all_tool_schemas=all_schemas,
            selected_tool_schemas=selected_schemas,
            tools_rag_active=tools_rag_active,
            selected_tool_scores=tool_scores,
            system_prompt=system_prompt,
        )

