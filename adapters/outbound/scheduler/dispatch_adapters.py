from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from typing import TYPE_CHECKING

import httpx

from core.ports.outbound.channel_port import IIntermediateSink
from core.use_cases.consolidate_all_agents import ConsolidateAllAgentsUseCase
from core.use_cases.reconcile_memory import ReconcileMemoryUseCase

if TYPE_CHECKING:
    from core.domain.entities.task import ShellExecPayload, WebhookPayload


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


class ConsolidationDispatchAdapter:
    """Thin wrapper so the scheduler service doesn't import the use case directly."""

    def __init__(self, use_case: ConsolidateAllAgentsUseCase) -> None:
        self._uc = use_case

    async def consolidate_all(self) -> str:
        return await self._uc.execute()


class ReconcileDispatchAdapter:
    """Thin wrapper que expone ``reconcile(agent_id)`` al ``SchedulerService``.

    Resuelve la instancia de ``ReconcileMemoryUseCase`` del agente en runtime
    desde el dict de use cases registrados por ``AppContainer``. Si el agente
    no existe o no tiene reconciliación habilitada, lanza ``ValueError`` (el
    scheduler lo captura como fallo del trigger y aplica backoff + log).
    """

    def __init__(self, reconcilers: dict[str, ReconcileMemoryUseCase]) -> None:
        self._reconcilers = reconcilers

    async def reconcile(self, agent_id: str) -> str:
        uc = self._reconcilers.get(agent_id)
        if uc is None:
            raise ValueError(
                f"reconcile_memory: no hay ReconcileMemoryUseCase para el agente '{agent_id}'. "
                "Verificá que memories.reconciliation.enabled=True en su config."
            )
        return await uc.execute()


class ShellExecAdapter:
    """Ejecuta triggers shell_exec como subprocess con timeout duro.

    Satisface ``IShellExecutor``. Al expirar el timeout, el proceso se MATA
    (kill + reap) — sin esto quedaba corriendo huérfano y cada retry lanzaba
    otro encima.
    """

    async def run(self, payload: ShellExecPayload) -> str:
        proc = await asyncio.create_subprocess_shell(
            payload.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=payload.working_dir,
            env={**os.environ, **(payload.env_vars or {})},
        )
        timeout = payload.timeout or 300
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            with suppress(Exception):
                await proc.communicate()  # reap — evita zombie
            raise RuntimeError(
                f"shell_exec excedió el timeout de {timeout}s — proceso terminado"
            ) from None
        if proc.returncode != 0:
            raise RuntimeError(f"shell_exec exited with code {proc.returncode}")
        return stdout.decode(errors="replace")


class HttpCallerAdapter:
    """Performs HTTP calls for webhook triggers."""

    async def call(self, payload: WebhookPayload) -> str:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.request(
                    method=payload.method,
                    url=payload.url,
                    headers=payload.headers,
                    content=payload.body,
                    timeout=payload.timeout,
                )
            except httpx.TimeoutException as exc:
                raise RuntimeError(f"Webhook timed out: {exc}") from exc
            except httpx.ConnectError as exc:
                raise RuntimeError(f"Webhook connection failed: {exc}") from exc
            if response.status_code not in payload.success_codes:
                raise RuntimeError(f"Webhook returned non-success status {response.status_code}")
            return response.text
