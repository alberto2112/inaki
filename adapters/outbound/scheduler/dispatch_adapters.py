from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from core.ports.outbound.llm_dispatcher_port import ILLMDispatcher
from core.use_cases.consolidate_all_agents import ConsolidateAllAgentsUseCase

if TYPE_CHECKING:
    from core.domain.entities.task import WebhookPayload


class ChannelSenderAdapter:
    """Routes channel_id prefix to the correct gateway."""

    def __init__(self, app_container: Any) -> None:
        self._container = app_container

    async def send_message(self, channel_id: str, text: str) -> None:
        # parse prefix: "telegram:<user_id>"
        prefix, _, target = channel_id.partition(":")
        if prefix == "telegram":
            await self._container.telegram_gateway.send_message(int(target), text)
        else:
            raise ValueError(f"Unknown channel prefix: {prefix}")


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
        return await agent.run_agent.run(prompt or "", tools_override=tools_override)


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
    channel_sender: ChannelSenderAdapter
    llm_dispatcher: ILLMDispatcher
    consolidator: ConsolidationDispatchAdapter
    http_caller: HttpCallerAdapter
