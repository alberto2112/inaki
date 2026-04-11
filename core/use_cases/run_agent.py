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

import json
import logging
from dataclasses import dataclass, field

from core.domain.entities.message import Message, Role
from core.domain.entities.skill import Skill
from core.domain.value_objects.agent_context import AgentContext
from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.history_port import IHistoryStore
from core.ports.outbound.llm_port import ILLMProvider
from core.ports.outbound.memory_port import IMemoryRepository
from core.ports.outbound.skill_port import ISkillRepository
from core.ports.outbound.tool_port import IToolExecutor
from infrastructure.config import AgentConfig

logger = logging.getLogger(__name__)

@dataclass
class InspectResult:
    user_input: str
    memory_digest: str
    all_skills: list[Skill]
    selected_skills: list[Skill]
    skills_rag_active: bool
    all_tool_schemas: list[dict]
    selected_tool_schemas: list[dict]
    tools_rag_active: bool
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
                retrieved_skills = await self._skills.retrieve(query_vec, top_k=self._cfg.skills.rag_top_k)
            if tools_rag_active:
                tool_schemas = await self._tools.get_schemas_relevant(query_vec, top_k=self._cfg.tools.rag_top_k)

        context = AgentContext(agent_id=agent_id, memory_digest=digest_text, skills=retrieved_skills)
        system_prompt = context.build_system_prompt(self._cfg.system_prompt)

        user_msg = Message(role=Role.USER, content=user_input)
        messages = history + [user_msg]

        response = await self._run_with_tools(messages, system_prompt, tool_schemas)

        await self._history.append(agent_id, user_msg)
        await self._history.append(agent_id, Message(role=Role.ASSISTANT, content=response))

        return response

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

        if skills_rag_active or tools_rag_active:
            query_vec = await self._embedder.embed_query(user_input)
            if skills_rag_active:
                retrieved_skills = await self._skills.retrieve(query_vec, top_k=self._cfg.skills.rag_top_k)
            if tools_rag_active:
                selected_schemas = await self._tools.get_schemas_relevant(query_vec, top_k=self._cfg.tools.rag_top_k)

        context = AgentContext(agent_id=self._cfg.id, memory_digest=digest_text, skills=retrieved_skills)
        system_prompt = context.build_system_prompt(self._cfg.system_prompt)

        return InspectResult(
            user_input=user_input,
            memory_digest=digest_text,
            all_skills=all_skills,
            selected_skills=retrieved_skills,
            skills_rag_active=skills_rag_active,
            all_tool_schemas=all_schemas,
            selected_tool_schemas=selected_schemas,
            tools_rag_active=tools_rag_active,
            system_prompt=system_prompt,
        )

    async def _run_with_tools(
        self,
        messages: list[Message],
        system_prompt: str,
        tool_schemas: list[dict],
    ) -> str:
        """
        Loop de tool calls: llama al LLM, ejecuta tools si las pide,
        añade los resultados y relama hasta obtener respuesta final o
        alcanzar el máximo de iteraciones.

        Incluye un circuit breaker por tool dentro del mismo turno: si una tool
        falla `circuit_breaker_threshold` veces, sucesivas llamadas a esa tool
        retornan inmediatamente sin ejecutarse y se le informa al LLM que corte
        los reintentos.
        """
        working_messages = list(messages)
        failure_counts: dict[str, int] = {}
        tripped: set[str] = set()
        threshold = self._cfg.tools.circuit_breaker_threshold

        for iteration in range(self._cfg.tools.tool_call_max_iterations):
            raw = await self._llm.complete(
                working_messages,
                system_prompt,
                tools=tool_schemas if tool_schemas else None,
            )

            tool_calls = self._extract_tool_calls(raw)
            if not tool_calls:
                return raw

            tool_results = []
            for tc in tool_calls:
                tool_name = tc.get("function", {}).get("name", "")
                args_raw = tc.get("function", {}).get("arguments", "{}")
                try:
                    kwargs = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except json.JSONDecodeError:
                    kwargs = {}

                if tool_name in tripped:
                    logger.warning(
                        "Circuit breaker abierto para '%s' — llamada bloqueada", tool_name
                    )
                    tool_results.append(
                        f"[{tool_name}]: CIRCUIT OPEN — esta tool ya falló "
                        f"{threshold} vez/veces en este turno. NO la vuelvas a llamar. "
                        "Respondé al usuario con lo que sabés, o pedile ayuda para resolver el bloqueo."
                    )
                    continue

                result = await self._tools.execute(tool_name, **kwargs)
                tool_results.append(f"[{tool_name}]: {result.output}")
                logger.debug("Tool '%s' ejecutada: success=%s", tool_name, result.success)

                if result.success:
                    failure_counts[tool_name] = 0
                else:
                    failure_counts[tool_name] = failure_counts.get(tool_name, 0) + 1
                    if failure_counts[tool_name] >= threshold:
                        tripped.add(tool_name)
                        logger.warning(
                            "Circuit breaker DISPARADO para '%s' tras %d fallos",
                            tool_name,
                            failure_counts[tool_name],
                        )

            results_summary = "\n".join(tool_results)
            working_messages.append(
                Message(role=Role.USER, content=f"[Resultados de tools]\n{results_summary}")
            )

        logger.warning("Máximo de iteraciones de tool calls alcanzado para '%s'", self._cfg.id)
        return raw

    def _extract_tool_calls(self, raw: str) -> list[dict]:
        """Extrae tool calls del JSON devuelto por el LLM."""
        if not raw.strip().startswith("{"):
            return []
        try:
            data = json.loads(raw)
            return data.get("tool_calls", [])
        except json.JSONDecodeError:
            return []
