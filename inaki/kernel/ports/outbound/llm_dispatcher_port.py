from __future__ import annotations

from typing import Protocol

from inaki.kernel.ports.outbound.channel_port import IIntermediateSink


class ILLMDispatcher(Protocol):
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
    ) -> str: ...
