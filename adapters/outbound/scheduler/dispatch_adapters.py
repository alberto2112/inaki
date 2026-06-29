from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

from core.domain.entities.message import Message, Role
from core.domain.value_objects.dispatch_result import DispatchResult
from core.ports.outbound.intermediate_sink_port import IIntermediateSink
from core.ports.outbound.outbound_sink_port import IOutboundSink
from core.use_cases.consolidate_all_agents import ConsolidateAllAgentsUseCase
from core.use_cases.reconcile_memory import ReconcileMemoryUseCase

if TYPE_CHECKING:
    from core.domain.entities.task import ShellExecPayload, WebhookPayload


# El fallback de último recurso (file://...) lo INYECTA el composition root ya resuelto
# contra el home de instancia (``SchedulerConfig.fallback_log_filename``) por privacidad
# — bajo ``<home>/data/`` (no /tmp world-readable). El default del adapter (/tmp) es solo
# para construcción directa / tests; producción siempre lo sobreescribe.


@dataclass(frozen=True)
class ChannelFallbackSettings:
    """Settings VO del router — el container lo mapea desde ``ChannelFallbackConfig``.

    Atributos:
        default: Target string usado cuando no hay override específico.
            ``None`` delega al fallback hardcoded.
        overrides: Mapa ``channel_type → target string`` para redirigir
            canales concretos.
    """

    default: str | None = None
    overrides: dict[str, str] = field(default_factory=dict)


class ChannelRouter:
    """Resuelve un ``target`` contra una cascada de sinks y delega el envío.

    Cascada (ordenada):
      1. Sink nativo registrado para el prefix del target.
      2. ``fallback_config.overrides[channel_type]`` → fabrica sink vía factory.
      3. ``fallback_config.default`` → fabrica sink vía factory.
      4. ``hardcoded_fallback`` (``file:///tmp/inaki-schedule-output.log``
         por defecto) → fabrica sink vía factory. Nunca falla por canal.

    El ``DispatchResult`` siempre preserva el ``original_target`` tal como
    llegó al router, aunque el sink delegado devuelva otra cosa.
    """

    def __init__(
        self,
        native_sinks: dict[str, IOutboundSink],
        fallback_config: ChannelFallbackSettings,
        sink_factory: Callable[[str], IOutboundSink],
        hardcoded_fallback: str = "file:///tmp/inaki-schedule-output.log",
    ) -> None:
        self._native = native_sinks
        self._config = fallback_config
        self._factory = sink_factory
        self._hardcoded = hardcoded_fallback

    def build_intermediate_sink(self, target: str) -> IIntermediateSink:
        """Fabrica un ``IIntermediateSink`` que emite cada texto vía ``send_message``
        hacia ``target``. Lo usa el scheduler para propagar intermedios en vivo
        al canal destino durante un ``agent_send``.
        """
        # Import local para evitar el ciclo adapters→adapters al cargar.
        from adapters.outbound.intermediate_sinks.channel_router import (
            ChannelRouterIntermediateSink,
        )

        return ChannelRouterIntermediateSink(router=self, target=target)

    async def send_message(self, target: str, text: str) -> DispatchResult:
        prefix, sep, _ = target.partition(":")
        if not sep:
            raise ValueError(f"Target sin prefix: '{target}'")

        resolved_target = target
        sink: IOutboundSink
        if prefix in self._native:
            sink = self._native[prefix]
        elif prefix in self._config.overrides:
            resolved_target = self._config.overrides[prefix]
            sink = self._factory(resolved_target)
        elif self._config.default is not None:
            resolved_target = self._config.default
            sink = self._factory(resolved_target)
        else:
            resolved_target = self._hardcoded
            sink = self._factory(resolved_target)

        result = await sink.send(resolved_target, text)
        # Preserva SIEMPRE el target original que llegó al router, ignorando
        # lo que el sink haya puesto en su DispatchResult.
        return DispatchResult(
            original_target=target,
            resolved_target=result.resolved_target,
        )


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


class ChannelHistoryRecorderAdapter:
    """Persiste un ``channel_send`` como mensaje del asistente en el historial
    del agente dueño de la conversación (``payload.agent_id`` si se informó, o
    ``task.created_by`` en su defecto — la resolución del dueño la hace el
    ``SchedulerService``; acá llega ya resuelto en ``agent_id``).

    Sigue el patrón de ``LLMDispatcherAdapter``: recibe el dict de agentes
    (duck-typed — ``adapters`` no importa ``infrastructure``) y resuelve el
    historial por ``agent_id`` en runtime accediendo a ``agent.history``.

    Persiste SOLO cuando el ``resolved_target`` apunta a un canal conversacional
    vivo (su prefijo está en ``conversational_channels`` — los sinks nativos del
    router). Si el mensaje cayó a un fallback no-conversacional (archivo) o el
    agente no existe, es no-op: no hay conversación que registrar.
    """

    def __init__(self, agents: dict, conversational_channels: set[str]) -> None:
        self._agents = agents
        self._conversational = conversational_channels

    async def record_channel_send(
        self, agent_id: str, resolved_target: str, text: str
    ) -> None:
        channel, sep, chat_id = resolved_target.partition(":")
        if not sep or channel not in self._conversational:
            # No es canal conversacional (ej: file:///... del fallback) → el
            # usuario nunca vio esto en una conversación: nada que persistir.
            return
        agent = self._agents.get(agent_id)
        if agent is None:
            # Agente desconocido (renombrado/eliminado tras crear la tarea).
            return
        await agent.history.append(
            agent_id,
            Message(role=Role.ASSISTANT, content=text),
            channel=channel,
            chat_id=chat_id,
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
