"""Despacho de un turno a un agente del proceso, serializado por scope.

Implementa ``ILLMDispatcher`` (``core/ports/outbound/llm_dispatcher_port.py``). Lo
comparten el scheduler (trigger ``agent_send``) y la cola de delegación en
background: UNA instancia por proceso, así los dos comparten el dict de locks y
un ``bg-N`` y un trigger programado sobre la misma conversación no se intercalan.
"""

from __future__ import annotations

import asyncio

from inaki.kernel.ports.channel_port import IIntermediateSink


class LLMDispatcherAdapter:
    """Dispatcher que invoca ``agent.run_agent.execute`` serializando por scope.

    Cada combinación ``(agent_id, channel, chat_id)`` recibe un
    ``asyncio.Lock`` propio (lazy-init via ``setdefault``). El lock se toma SOLO
    alrededor de la llamada a ``execute`` — no incluye la resolución del agente
    ni la liberación tras el return. Esto garantiza que turnos concurrentes
    sobre la misma conversación (p. ej. un mensaje del usuario llegando a la
    vez que un bg-task termina) no se intercalen en el historial (REQ-BGD-6).

    El dict ``_locks`` crece sin bound — aceptable para uso doméstico en Pi 5
    donde la cantidad de scopes únicos es pequeña.
    """

    def __init__(self, agents: dict) -> None:
        self._agents = agents
        self._locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    def _get_lock(self, agent_id: str, channel: str, chat_id: str) -> asyncio.Lock:
        key = (agent_id, channel, chat_id)
        # ``setdefault`` evita la carrera del check-then-create cuando dos
        # corrutinas llaman dispatch al mismo scope antes de que ninguna haya
        # creado el lock.
        return self._locks.setdefault(key, asyncio.Lock())

    async def dispatch(
        self,
        agent_id: str,
        prompt: str | None = None,
        tools_override: list[dict] | None = None,
        intermediate_sink: IIntermediateSink | None = None,
        channel: str = "",
        chat_id: str = "",
        ephemeral: bool = False,
        skip_marker: str | None = None,
    ) -> str:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise ValueError(f"Agent '{agent_id}' not found")
        async with self._get_lock(agent_id, channel, chat_id):
            return await agent.run_agent.execute(
                prompt or "",
                tools_override=tools_override,
                intermediate_sink=intermediate_sink,
                channel=channel,
                chat_id=chat_id,
                ephemeral=ephemeral,
                skip_marker=skip_marker,
            )
