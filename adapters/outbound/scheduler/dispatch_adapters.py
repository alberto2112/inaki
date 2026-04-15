from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from core.domain.value_objects.dispatch_result import DispatchResult
from core.ports.outbound.llm_dispatcher_port import ILLMDispatcher
from core.ports.outbound.outbound_sink_port import IOutboundSink
from core.use_cases.consolidate_all_agents import ConsolidateAllAgentsUseCase

if TYPE_CHECKING:
    from core.domain.entities.task import WebhookPayload
    from infrastructure.config import ChannelFallbackConfig


_HARDCODED_FALLBACK = "file:///tmp/inaki-schedule-output.log"


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
        fallback_config: ChannelFallbackConfig,
        sink_factory: Callable[[str], IOutboundSink],
        hardcoded_fallback: str = _HARDCODED_FALLBACK,
    ) -> None:
        self._native = native_sinks
        self._config = fallback_config
        self._factory = sink_factory
        self._hardcoded = hardcoded_fallback

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
    def __init__(self, agents: dict) -> None:
        self._agents = agents

    async def dispatch(
        self,
        agent_id: str,
        prompt: str | None = None,
        tools_override: list[dict] | None = None,
    ) -> str:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise ValueError(f"Agent '{agent_id}' not found")
        return await agent.run_agent.execute(prompt or "", tools_override=tools_override)


class ConsolidationDispatchAdapter:
    """Thin wrapper so the scheduler service doesn't import the use case directly."""

    def __init__(self, use_case: ConsolidateAllAgentsUseCase) -> None:
        self._uc = use_case

    async def consolidate_all(self) -> str:
        return await self._uc.execute()


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
                raise RuntimeError(
                    f"Webhook returned non-success status {response.status_code}"
                )
            return response.text


@dataclass
class SchedulerDispatchPorts:
    channel_sender: ChannelRouter
    llm_dispatcher: ILLMDispatcher
    consolidator: ConsolidationDispatchAdapter
    http_caller: HttpCallerAdapter
