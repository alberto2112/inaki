from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.ports.outbound.llm_dispatcher_port import ILLMDispatcher


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


@dataclass
class SchedulerDispatchPorts:
    channel_sender: ChannelSenderAdapter
    llm_dispatcher: ILLMDispatcher
